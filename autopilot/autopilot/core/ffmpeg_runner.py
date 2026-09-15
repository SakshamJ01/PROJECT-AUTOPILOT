"""FFmpeg-safe execution layer — Phase 4 / M4."""
from __future__ import annotations
import subprocess
import hashlib
import json
from pathlib import Path

class FFmpegRunner:
    def __init__(self, timeout: int = 300):
        self.timeout = timeout

    def run(self, cmd: list[str], cwd: str | None = None) -> dict:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout, cwd=cwd)
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "cmd": cmd,
        }

    def version(self) -> str:
        try:
            r = self.run(["ffmpeg", "-version"])
            if r["returncode"] == 0:
                return r["stdout"].splitlines()[0]
        except Exception:
            pass
        return "unknown"
