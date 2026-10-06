"""Plan 3.2 — TTS prosody variation per scene role.

Hook scenes synthesize at rate +8%, the payoff/CTA scene at -5%, and
middle scenes stay neutral. The hint reaches the provider request before
word-timestamp alignment, so captions measure the rate-shifted audio;
Kokoro ignores the hint (its speed is hardcoded) without error.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from autopilot.core.audio_truth_pass import VoiceQAReport
from autopilot.core.timeline import NarrativeRole
from autopilot.core.voice_studio import (
    NeuralVoiceStudio,
    ProsodyProfile,
    ProsodySettings,
    prosody_for_narrative_role,
)
from autopilot.providers.mock_tts import MockTTSProvider


def test_role_to_rate_mapping() -> None:
    assert ProsodySettings.from_profile(prosody_for_narrative_role(NarrativeRole.HOOK)).rate == "+8%"
    assert ProsodySettings.from_profile(prosody_for_narrative_role(NarrativeRole.CTA)).rate == "-5%"
    assert ProsodySettings.from_profile(prosody_for_narrative_role(NarrativeRole.PAYOFF)).rate == "-5%"
    assert ProsodySettings.from_profile(prosody_for_narrative_role(NarrativeRole.CONTENT)).rate == "+0%"

    assert prosody_for_narrative_role("hook") == ProsodyProfile.ENERGETIC
    assert prosody_for_narrative_role("CTA") == ProsodyProfile.PAYOFF
    assert prosody_for_narrative_role("content") == ProsodyProfile.CONVERSATIONAL


class _RecordingProvider:
    """Captures the exact kwargs the TTS request carries; makes real audio."""

    provider_name = "recording"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def synthesize(self, text: str, out_path: str, **kwargs) -> str:
        self.calls.append(dict(kwargs))
        return MockTTSProvider().synthesize(text=text, out_path=out_path)


class _FakeTruth:
    """Deterministic stand-in for the Whisper truth pass (alignment exists)."""

    def execute_truth_pass(
        self,
        audio_path,
        expected_text,
        expected_duration_sec,
        mastered_lufs,
        mastered_true_peak,
    ) -> VoiceQAReport:
        return VoiceQAReport(expected_text=expected_text, transcribed_text=expected_text)


def test_hook_and_payoff_requests_carry_role_rates(tmp_path: Path) -> None:
    recorder = _RecordingProvider()
    studio = NeuralVoiceStudio(truth_pipeline=_FakeTruth())

    cases = [
        (NarrativeRole.HOOK, ProsodyProfile.ENERGETIC, "+8%"),
        (NarrativeRole.CONTENT, ProsodyProfile.CONVERSATIONAL, "+0%"),
        (NarrativeRole.CTA, ProsodyProfile.PAYOFF, "-5%"),
    ]
    with patch("autopilot.core.voice_studio.get_tts_provider", return_value=recorder):
        for role, expected_profile, expected_rate in cases:
            result = studio.process_scene_narration(
                scene_id=f"scene-{role.name}",
                raw_text="The sea keeps its secrets buried in the dark.",
                prosody=prosody_for_narrative_role(role),
                provider_name="recording",
                out_dir=tmp_path,
            )
            assert result.prosody_profile is expected_profile
            assert recorder.calls[-1]["rate"] == expected_rate

    assert [c["rate"] for c in recorder.calls] == ["+8%", "+0%", "-5%"]
    assert recorder.calls[0]["pitch"] == "+2Hz"
    assert recorder.calls[2]["pitch"] == "-1Hz"


def _probe_duration(path: Path) -> float:
    r = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True, text=True, timeout=30,
    )
    return float(r.stdout.strip())


def test_rate_changes_generated_audio_duration(tmp_path: Path) -> None:
    """Real Edge TTS: +8% renders shorter, -5% renders longer than neutral."""
    from autopilot.providers.edge_tts_provider import EdgeTTSProvider

    provider = EdgeTTSProvider()
    text = "The ocean keeps its secrets for a hundred years."
    durations: dict[str, float] = {}
    for rate in ("+0%", "+8%", "-5%"):
        out = tmp_path / f"edge_{rate}.wav"
        provider.synthesize(text, str(out), rate=rate)
        durations[rate] = _probe_duration(out)

    assert durations["+8%"] < durations["+0%"] < durations["-5%"]


def test_kokoro_ignores_prosody_hint_without_error(tmp_path: Path) -> None:
    import numpy as np
    from autopilot.providers.kokoro_tts_provider import KokoroTTSProvider

    provider = KokoroTTSProvider()
    mock_kokoro = MagicMock()
    mock_kokoro.create.return_value = (np.zeros(24000, dtype=np.float32), 24000)
    mock_kokoro.get_voices.return_value = ["af_sarah", "am_adam"]

    m_path = tmp_path / "kokoro-v1.0.onnx"
    v_path = tmp_path / "voices.bin"
    m_path.touch()
    v_path.touch()

    with patch("kokoro_onnx.Kokoro", return_value=mock_kokoro), patch(
        "autopilot.providers.kokoro_tts_provider.resolve_kokoro_model_paths",
        return_value=(m_path, v_path),
    ):
        res = provider.synthesize(
            "Hello from the payoff scene",
            str(tmp_path / "out.wav"),
            voice_id="af_sarah",
            rate="-5%",
            pitch="-1Hz",
            volume="+3%",
        )

    assert Path(res).exists()
    mock_kokoro.create.assert_called_once_with(
        "Hello from the payoff scene", voice="af_sarah", speed=1.0
    )


def test_pipeline_passes_role_prosody_to_voice_stage() -> None:
    """The pipeline voice stage must ask for hook/middle/payoff prosody."""
    import uuid

    from autopilot.core.pipeline import PipelineOrchestrator
    from autopilot.core.contracts import ScriptDocument, ScriptScene

    p = PipelineOrchestrator()
    job_id = f"test-prosody-{uuid.uuid4().hex[:8]}"
    script = ScriptDocument(
        content_id=job_id,
        topic="Titanic secrets",
        working_title="Test",
        hook="Test Hook",
        cta="Test CTA",
        scenes=[
            ScriptScene(scene_id="scene-01", order=1, narration="The hook narration line here", visual_intent="hook visual"),
            ScriptScene(scene_id="scene-02", order=2, narration="The middle narration line", visual_intent="middle visual"),
            ScriptScene(scene_id="scene-03", order=3, narration="The payoff narration line", visual_intent="payoff visual"),
        ],
    )

    captured: list[tuple[str, object]] = []

    def fake_process(scene_id, raw_text, prosody=None, **kwargs):
        captured.append((scene_id, prosody))
        return SimpleNamespace(
            mastered_audio_path=str(Path(kwargs.get("out_dir", ".")) / f"{scene_id}.wav"),
            duration_sec=2.5,
            qa_report=SimpleNamespace(authoritative_words=[]),
        )

    with patch.object(p.db, "get_latest_research_report_for_topic", return_value=None), \
         patch("autopilot.providers.mock_search.MockSearchProvider.search", return_value=[]), \
         patch("autopilot.providers.mock_script.MockScriptProvider.generate_script", return_value=script), \
         patch("autopilot.core.pipeline.process_scene_assets", return_value=([], {})), \
         patch("autopilot.core.pipeline.FFmpegRenderer.render", return_value=MagicMock()), \
         patch("autopilot.core.pipeline.get_tts_provider", return_value=MagicMock(provider_name="edge_tts")), \
         patch("autopilot.core.pipeline.FasterWhisperEngine", return_value=MagicMock()), \
         patch(
             "autopilot.core.voice_studio.NeuralVoiceStudio.process_scene_narration",
             side_effect=fake_process,
         ):
        try:
            p.run_pipeline(job_id=job_id, topic="Titanic secrets", tts_provider="edge_tts", llm_provider="mock")
        except Exception:
            pass

    assert [s for s, _ in captured] == ["scene_scene-01", "scene_scene-02", "scene_scene-03"]
    assert captured[0][1] is ProsodyProfile.ENERGETIC
    assert captured[1][1] is ProsodyProfile.CONVERSATIONAL
    assert captured[2][1] is ProsodyProfile.PAYOFF
