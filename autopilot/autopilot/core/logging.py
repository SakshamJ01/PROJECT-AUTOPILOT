"""Structured JSON logging — Phase 0."""
from __future__ import annotations
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autopilot.core.config import CONFIG


class StructuredLogger:
    def __init__(self, job_id: str | None = None, stage: str = "system"):
        self.job_id = job_id or "system"
        self.stage = stage
        self.log_file = CONFIG.get_artifacts_dir() / "logs" / f"{self.job_id}.jsonl"
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
