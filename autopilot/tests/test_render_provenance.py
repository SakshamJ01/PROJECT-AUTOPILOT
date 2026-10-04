"""P0 render provenance tests — offline, deterministic.

These cover the failure mode where a production engine discards the verified
assets (and re-fetches its own footage) so that earlier CLIP/rights evidence
describes a video that is not the shipped file.
"""
import subprocess
from pathlib import Path

import pytest

from autopilot.core.render_provenance import (
    DEFAULT_MATCH_THRESHOLD,
    RenderProvenanceError,
    _frame_signatures,
    verify_asset_presence,
    summarize,
    write_provenance_report,
)

pytestmark = pytest.mark.skipif(
    subprocess.run(["where", "ffmpeg"], capture_output=True).returncode != 0,
    reason="ffmpeg is required for render provenance verification",
)

_SIZE = "size=360x640"
_RATE = "rate=25"


def _make_clip(path: Path, source: str, seconds: int = 2) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"{source}={_SIZE}:{_RATE}",
            # -t works for every lavfi source, unlike a source-level duration option.
            "-t", str(seconds),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
    )
    return str(path)


def test_frames_are_sampled_across_whole_clip(tmp_path):
    """Regression: fps= is a rate, not a count.

    Passing the desired sample count straight to ffmpeg's fps filter only ever
    reads the first second of footage, which silently reduced coverage to the
    opening scene.
    """
    # life is animated, so distinct sample timestamps yield distinct hashes.
    clip = _make_clip(tmp_path / "long.mp4", "life", seconds=10)

    few = _frame_signatures(Path(clip), 6)
    assert len(few) == 6

    # Signatures must be distinct because they come from different timestamps.
    unique = {sig.tobytes() for sig in few}
    assert len(unique) == len(few), "samples were not spread across the clip"


def test_still_image_asset_is_fingerprintable(tmp_path):
    """Regression: the fps filter made every still image unverifiable.

    ffmpeg's fps filter emits ZERO frames for a single-image input, so
    _frame_signatures returned an empty list, verify_asset_presence reported the
    scene as MISSING, and any image-based job failed render provenance.
    """
    img = tmp_path / "still.png"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "testsrc=" + _SIZE + ":" + _RATE, "-frames:v", "1", str(img)],
        check=True,
    )

    sigs = _frame_signatures(img, 12)
    assert len(sigs) >= 1, "still image produced no signature"
    assert sigs[0].size == (9 - 1) * 8  # 8x8 horizontal-difference grid


def test_still_image_asset_verified_in_render(tmp_path):
    """End-to-end: an image-based scene must pass provenance, not fail it."""
    from PIL import Image

    asset = tmp_path / "asset.png"
    Image.new("RGB", (360, 640), (12, 200, 90)).save(asset)

    render = tmp_path / "render.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(asset),
         "-t", "2", "-r", "25", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(render)],
        check=True,
    )

    report = verify_asset_presence(render, [{"scene_id": "s1", "asset_path": str(asset)}])
    assert report.claimed_scene_count == 1
    assert report.missing_scene_ids == [], "image asset wrongly reported missing"
    assert report.verified_scene_count == 1
    assert report.valid is True


def test_planned_assets_present_in_render_are_verified(tmp_path):
    asset_a = _make_clip(tmp_path / "a.mp4", "testsrc")
    asset_b = _make_clip(tmp_path / "b.mp4", "smptebars")
    # The render mimics the real renderer: scale/crop plus burned overlays.
    render = tmp_path / "render.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-i", asset_a, "-i", asset_b,
            "-filter_complex",
            "[0:v]scale=360:640:force_original_aspect_ratio=increase,crop=360:640,format=yuv420p[v0];"
            "[1:v]scale=360:640:force_original_aspect_ratio=increase,crop=360:640,format=yuv420p[v1];"
            "[v0][v1]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(render),
        ],
        check=True,
    )

    scenes = [
        {"scene_id": "scene-01", "asset_path": asset_a},
        {"scene_id": "scene-02", "asset_path": asset_b},
    ]
    report = verify_asset_presence(render, scenes, production_engine="ffmpeg")

    assert report.valid is True
    assert report.verified_scene_count == 2
    assert report.missing_scene_ids == []
    for scene in report.scenes:
        assert scene.present is True
        assert scene.best_distance <= DEFAULT_MATCH_THRESHOLD


def test_render_missing_assets_fails_closed(tmp_path):
    """A render that does not contain the planned assets must NOT verify."""
    asset = _make_clip(tmp_path / "asset.mp4", "testsrc")
    unrelated = _make_clip(tmp_path / "unrelated.mp4", "smptebars")

    report = verify_asset_presence(
        unrelated,
        [{"scene_id": "scene-01", "asset_path": asset}],
        production_engine="moneyprinterturbo",
    )

    assert report.valid is False
    assert report.missing_scene_ids == ["scene-01"]
    assert report.scenes[0].present is False
    assert report.scenes[0].best_distance > DEFAULT_MATCH_THRESHOLD


def test_missing_and_unclaimed_assets_are_reported(tmp_path):
    report = verify_asset_presence(
        _make_clip(tmp_path / "render.mp4", "testsrc"),
        [
            {"scene_id": "scene-01", "asset_path": str(tmp_path / "nope.mp4")},
            {"scene_id": "scene-02"},
        ],
    )

    # A claimed-but-missing asset is a provenance failure.
    assert report.applicable is True
    assert report.valid is False
    assert report.missing_scene_ids == ["scene-01"]
    assert "missing" in (report.scenes[0].error or "").lower()
    # A scene that claims nothing is not a failure, but must still be recorded.
    assert "no asset claimed" in (report.scenes[1].error or "").lower()


def test_no_claimed_assets_is_not_applicable(tmp_path):
    """A render whose scenes claim no asset has no provenance claim to disprove."""
    report = verify_asset_presence(
        _make_clip(tmp_path / "render.mp4", "testsrc"),
        [{"scene_id": "scene-01"}],
    )

    assert report.applicable is False
    assert report.valid is False
    assert report.claimed_scene_count == 0
    assert "not applicable" in summarize(report).lower()


def test_absent_render_reports_error(tmp_path):
    report = verify_asset_presence(tmp_path / "does_not_exist.mp4", [{"scene_id": "s1"}])
    assert report.valid is False
    assert report.errors


def test_frame_signatures_rejects_zero_samples(tmp_path):
    clip = _make_clip(tmp_path / "clip.mp4", "testsrc")
    with pytest.raises(RenderProvenanceError):
        _frame_signatures(Path(clip), 0)


def test_report_round_trips_to_disk(tmp_path):
    asset = _make_clip(tmp_path / "asset.mp4", "testsrc")
    report = verify_asset_presence(
        _make_clip(tmp_path / "render.mp4", "testsrc"),
        [{"scene_id": "scene-01", "asset_path": asset}],
    )
    out = write_provenance_report(report, tmp_path / "provenance.json")
    assert out.exists()
    assert "scene-01" in out.read_text(encoding="utf-8")
