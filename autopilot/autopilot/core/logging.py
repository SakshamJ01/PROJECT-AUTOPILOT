"""Structured JSON logging — Phase 0."""
from __future__ import annotations
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autopilot.core.config import CONFIG

# Windows forbids these characters in filenames; control codes are rejected too.
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Windows reserved device names — still reserved when followed by an extension,
# so a log named "CON.jsonl" would be unusable on Windows.
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
_SAFE_LOG_NAME_FALLBACK = "job"
_MAX_LOG_NAME_LENGTH = 128


def sanitize_log_filename(job_id: str) -> str:
    """Return a Windows-safe, deterministic filename stem for a job's JSONL log.

    Only the on-disk filename is affected — the original ``job_id`` is preserved
    verbatim inside every JSON log entry. Applied on every platform so that logs
    produced on Linux/macOS remain valid when synced to Windows.
    """
    stem = _INVALID_FILENAME_CHARS.sub("_", str(job_id))
    # Windows silently strips trailing dots/spaces, which can cause collisions.
    stem = stem.strip(" .")
    if not stem:
        stem = _SAFE_LOG_NAME_FALLBACK
    if stem.upper() in _WINDOWS_RESERVED_NAMES:
        stem = f"_{stem}"
    return stem[:_MAX_LOG_NAME_LENGTH]


class StructuredLogger:
    def __init__(self, job_id: str | None = None, stage: str = "system"):
        self.job_id = job_id or "system"
        self.stage = stage
        safe_name = sanitize_log_filename(self.job_id)
        self.log_file = CONFIG.get_artifacts_dir() / "logs" / f"{safe_name}.jsonl"
        self.log_file.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, level: str, event: str, details: dict[str, Any] = None, error: str | None = None) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "job_id": self.job_id,
            "stage": self.stage,
            "level": level,
            "event": event,
        }
        if error:
            entry["error"] = error
        if details:
            entry["details"] = details
        from autopilot.bridge.protocol import redact_sensitive
        entry = redact_sensitive(entry)
        line = json.dumps(entry, default=str)
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        # Also emit to stdout for CLI visibility
        sys.stdout.write(line + "\n")

    def info(self, event: str, details: dict[str, Any] = None) -> None:
        self._write("INFO", event, details)

    def warning(self, event: str, details: dict[str, Any] = None) -> None:
        self._write("WARNING", event, details)

    def error(self, event: str, error: str, details: dict[str, Any] = None) -> None:
        self._write("ERROR", event, details, error=error)

    def debug(self, event: str, details: dict[str, Any] = None) -> None:
        self._write("DEBUG", event, details)


# Default system logger
SYSTEM_LOGGER = StructuredLogger(job_id="system", stage="init")
