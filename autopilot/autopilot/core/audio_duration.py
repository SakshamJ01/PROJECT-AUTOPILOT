"""Audio duration extraction — Phase 2.
Uses FFmpeg/FFprobe for media truth; never trusts estimated word count alone.
"""
from __future__ import annotations
import subprocess
import json
from pathlib import Path


def extract_duration(path: str | Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-show_entries", "stream=codec_name,sample_rate,channels",
        "-of", "json",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return {"valid": False, "error": result.stderr, "duration_sec": 0.0}
    data = json.loads(result.stdout)
    fmt = data.get("format", {})
    streams = data.get("streams", [{}])[0]
    duration = float(fmt.get("duration", 0) or 0)
    return {
        "valid": duration > 0,
        "duration_sec": round(duration, 3),
        "codec": streams.get("codec_name"),
        "sample_rate": streams.get("sample_rate"),
        "channels": streams.get("channels"),
        "path": str(path),
    }
