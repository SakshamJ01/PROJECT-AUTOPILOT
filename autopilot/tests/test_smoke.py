"""Synthetic smoke test — Phase 0.
Generates a small 9:16 video with FFmpeg, runs workflow, verifies artifacts.
No external APIs or ML models required.
"""
from __future__ import annotations
import subprocess
import json
import hashlib
import hashlib
from pathlib import Path

from autopilot.core.config import CONFIG
from autopilot.core.state_machine import WorkflowState, validate_transition
from autopilot.core.artifacts import render_path, script_path, media_path, job_artifact_dir
from autopilot.db.manager import DBManager
from autopilot.core.logging import StructuredLogger

SMOKE_JOB_ID = "smoke-phase0-001"
SMOKE_TOPIC = "Synthetic smoke test for Phase 0"


def generate_synthetic_video(job_id: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # 9:16, 2 seconds, test card + sine audio, 720x1280
    cmd = [
        "ffmpeg",
        "-y",
        "-f", "lavfi",
        "-i", "testsrc=duration=2:size=720x1280:rate=25",
        "-f", "lavfi",
        "-i", "sine=frequency=1000:duration=2",
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "28",
        "-c:a", "aac",
        "-b:a", "32k",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {result.stderr}\nCommand: {cmd}")


def verify_with_ffprobe(video_path: Path) -> dict:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height,avg_frame_rate,duration",
        "-of", "json",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return {"valid": False, "error": result.stderr}
    data = json.loads(result.stdout)
    stream = data.get("streams", [{}])[0]
    return {
        "valid": True,
        "codec": stream.get("codec_name"),
        "width": stream.get("width"),
        "height": stream.get("height"),
        "frame_rate": stream.get("avg_frame_rate"),
        "duration": stream.get("duration"),
    }


def run_smoke_workflow() -> dict:
    logger = StructuredLogger(job_id=SMOKE_JOB_ID, stage="smoke")
    db = DBManager(CONFIG.db_path)
    db.init_schema()

    # Initialize job
    db.create_job(SMOKE_JOB_ID, channel_id="smoke", topic=SMOKE_TOPIC, idempotency_key="smoke-phase0-key-001")
    logger.info("smoke_started", details={"job_id": SMOKE_JOB_ID, "topic": SMOKE_TOPIC})

    # Workflow transitions (explicit)
    transitions = [
        (WorkflowState.IDEA, WorkflowState.RESEARCHING),
        (WorkflowState.RESEARCHING, WorkflowState.RESEARCHED),
        (WorkflowState.RESEARCHED, WorkflowState.SCRIPTING),
        (WorkflowState.SCRIPTING, WorkflowState.SCRIPTED),
        (WorkflowState.SCRIPTED, WorkflowState.ASSET_PREPARING),
        (WorkflowState.ASSET_PREPARING, WorkflowState.ASSETS_READY),
        (WorkflowState.ASSETS_READY, WorkflowState.VOICING),
        (WorkflowState.VOICING, WorkflowState.VOICE_READY),
        (WorkflowState.VOICE_READY, WorkflowState.EDITING),
        (WorkflowState.EDITING, WorkflowState.RENDERING),
        (WorkflowState.RENDERING, WorkflowState.RENDERED),
    ]

    current = WorkflowState.IDEA
    for from_s, to_s in transitions:
        if current != from_s:
            raise RuntimeError(f"Expected state {from_s.value} but got {current.value}")
        validate_transition(from_s, to_s)
        db.update_job_status(SMOKE_JOB_ID, to_s.value)
        db.log_event(SMOKE_JOB_ID, from_s.value, to_s.value, reason=f"smoke_{to_s.value.lower()}")
        current = to_s
        logger.info("transition", details={"from": from_s.value, "to": to_s.value})

    # Write synthetic script JSON
    script = {
        "job_id": SMOKE_JOB_ID,
        "topic": SMOKE_TOPIC,
        "hook": "Synthetic test for Phase 0",
        "scenes": [{"duration_sec": 2, "visual_intent": "test card", "narration": "Smoke test audio."}],
    }
    sp = script_path(SMOKE_JOB_ID)
    sp.write_text(json.dumps(script, indent=2), encoding="utf-8")
    db.record_artifact(SMOKE_JOB_ID, str(sp), "script", checksum=hashlib.sha256(str(sp.read_text()).encode()).hexdigest()[:32])

    # Generate synthetic video
    render_p = render_path(SMOKE_JOB_ID, "smoke_output.mp4")
    generate_synthetic_video(SMOKE_JOB_ID, render_p)
    db.record_artifact(SMOKE_JOB_ID, str(render_p), "media")
    logger.info("render_complete", details={"render_path": str(render_p)})

    # Verify with ffprobe
    probe = verify_with_ffprobe(render_p)
    logger.info("ffprobe_verify", details=probe)
    if not probe["valid"]:
        raise RuntimeError(f"FFprobe verification failed: {probe.get('error')}")

    # Check artifact directory convention
    dir_check = job_artifact_dir(SMOKE_JOB_ID)
    expected_subs = {"media", "script", "assets", "provenance", "render", "thumbnails"}
    missing = expected_subs - {p.name for p in dir_check.iterdir()}
    if missing:
        raise RuntimeError(f"Artifact directories missing: {missing}")

    # Verify second run idempotency (same key should not corrupt)
    db.create_job(SMOKE_JOB_ID, channel_id="smoke", topic=SMOKE_TOPIC, idempotency_key="smoke-phase0-key-001")
    job_row = db.get_job(SMOKE_JOB_ID)
    if job_row is None or job_row["job_id"] != SMOKE_JOB_ID:
        raise RuntimeError("Job persistence failed after idempotent re-insert")

    # Log success
    logger.info("smoke_complete", details={"status": "passed", "video_path": str(render_p), "probe": probe})
    return {"status": "passed", "job_id": SMOKE_JOB_ID, "render_path": str(render_p), "probe": probe, "state_final": current.value}


if __name__ == "__main__":
    result = run_smoke_workflow()
    print(json.dumps(result, indent=2))
