"""Adversarial QA Fixture Tests — Milestone 5.
Deterministic broken media fixtures and contract violations.
Verifies QA correctly BLOCKS or WARNS for all 13 defect categories.
100% offline, GPU-free, Docker-free.
"""
import subprocess
import pytest
from pathlib import Path

from autopilot.core.config import CONFIG
from autopilot.core.contracts import (
    RenderPlan, ContentPackage, ContentItem, ScriptDocument, ScriptScene,
    AssetArtifact, AssetLicense, AssetProvenance, QAStatus
)
from autopilot.core.qa_engine import QAEngine


# Helper to synthesize small test videos via FFmpeg lavfi
def synthesize_test_clip(
    out_path: Path,
    width: int = 1080,
    height: int = 1920,
    duration: float = 2.0,
    video_color: str = "blue",
    audio: bool = True,
    audio_type: str = "sine=frequency=1000",
) -> Path:
    cmd = ["ffmpeg", "-y"]
    # Video input
    cmd.extend(["-f", "lavfi", "-i", f"color=c={video_color}:s={width}x{height}:d={duration}"])
    # Audio input
    if audio:
        cmd.extend(["-f", "lavfi", "-i", f"{audio_type}:duration={duration}"])
        cmd.extend(["-c:a", "aac", "-b:a", "32k"])
    else:
        cmd.extend(["-an"])

    cmd.extend([
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-pix_fmt", "yuv420p", "-r", "25",
        "-t", str(duration),
        str(out_path)
    ])
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        raise RuntimeError(f"Failed to synthesize clip: {r.stderr}")
    return out_path


# ------------------------------------------------------------------
# 1. Corrupt MP4
# ------------------------------------------------------------------
def test_adversarial_1_corrupt_mp4(tmp_path):
    corrupt_file = tmp_path / "corrupt.mp4"
    corrupt_file.write_bytes(b"NOT_A_REAL_MP4_HEADER_CORRUPTED_BYTES_HERE")
    engine = QAEngine(CONFIG)
    report = engine.evaluate(media_path=corrupt_file)

    assert report.status == QAStatus.BLOCK
    assert report.publish_allowed is False
    assert any(c.check_id == "check-container-integrity" and c.status == QAStatus.BLOCK for c in report.checks)


# ------------------------------------------------------------------
# 2. Wrong Dimensions (e.g. 1920x1080 horizontal instead of 1080x1920)
# ------------------------------------------------------------------
def test_adversarial_2_wrong_dimensions(tmp_path):
    clip = synthesize_test_clip(tmp_path / "wrong_dim.mp4", width=1920, height=1080, duration=1.0)
    engine = QAEngine(CONFIG)
    report = engine.evaluate(media_path=clip)

    assert report.status == QAStatus.BLOCK
    assert report.publish_allowed is False
    assert any(c.check_id == "check-video-properties" and c.status == QAStatus.BLOCK for c in report.checks)
    assert any("resolution mismatch" in f.message.lower() for f in report.findings)


# ------------------------------------------------------------------
# 3. Missing Audio Stream when narration exists
# ------------------------------------------------------------------
def test_adversarial_3_missing_audio_stream(tmp_path):
    clip = synthesize_test_clip(tmp_path / "no_audio.mp4", width=1080, height=1920, duration=1.0, audio=False)
    package = ContentPackage(
        content_item=ContentItem(content_id="c3", topic="Topic"),
        script=ScriptDocument(
            content_id="c3", topic="Topic",
            scenes=[ScriptScene(scene_id="s1", order=1, narration="Spoken narration line", visual_intent="v")]
        ),
        voice_artifacts=["voice/seg1.wav"],
    )
    engine = QAEngine(CONFIG)
    report = engine.evaluate(media_path=clip, package=package)

    assert report.status == QAStatus.BLOCK
    assert report.publish_allowed is False
    assert any(c.check_id == "check-audio-properties" and c.status == QAStatus.BLOCK for c in report.checks)


# ------------------------------------------------------------------
# 4. Mismatched Duration (Severe Drift)
# ------------------------------------------------------------------
def test_adversarial_4_mismatched_duration(tmp_path):
    # 2-second clip vs expected 15-second plan
    clip = synthesize_test_clip(tmp_path / "short_drift.mp4", width=1080, height=1920, duration=2.0)
    plan = RenderPlan(
        plan_id="p4", content_id="c4", job_id="j4",
        scenes=[
            {"scene_id": "s1", "duration_sec": 7.5},
            {"scene_id": "s2", "duration_sec": 7.5},
        ]
    )
    engine = QAEngine(CONFIG)
    report = engine.evaluate(media_path=clip, plan=plan)

    assert report.status == QAStatus.BLOCK
    assert report.publish_allowed is False
    assert any(c.check_id == "check-duration-timeline" and c.status == QAStatus.BLOCK for c in report.checks)
    assert any("duration drift" in f.message.lower() for f in report.findings)


# ------------------------------------------------------------------
# 5. Black-Frame Segment
# ------------------------------------------------------------------
def test_adversarial_5_black_frame_segment(tmp_path):
    # 3.5 seconds of pure black frame
    clip = synthesize_test_clip(tmp_path / "black.mp4", width=1080, height=1920, duration=3.5, video_color="black")
    engine = QAEngine(CONFIG)
    report = engine.evaluate(media_path=clip)

    black_check = next((c for c in report.checks if c.check_id == "check-black-frames"), None)
    assert black_check is not None
    assert black_check.status in (QAStatus.WARN, QAStatus.BLOCK)
    assert len(black_check.findings) >= 1
    assert any("black section detected" in f.message.lower() for f in black_check.findings)


# ------------------------------------------------------------------
# 6. Frozen Segment
# ------------------------------------------------------------------
def test_adversarial_6_frozen_frame_segment(tmp_path):
    # 3.5 seconds of identical static blue color
    clip = synthesize_test_clip(tmp_path / "freeze.mp4", width=1080, height=1920, duration=3.5, video_color="blue")
    engine = QAEngine(CONFIG)
    report = engine.evaluate(media_path=clip)

    freeze_check = next((c for c in report.checks if c.check_id == "check-freeze-frames"), None)
    assert freeze_check is not None
    assert len(freeze_check.findings) >= 1
    assert any("frozen frame" in f.message.lower() for f in freeze_check.findings)


# ------------------------------------------------------------------
# 7. Excessive Audio Silence (Dead-Air)
# ------------------------------------------------------------------
def test_adversarial_7_excessive_audio_silence(tmp_path):
    # 3.0 seconds with silent audio
    clip = synthesize_test_clip(
        tmp_path / "silence.mp4", width=1080, height=1920, duration=3.0,
        audio_type="anullsrc=r=22050:cl=mono"
    )
    engine = QAEngine(CONFIG)
    report = engine.evaluate(media_path=clip)

    silence_check = next((c for c in report.checks if c.check_id == "check-dead-air"), None)
    assert silence_check is not None
    assert silence_check.status == QAStatus.WARN
    assert len(silence_check.findings) >= 1
    assert any("silence detected" in f.message.lower() for f in silence_check.findings)


# ------------------------------------------------------------------
# 8. Invalid Caption Timing (Non-positive duration)
# ------------------------------------------------------------------
def test_adversarial_8_invalid_caption_timing(tmp_path):
    clip = synthesize_test_clip(tmp_path / "valid_clip.mp4", width=1080, height=1920, duration=2.0)
    plan = RenderPlan(
        plan_id="p8", content_id="c8", job_id="j8",
        scenes=[
            {"scene_id": "s1", "duration_sec": -1.0, "caption_text": "Negative duration scene"}
        ]
    )
    engine = QAEngine(CONFIG)
    report = engine.evaluate(media_path=clip, plan=plan)

    assert report.status == QAStatus.BLOCK
    assert report.publish_allowed is False
    assert any("non-positive duration" in f.message.lower() for f in report.findings)


# ------------------------------------------------------------------
# 9. Caption Overflow (Line count / Character length overflow)
# ------------------------------------------------------------------
def test_adversarial_9_caption_overflow(tmp_path):
    clip = synthesize_test_clip(tmp_path / "valid_clip.mp4", width=1080, height=1920, duration=2.0)
    plan = RenderPlan(
        plan_id="p9", content_id="c9", job_id="j9",
        scenes=[
            {
                "scene_id": "s1",
                "duration_sec": 2.0,
                "caption_text": "Line 1\nLine 2\nLine 3\nLine 4 (Exceeds maximum two allowed caption lines)",
            }
        ]
    )
    engine = QAEngine(CONFIG)
    report = engine.evaluate(media_path=clip, plan=plan)

    caption_check = next((c for c in report.checks if c.check_id == "check-captions"), None)
    assert caption_check is not None
    assert caption_check.status == QAStatus.WARN
    assert any("exceeds maximum lines" in f.message.lower() for f in caption_check.findings)


# ------------------------------------------------------------------
# 10. Missing Provenance Chain
# ------------------------------------------------------------------
def test_adversarial_10_missing_provenance(tmp_path):
    clip = synthesize_test_clip(tmp_path / "valid_clip.mp4", width=1080, height=1920, duration=2.0)
    asset_file = tmp_path / "asset.png"
    asset_file.write_bytes(b"\x89PNG\r\n\x1a\n")

    art = AssetArtifact(
        artifact_id="art-noprov",
        source_path=str(asset_file),
        provenance=AssetProvenance(provider=""),  # empty provider
        license=AssetLicense(rights_status="VERIFIED"),
    )
    engine = QAEngine(CONFIG)
    report = engine.evaluate(media_path=clip, asset_artifacts=[art])

    assert report.status == QAStatus.BLOCK
    assert report.publish_allowed is False
    assert any("missing provider provenance" in f.message.lower() for f in report.findings)


# ------------------------------------------------------------------
# 11. Unknown / Rejected License
# ------------------------------------------------------------------
def test_adversarial_11_unknown_or_rejected_license(tmp_path):
    clip = synthesize_test_clip(tmp_path / "valid_clip.mp4", width=1080, height=1920, duration=2.0)
    asset_file = tmp_path / "asset.png"
    asset_file.write_bytes(b"\x89PNG\r\n\x1a\n")

    art_unknown = AssetArtifact(
        artifact_id="art-unk",
        source_path=str(asset_file),
        license=AssetLicense(license_name="UNKNOWN", rights_status="UNKNOWN"),
    )
    engine = QAEngine(CONFIG)
    report_unk = engine.evaluate(media_path=clip, asset_artifacts=[art_unknown])

    assert report_unk.status == QAStatus.BLOCK
    assert report_unk.publish_allowed is False
    assert any("unknown rights" in f.message.lower() for f in report_unk.findings)

    art_rejected = AssetArtifact(
        artifact_id="art-rej",
        source_path=str(asset_file),
        license=AssetLicense(license_name="CC-NC-ND", rights_status="REJECTED"),
    )
    report_rej = engine.evaluate(media_path=clip, asset_artifacts=[art_rejected])
    assert report_rej.status == QAStatus.BLOCK
    assert report_rej.publish_allowed is False
    assert any("rejected license" in f.message.lower() for f in report_rej.findings)


# ------------------------------------------------------------------
# 12. Stale Asset Reference in RenderPlan
# ------------------------------------------------------------------
def test_adversarial_12_stale_asset_reference(tmp_path):
    clip = synthesize_test_clip(tmp_path / "valid_clip.mp4", width=1080, height=1920, duration=2.0)
    stale_path = tmp_path / "deleted_or_stale_asset.png"  # does not exist

    plan = RenderPlan(
        plan_id="p12", content_id="c12", job_id="j12",
        scenes=[
            {"scene_id": "s1", "duration_sec": 2.0, "asset_path": str(stale_path)}
        ]
    )
    engine = QAEngine(CONFIG)
    report = engine.evaluate(media_path=clip, plan=plan)

    assert report.status == QAStatus.BLOCK
    assert report.publish_allowed is False
    assert any("non-existent or stale asset" in f.message.lower() for f in report.findings)


# ------------------------------------------------------------------
# 13. Duplicate Output Detection Across Different Jobs
# ------------------------------------------------------------------
def test_adversarial_13_duplicate_output_detection(tmp_path):
    clip = synthesize_test_clip(tmp_path / "clip.mp4", width=1080, height=1920, duration=1.0)

    # Mock DBManager with get_job_by_output_checksum
    class MockDB:
        def get_job_by_output_checksum(self, checksum):
            return {"job_id": "prior-job-different-topic"}

    engine = QAEngine(CONFIG)
    report = engine.evaluate(media_path=clip, job_id="current-job-new", db_manager=MockDB())

    dup_check = next((c for c in report.checks if c.check_id == "check-duplicate-output"), None)
    assert dup_check is not None
    assert dup_check.status == QAStatus.WARN
    assert any("identical rendered output sha-256 matches prior job" in f.message.lower() for f in dup_check.findings)
