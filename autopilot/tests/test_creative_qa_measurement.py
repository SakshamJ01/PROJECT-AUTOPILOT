"""Regression Tests for Creative QA measurement correctness.

These lock in fixes for QA dimensions that reported confident, wrong results:

* CLIP cosine similarities were treated as percentages, so every real asset
  looked like a ~26% mismatch and both semantic dimensions hard-BLOCKed.
* The freeze detector used ffmpeg's ``scene`` filter, which only fires on hard
  cuts. Continuous B-roll therefore reported "no visual changes ... appears
  frozen" even when consecutive frames differed substantially.
* Video assets recorded without an explicit ``asset_type`` defaulted to
  "image", so real footage was reported as a "static image" defect.

Test fixtures here are synthetic clips generated with ffmpeg. They are test
inputs only and are never treated as production evidence.
"""
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from autopilot.core.config import CONFIG
from autopilot.core.contracts import AssetProvenance
from autopilot.core.creative_qa import (
    CreativeQAEngine,
    CreativeQAStatus,
    clip_similarity_to_score,
)
from autopilot.providers.openai_llm_provider import (
    MAX_SCENE_SPEECH_WORDS,
    TTS_WORDS_PER_SECOND,
)


# ------------------------------------------------------------------
# CLIP similarity calibration
# ------------------------------------------------------------------


def test_clip_measured_real_match_passes_band():
    """A genuine text-match similarity must not be scored as a low mismatch."""
    real_match = 0.256  # measured real match documented in config
    assert clip_similarity_to_score(real_match) >= 65.0


def test_clip_decoy_similarity_blocks():
    """An unrelated decoy must still be rejected, not rubber-stamped."""
    decoy = 0.177  # measured decoy documented in config
    assert clip_similarity_to_score(decoy) < 45.0


def test_clip_below_enforced_floor_blocks():
    floor = float(CONFIG.visual_semantic_min_similarity)
    assert clip_similarity_to_score(floor - 0.05) < 45.0


def test_clip_score_is_monotonic_and_bounded():
    values = [clip_similarity_to_score(v) for v in (0.0, 0.1, 0.18, 0.21, 0.26, 0.32, 0.5)]
    assert all(0.0 <= v <= 100.0 for v in values)
    assert values == sorted(values), "score must increase with similarity"


# ------------------------------------------------------------------
# Freeze detection against real pixels
# ------------------------------------------------------------------


def _make_clip(path: Path, source: str, duration: int) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", source,
            "-t", str(duration), "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def test_moving_video_is_not_reported_as_frozen(tmp_path):
    """Continuous motion without hard cuts must not trigger a freeze BLOCK."""
    clip = _make_clip(
        tmp_path / "moving.mp4",
        "testsrc2=size=320x240:rate=25",
        6,
    )
    item = CreativeQAEngine()._evaluate_freeze_sections(clip, 6.0)
    assert item.status == CreativeQAStatus.PASS, item.evidence
    assert "frozen" not in item.evidence.lower() or "no frozen" in item.evidence.lower()


def test_genuinely_frozen_video_is_still_blocked(tmp_path):
    """A truly static clip must still be caught, so the fix is not a bypass."""
    clip = _make_clip(
        tmp_path / "frozen.mp4",
        "color=c=black:size=320x240:rate=25",
        6,
    )
    item = CreativeQAEngine()._evaluate_freeze_sections(clip, 6.0)
    assert item.status in (CreativeQAStatus.BLOCK, CreativeQAStatus.WARN), item.evidence


def test_freeze_inspection_without_video_is_not_a_pass_by_default(tmp_path):
    """Missing input must not be reported as inspected-and-clean."""
    item = CreativeQAEngine()._evaluate_freeze_sections(tmp_path / "nope.mp4", 0.0)
    assert "skipped" in item.evidence.lower()
    assert item.confidence == pytest.approx(0.5)


# ------------------------------------------------------------------
# Video assets must not be reported as still images
# ------------------------------------------------------------------


def _scene(media_type: str, duration: float):
    scene = SimpleNamespace(
        scene_id="scene-01",
        duration_sec=duration,
        timing=SimpleNamespace(duration_sec=duration),
        selected_assets=[
            SimpleNamespace(
                asset_type=media_type,
                media_type=media_type,
                asset_path="asset.mp4",
                local_path="asset.mp4",
                provenance=AssetProvenance(semantic_score=0.28),
                semantic_score=0.28,
            )
        ],
        narration="Narration present.",
    )
    return scene


def test_long_video_scene_is_not_flagged_as_static_image():
    """A 6s video scene must not be penalized as a >5s static image."""
    timeline = SimpleNamespace(scenes=[_scene("video", 6.0)])
    item = CreativeQAEngine()._evaluate_dead_sections(timeline)
    assert "static image" not in item.evidence, item.evidence
    assert item.status == CreativeQAStatus.PASS


def test_long_still_image_scene_is_still_penalized():
    """A genuinely long still image must remain a defect."""
    timeline = SimpleNamespace(scenes=[_scene("image", 6.0)])
    item = CreativeQAEngine()._evaluate_dead_sections(timeline)
    assert "static image" in item.evidence
    assert item.status != CreativeQAStatus.PASS


# ------------------------------------------------------------------
# Generation must not produce scenes the pacing gate will reject
# ------------------------------------------------------------------


def test_pacing_constants_match_the_gate():
    """The word budget must stay consistent with the 4.5s pacing target."""
    floor = float(CONFIG.visual_semantic_min_similarity)  # sanity: config loaded
    assert floor > 0
    assert MAX_SCENE_SPEECH_WORDS / TTS_WORDS_PER_SECOND <= 5.0
    # 11 words is the ceiling and must never sit above the 4.5s gate target
    # plus the documented 0.5s "slightly long" grace band.
    assert MAX_SCENE_SPEECH_WORDS <= 11


def test_script_validation_rejects_over_long_narration():
    """An over-long scene must fail at script time, not after a full render."""
    from autopilot.providers.openai_llm_provider import _parse_json_to_script_document

    long_narration = " ".join(["word"] * (MAX_SCENE_SPEECH_WORDS + 4))
    parsed = {
        "title": "T",
        "description": "D",
        "hook_text": "H",
        "cta_text": "C",
        "scenes": [
            {
                "scene_id": "scene-01",
                "order": 1,
                "narration": long_narration,
                "visual_intent": "a concrete physical subject",
                "asset_query": "concrete subject",
                "on_screen_text": "TEXT",
                "scene_type": "broll",
            }
        ],
    }
    with pytest.raises(ValueError, match="pacing budget"):
        _parse_json_to_script_document(
            parsed,
            topic="Test Topic",
            content_id="cid",
            language="en",
            raw_response="{}",
            provider_name="test",
            model_name="test",
            valid_source_refs=set(),
            has_research=False,
        )


def test_script_validation_accepts_budget_sized_narration():
    """A scene inside the budget must still build successfully."""
    from autopilot.providers.openai_llm_provider import _parse_json_to_script_document

    parsed = {
        "title": "T",
        "description": "D",
        "hook_text": "H",
        "cta_text": "C",
        "scenes": [
            {
                "scene_id": "scene-01",
                "order": 1,
                "narration": " ".join(["word"] * 9),
                "visual_intent": "a concrete physical subject",
                "asset_query": "concrete subject",
                "on_screen_text": "TEXT",
                "scene_type": "broll",
            }
        ],
    }
    script = _parse_json_to_script_document(
        parsed,
        topic="Test Topic",
        content_id="cid",
        language="en",
        raw_response="{}",
        provider_name="test",
        model_name="test",
        valid_source_refs=set(),
        has_research=False,
    )
    assert len(script.scenes) == 1
