"""Wiring tests for the dormant modules (user-selected upgrade).

Covers the production wire points added for the previously-dormant modules:

  - ``visual_intelligence``: the pipeline computes saliency-based ``crop_framing``
    on acquired image assets, and ``build_materialized_timeline`` carries that
    framing into ``SelectedAsset`` + aligns the caption plan's safe zone so QA's
    caption-placement gate sees the real subject location.
  - ``targeted_regeneration``: the QA stage routes defect invalidation through
    ``TargetedRegenerationController.route_defect`` (single authoritative
    dependency graph) instead of a hardcoded ``if/elif``.
  - ``claim_verification``: the SCRIPT stage runs the dormant ClaimVerifier over
    each scene's narration against its own research evidence, fail-open, and
    records the result for QA.
"""
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from autopilot.core.pipeline import (
    _attach_claim_verification,
    _compute_scene_crop_framing,
    _route_regen_invalidation,
)
from autopilot.core.timeline import CaptionPosition
from autopilot.core.timeline_builder import build_materialized_timeline


def _write_wav(path: Path, dur: float = 2.0, sr: int = 16000):
    import wave

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(b"\x00\x00" * int(sr * dur))


def _fake_artifact(scene_id: str, path: Path, asset_type: str = "image") -> SimpleNamespace:
    return SimpleNamespace(scene_id=scene_id, normalized_path=str(path), asset_type=asset_type)


# ---------------------------------------------------------------------------
# visual_intelligence: saliency crop framing
# ---------------------------------------------------------------------------

def test_compute_scene_crop_framing_bottom_subject_goes_top(tmp_path: Path) -> None:
    """A bright stripe band in the bottom half pushes the caption safe zone to TOP."""
    from autopilot.core.visual_intelligence import compute_saliency_and_safe_zone

    # Gradient energy (edges) confined to a bottom band -> center_of_energy > 0.58.
    img = np.zeros((256, 256), dtype=np.uint8)
    for row in range(180, 220):
        img[row, ::2] = 255
    img_path = tmp_path / "bottom_subject.png"
    Image.fromarray(img).save(img_path)

    framing = _compute_scene_crop_framing([_fake_artifact("scene_1", img_path)])
    assert framing["scene_1"]["caption_safe_zone"] == "TOP"
    assert framing["scene_1"]["saliency_y"] > 0.58

    # The underlying analyser agrees (cheap sanity check of our expectations).
    _, cy, safe = compute_saliency_and_safe_zone(img_path)
    assert cy > 0.58
    assert safe == CaptionPosition.TOP


def test_compute_scene_crop_framing_defaults_keep_lower(tmp_path: Path) -> None:
    """Missing/unreadable files and video assets keep the neutral default."""
    missing = _fake_artifact("scene_1", tmp_path / "nope.png")
    video = _fake_artifact("scene_2", tmp_path / "clip.mp4", asset_type="video")
    framing = _compute_scene_crop_framing([missing, video])
    assert framing["scene_1"] == {"saliency_x": 0.5, "saliency_y": 0.5, "caption_safe_zone": "LOWER"}
    assert framing["scene_2"] == {"saliency_x": 0.5, "saliency_y": 0.5, "caption_safe_zone": "LOWER"}


# ---------------------------------------------------------------------------
# targeted_regeneration: routed invalidation
# ---------------------------------------------------------------------------

def test_route_regen_invalidation_script_general() -> None:
    routed, groups = _route_regen_invalidation("SCRIPT")
    assert "SCRIPT" in routed
    assert groups == {"SCRIPT", "VOICE", "ASSETS", "RENDER"}
    # General defects are routed as SCRIPT-level.
    routed2, groups2 = _route_regen_invalidation("GENERAL")
    assert "SCRIPT" in routed2
    assert groups2 == {"SCRIPT", "VOICE", "ASSETS", "RENDER"}


def test_route_regen_invalidation_voice_and_render() -> None:
    routed, groups = _route_regen_invalidation("VOICE")
    assert routed[:1] == ["VOICE"]
    assert groups == {"VOICE", "RENDER"}

    routed2, groups2 = _route_regen_invalidation("RENDER")
    assert "RENDER" in routed2
    assert groups2 == {"RENDER"}

    routed3, groups3 = _route_regen_invalidation("UNKNOWN_TARGET")
    assert routed3 == []
    assert groups3 == set()


# ---------------------------------------------------------------------------
# claim_verification: SCRIPT-stage grounding pass (fail-open)
# ---------------------------------------------------------------------------

def _claimable_script(scenes) -> SimpleNamespace:
    return SimpleNamespace(
        scenes=[SimpleNamespace(scene_id=sid, narration=text) for sid, text in scenes],
        generation_metadata={},
    )


def test_attach_claim_verification_records_grounded_scenes() -> None:
    report = {
        "evidence": [
            {
                "source_id": "wiki-1",
                "snippet": "Snapdragon flowers contain magnetite crystals, scientists reported.",
            }
        ]
    }
    script = _claimable_script(
        [("scene_1", "Snapdragon flowers contain magnetite crystals"), ("scene_2", "The moon is made of cheese")]
    )
    script = _attach_claim_verification(script, report)

    meta = script.generation_metadata["claim_verification"]
    assert meta["scenes_checked"] == 2
    assert meta["verified"] == 1
    assert meta["flagged"] == 1
    by_id = {r["scene_id"]: r for r in meta["scenes"]}
    assert by_id["scene_1"]["status"] == "VERIFIED"
    assert by_id["scene_1"]["source"] == "wiki-1"
    assert by_id["scene_2"]["status"] == "UNVERIFIED"


def test_attach_claim_verification_fail_open_and_idempotent() -> None:
    # Missing research evidence: unchanged script, no crash.
    script = _claimable_script([("scene_1", "Some narration")])
    out = _attach_claim_verification(script, None)
    assert out is script
    assert "claim_verification" not in (script.generation_metadata or {})

    # Malformed evidence: helper swallows and returns the script untouched.
    script2 = _claimable_script([("scene_1", "Some narration")])
    out2 = _attach_claim_verification(script2, {"evidence": [None, 42]})
    assert out2 is script2


def test_attach_claim_verification_warns_on_flags() -> None:
    calls = []

    class FakeLogger:
        def warning(self, key, details):
            calls.append((key, details))

    report = {"evidence": [{"source_id": "s0", "snippet": "unrelated dataset content"}]}
    script = _claimable_script([("scene_1", "totally different claim words")])
    _attach_claim_verification(script, report, logger=FakeLogger())
    assert calls and calls[0][0] == "claim_verification_flags"
    assert calls[0][1]["flagged"] >= 1


# ---------------------------------------------------------------------------
# visual_intelligence: crop_framing reaches the QA timeline
# ---------------------------------------------------------------------------

def _build_tiny_timeline(tmp_path, plan_scenes):
    scripts = []
    for idx, ps in enumerate(plan_scenes, start=1):
        scene_id = f"scene_{idx}"
        scripts.append(
            SimpleNamespace(
                scene_id=scene_id,
                narration=f"Narration for {scene_id}.",
                visual_intent=f"visual {scene_id}",
                asset_query=None,
                transition_hint=None,
                word_timestamps=None,
            )
        )
    return build_materialized_timeline(
        job_id="job-wiring",
        script=SimpleNamespace(scenes=scripts),
        plan_scenes=plan_scenes,
        topic="wiring test",
    )


def test_build_materialized_timeline_carries_crop_framing(tmp_path: Path) -> None:
    scenes = []
    for idx, (safe_zone, extra) in enumerate(
        [
            ("TOP", {"saliency_y": 0.72}),
            (None, {"position": "LOWER"}),
        ],
        start=1,
    ):
        asset = tmp_path / f"{idx}.png"
        asset.write_bytes(b"\x89PNG\r\n\x1a\n fake")
        voice = tmp_path / f"{idx}.wav"
        _write_wav(voice)
        plan = {
            "scene_id": f"scene_{idx}",
            "asset_path": str(asset),
            "audio_path": str(voice),
            "duration_sec": 3.0,
            "asset_type": "image",
            "caption_plan": {"position": extra.get("position", "LOWER"), "platform_safe_zone": "YOUTUBE_SHORTS"},
        }
        if safe_zone is not None:
            plan["crop_framing"] = {"saliency_x": 0.5, "saliency_y": extra["saliency_y"], "caption_safe_zone": safe_zone}
        scenes.append(plan)

    timeline = _build_tiny_timeline(tmp_path, scenes)

    # Scene 1: real framing reaches SelectedAsset and moves the caption plan.
    s0 = timeline.scenes[0]
    assert s0.selected_assets[0].crop_framing.saliency_y == pytest.approx(0.72)
    assert s0.selected_assets[0].crop_framing.caption_safe_zone == CaptionPosition.TOP
    assert s0.caption_plan.position == CaptionPosition.TOP

    # Scene 2: no framing -> neutral defaults, plan captions untouched.
    s1 = timeline.scenes[1]
    assert s1.selected_assets[0].crop_framing.caption_safe_zone == CaptionPosition.LOWER
    assert s1.caption_plan.position == CaptionPosition.LOWER