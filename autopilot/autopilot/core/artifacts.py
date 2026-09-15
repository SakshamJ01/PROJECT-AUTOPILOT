"""Artifact convention — Phase 0.
Every job gets a deterministic directory under artifacts/.
"""
from __future__ import annotations
from pathlib import Path
from autopilot.core.config import CONFIG


def job_artifact_dir(job_id: str, base_dir: Path | None = None) -> Path:
    root = Path(base_dir) if base_dir else CONFIG.get_artifacts_dir()
    d = root / "jobs" / job_id
    d.mkdir(parents=True, exist_ok=True)
    # Subdirectories
    for sub in ("media", "script", "assets", "provenance", "render", "thumbnails", "quality", "publish", "voice"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def media_path(job_id: str, filename: str, base_dir: Path | None = None) -> Path:
    return job_artifact_dir(job_id, base_dir) / "media" / filename


def script_path(job_id: str, filename: str = "script.json", base_dir: Path | None = None) -> Path:
    return job_artifact_dir(job_id, base_dir) / "script" / filename


def provenance_path(job_id: str, filename: str = "provenance.json", base_dir: Path | None = None) -> Path:
    return job_artifact_dir(job_id, base_dir) / "provenance" / filename


def render_path(job_id: str, filename: str = "output.mp4", base_dir: Path | None = None) -> Path:
    return job_artifact_dir(job_id, base_dir) / "render" / filename


def publish_path(job_id: str, filename: str = "receipt.json", base_dir: Path | None = None) -> Path:
    return job_artifact_dir(job_id, base_dir) / "publish" / filename

