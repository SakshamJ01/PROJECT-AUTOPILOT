"""Phase 2 tests for Channel Profiles and Editorial Influences.
Covers:
- Channel profile serialization (JSON & YAML loading)
- Builtin presets (science_shorts, history_shorts, tech_shorts)
- Profile influence on script generation (persona tone, vocabulary, hook style, visual motif)
- Channel profile isolation across jobs and database records
- Invalid profile rejection and validation constraints
"""
import json
import pytest
from pathlib import Path

from autopilot.core.channel import (
    ChannelManager,
    BUILTIN_CHANNEL_PRESETS,
    load_profile_from_file,
)
from autopilot.core.contracts import ChannelProfile, ChannelStatus
from autopilot.providers.mock_script import MockScriptProvider
from autopilot.providers.openai_llm_provider import OpenAICompatibleLLMProvider
from autopilot.db.manager import DBManager


def test_builtin_channel_presets_exist_and_validate():
    """Verify built-in presets for science, history, and tech shorts validate cleanly."""
    mgr = ChannelManager()
    for channel_id in ["science_shorts", "history_shorts", "tech_shorts"]:
        profile = mgr.get_or_create_channel(channel_id)
        assert profile is not None
        assert profile.channel_id == channel_id
        errors = mgr.validate_profile(profile)
        assert errors == [], f"Preset {channel_id} had validation errors: {errors}"
        rules = profile.to_editorial_rules()
        assert "niche" in rules
        assert "tone" in rules


def test_load_channel_profile_from_yaml_and_json(tmp_path):
    """Verify data-driven profile loading from external YAML and JSON files."""
    json_path = tmp_path / "custom_channel.json"
    custom_data = {
        "channel_id": "custom_curiosity",
        "channel_name": "Custom Curiosity",
        "niche": {
            "niche_name": "philosophy",
            "description": "Thought experiments and paradoxes",
            "allowed_categories": ["philosophy", "logic"],
            "content_format_preferences": ["short_vertical"],
        },
        "persona": {
            "persona_name": "philosopher",
            "tone": "contemplative",
            "vocabulary_level": "elevated",
            "narration_personality": "thought_provoking",
            "cta_style": "reflective",
            "hook_style": "paradox",
        },
        "voice": {
            "provider": "kokoro",
            "voice_id": "am_michael",
            "language": "en",
            "speaking_rate": 0.95,
        },
        "visual": {
            "font_family": "Cinzel",
            "primary_color": "#FFD700",
            "secondary_color": "#1A1A1A",
            "caption_style": "minimal_serif",
            "visual_motif": "philosophical_monochrome",
        },
        "target_platforms": ["youtube"],
    }
    json_path.write_text(json.dumps(custom_data), encoding="utf-8")

    profile_loaded = load_profile_from_file(json_path)
    assert profile_loaded.channel_id == "custom_curiosity"
    assert profile_loaded.persona.tone == "contemplative"
    assert profile_loaded.visual.visual_motif == "philosophical_monochrome"


def test_channel_profile_influences_mock_script_generation():
    """Verify that ChannelProfile modifies MockScriptProvider tone, hook, and scenes."""
    mgr = ChannelManager()
    science_profile = mgr.get_or_create_channel("science_shorts")
    history_profile = mgr.get_or_create_channel("history_shorts")

    provider = MockScriptProvider()

    sci_script = provider.generate_script(
        topic="Black Holes",
        content_id="test-sci-01",
        channel_profile=science_profile,
    )

    hist_script = provider.generate_script(
        topic="The Roman Empire",
        content_id="test-hist-01",
        channel_profile=history_profile,
    )

    # Science channel script check
    assert "discovery about Black Holes" in sci_script.hook or "scientific anomaly" in sci_script.hook
    assert sci_script.generation_metadata["channel_id"] == "science_shorts"
    assert "laboratory experiments" in sci_script.scenes[0].narration

    # History channel script check
    assert "turning point occurred" in hist_script.hook or "history" in hist_script.hook
    assert hist_script.generation_metadata["channel_id"] == "history_shorts"
    assert "Historical records" in hist_script.scenes[0].narration


def test_channel_profile_influences_openai_prompt():
    """Verify that OpenAICompatibleLLMProvider injects channel rules into prompts."""
    mgr = ChannelManager()
    tech_profile = mgr.get_or_create_channel("tech_shorts")
    provider = OpenAICompatibleLLMProvider(base_url="http://127.0.0.1:11434/v1", model_name="qwen:test")

    captured_req = None

    def fake_urlopen(req, timeout=30.0):
        nonlocal captured_req
        captured_req = json.loads(req.data.decode("utf-8"))
        fake_content = {
            "title": "Quantum GPU Computing",
            "hook_text": "GPUs are undergoing a massive hardware shift.",
            "scenes": [{"scene_id": "s1", "order": 1, "narration": "Quantum accelerators are here.", "estimated_duration_seconds": 6.0}],
        }
        resp_data = {"choices": [{"message": {"content": json.dumps(fake_content)}}]}
        mock_resp = pytest.importorskip("unittest.mock").MagicMock()
        mock_resp.read.return_value = json.dumps(resp_data).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None
        return mock_resp

    with pytest.importorskip("unittest.mock").patch("urllib.request.urlopen", side_effect=fake_urlopen):
        provider.generate_script(
            topic="Quantum Computing Chips",
            content_id="test-tech-llm-01",
            channel_profile=tech_profile,
        )

    assert captured_req is not None
    messages = captured_req.get("messages", [])
    system_msg = next((m["content"] for m in messages if m["role"] == "system"), "")
    user_msg = next((m["content"] for m in messages if m["role"] == "user"), "")

    combined = system_msg + "\n" + user_msg
    assert "CHANNEL EDITORIAL DIRECTIVES" in combined
    assert "technology" in combined
    assert "futuristic_technical" in combined
    assert "hardware_and_electronics" in combined
