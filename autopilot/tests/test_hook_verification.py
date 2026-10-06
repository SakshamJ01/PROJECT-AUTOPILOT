"""Hook verification (plan 2.2).

The Moon job shipped with ``hook`` == None even though the model was asked for
``hook_text``. The cause was ``parsed.get("hook_text", fallback)``: when the key
*exists* but is null/empty, ``.get`` returns None/"" and the fallback never
fires. ``hook`` is the first line of the published description
(publisher.py:717), so a null hook silently degraded every upload.

These tests pin the three guarantees:
  1. hook_text is never empty/null,
  2. it is distinct from scene-01 narration,
  3. it is recorded as synthesized when we had to build it, not silently defaulted.
"""
import json

import pytest

from autopilot.core.contracts import ContentItem, ContentPackage, PublicationMetadata, ScriptDocument
from autopilot.providers.openai_llm_provider import (
    _channel_hook_style,
    _hook_is_usable,
    _normalize_hook_text,
    _parse_json_to_script_document,
    _pick_strongest_middle_fact,
    _resolve_hook_text,
    _synthesize_hook_text,
)


# Each narration stays within the 8-word-per-scene pacing ceiling enforced by
# PacingBudgetError, so these fixtures exercise hook logic and not pacing.
SCENES = [
    {"scene_id": "s1", "narration": "Why does the ocean look so blue?"},
    {"scene_id": "s2", "narration": "Water absorbs red light very strongly."},
    {"scene_id": "s3", "narration": "In 1874 divers found the Antikythera wreck."},
    {"scene_id": "s4", "narration": "Only blue wavelengths survive the descent."},
]


def _doc(parsed_hook=None, scenes=None, topic="Why the ocean is blue", hook_style="intriguing_question"):
    parsed = {
        "title": "Ocean Blue",
        "hook_text": parsed_hook,
        "scenes": [
            {
                "scene_id": s["scene_id"],
                "narration": s["narration"],
                "visual_intent": "a photo",
                "asset_query": "ocean blue",
                "on_screen_text": "BLUE",
                "scene_type": "broll",
                "estimated_duration_seconds": 4.0,
            }
            for s in (scenes or SCENES)
        ],
    }
    return _parse_json_to_script_document(
        parsed=parsed,
        topic=topic,
        content_id="c1",
        language="en",
        raw_response="{}",
        provider_name="test",
        model_name="test",
        valid_source_refs=set(),
        has_research=False,
        hook_style=hook_style,
    )


# --------------------------------------------------------------------------
# the core defect
# --------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [None, "", "   ", "\n"])
def test_null_or_empty_hook_never_survives(bad):
    """dict.get(key, fallback) cannot rescue a present-but-empty key."""
    doc = _doc(parsed_hook=bad)
    assert doc.hook
    assert doc.hook.strip()
    assert doc.generation_metadata["hook_synthesized"] is True


def test_hook_that_copies_scene_one_is_replaced():
    doc = _doc(parsed_hook=SCENES[0]["narration"])
    assert doc.hook.strip().lower() != SCENES[0]["narration"].strip().lower()
    assert doc.generation_metadata["hook_synthesized"] is True


def test_missing_hook_key_is_replaced():
    """Key entirely absent (not just null) is the other half of the defect."""
    doc = _parse_json_to_script_document(
        parsed={
            "scenes": [
                {
                    "scene_id": s["scene_id"],
                    "narration": s["narration"],
                    "visual_intent": "a photo",
                    "asset_query": "ocean blue",
                    "on_screen_text": "BLUE",
                    "scene_type": "broll",
                    "estimated_duration_seconds": 4.0,
                }
                for s in SCENES
            ]
        },
        topic="Why the ocean is blue",
        content_id="c1",
        language="en",
        raw_response="{}",
        provider_name="test",
        model_name="test",
        valid_source_refs=set(),
        has_research=False,
    )
    assert doc.hook
    assert doc.generation_metadata["hook_synthesized"] is True


def test_good_hook_is_preserved_and_flagged_false():
    good = "The answer is stranger than the question you are about to ask."
    doc = _doc(parsed_hook=good)
    assert doc.hook == good
    assert doc.generation_metadata["hook_synthesized"] is False


# --------------------------------------------------------------------------
# synthesis quality
# --------------------------------------------------------------------------
def test_synthesis_prefers_a_middle_fact_not_the_hook_scene():
    fact = _pick_strongest_middle_fact(SCENES)
    assert fact != SCENES[0]["narration"]
    assert "1874" in fact  # densest middle scene


def test_historical_style_uses_a_date():
    hook = _synthesize_hook_text(SCENES, "Antikythera", "historical_framing_and_date")
    assert "1874" in hook


def test_historical_style_does_not_repeat_the_date_twice():
    scenes = [
        {"narration": "A perfectly fine hook question here"},
        {"narration": "In 1874 sponge divers found the Antikythera wreck."},
    ]
    hook = _synthesize_hook_text(scenes, "Antikythera", "historical_framing_and_date")
    assert hook.count("1874") == 1, hook


@pytest.mark.parametrize("style", [
    "historical_framing_and_date",
    "surprising_discovery",
    "breakthrough_fact",
    "intriguing_question",
    "totally_unknown_style",
])
def test_every_style_produces_a_non_empty_hook(style):
    hook = _synthesize_hook_text(SCENES, "Why the ocean is blue", style)
    assert hook and len(hook.split()) >= 4
    assert _hook_is_usable(hook, SCENES[0]["narration"])


def test_hook_is_not_just_the_topic_echoed():
    scenes = [{"narration": "Why the ocean is blue"}, {"narration": "Why the ocean is blue"}]
    hook = _synthesize_hook_text(scenes, "Why the ocean is blue", "breakthrough_fact")
    assert "everything about" in hook.lower()


def test_synthesized_hook_is_capped_in_length():
    long_scenes = [
        {"narration": "Short hook question"},
        {"narration": " ".join(["word"] * 80)},
    ]
    hook = _synthesize_hook_text(long_scenes, "Topic", "breakthrough_fact")
    assert len(hook.split()) <= 30, len(hook.split())


def test_single_scene_script_still_yields_a_hook():
    scenes = [{"narration": "A lonely single scene narration."}]
    hook, synth = _resolve_hook_text({}, scenes, "Lonely Topic", "breakthrough_fact")
    assert hook and synth is True


def test_normalization_strips_noise():
    assert _normalize_hook_text('  "A  noisy\n  hook "  ') == "A noisy hook"


# --------------------------------------------------------------------------
# style plumbing + persistence
# --------------------------------------------------------------------------
def test_hook_style_resolution_defaults_and_reads_channel():
    assert _channel_hook_style(None) == "intriguing_question"
    assert _channel_hook_style({"hook_style": "surprising_discovery"}) == "surprising_discovery"
    assert _channel_hook_style({}) == "intriguing_question"


def test_style_is_recorded_in_metadata():
    doc = _doc(parsed_hook=None, hook_style="historical_framing_and_date")
    assert doc.generation_metadata["hook_style"] == "historical_framing_and_date"


def test_hook_round_trips_into_script_json_and_package(tmp_path):
    """The publisher reads hook from content_package.json['script'] (or script.json)."""
    doc = _doc(parsed_hook=None)

    script_file = tmp_path / "script.json"
    script_file.write_text(doc.model_dump_json(indent=2), encoding="utf-8")

    package = ContentPackage(
        content_item=ContentItem(content_id="c1", topic="t", format="9:16_video"),
        script=doc,
        publication=PublicationMetadata(
            title=doc.working_title, description=f"{doc.hook}\n\n{doc.cta}",
            hashtags=["shorts"], privacy_status="private",
        ),
        provenance={"provider": "test"},
    )
    pkg_file = tmp_path / "content_package.json"
    pkg_file.write_text(package.model_dump_json(indent=2), encoding="utf-8")

    script_meta = json.loads(script_file.read_text(encoding="utf-8"))
    assert script_meta.get("hook"), "publisher would read an empty hook from script.json"

    pkg_data = json.loads(pkg_file.read_text(encoding="utf-8"))
    pkg_hook = (pkg_data.get("script") or {}).get("hook")
    assert pkg_hook, "publisher prefers content_package.json['script']['hook']"
    assert pkg_hook == doc.hook