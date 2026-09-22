"""Artifact convention — Phase 0.
Every job gets a deterministic directory under artifacts/.
Windows‑safe directory names are mandatory; job_id itself is preserved
unchanged in SQLite, DB records, logs and metadata.
"""
from __future__ import annotations
import re
from pathlib import Path
from autopilot.core.config import CONFIG

# ---------------------------------------------------------------------------
# Windows‑safe filename characters (same regex as the logging sanitizer)
# ---------------------------------------------------------------------------
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Windows reserved device names — a directory named "CON.jsonl" would be
# unusable on Windows even though "CON" is a valid bare name.
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
_SAFE_ARTIFACT_DIR_FALLBACK = "job"
_MAX_ARTIFACT_DIR_LENGTH = 128


def sanitize_artifact_dirname(job_id: str) -> str:
    """Return a deterministic Windows‑safe directory name from a job_id.

    Only the on‑disk directory component is affected. The original ``job_id``
    is preserved verbatim inside every JSON log entry, SQLite row, and API
    response.
    """
    stem = _INVALID_FILENAME_CHARS.sub("_", str(job_id))
    # Windows silently strips trailing dots/spaces, which can cause collisions.
    stem = stem.strip(" .")
    if not stem:
        stem = _SAFE_ARTIFACT_DIR_FALLBACK
    if stem.upper() in _WINDOWS_RESERVED_NAMES:
        stem = f"_{stem}"
    return stem[:_MAX_ARTIFACT_DIR_LENGTH]


def job_artifact_dir(job_id: str, base_dir: Path | None = None) -> Path:
    root = Path(base_dir) if base_dir else CONFIG.get_artifacts_dir()
    safe_name = sanitize_artifact_dirname(job_id)
    d = root / "jobs" / safe_name
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

