"""Safe media download + inspection utilities — Phase 3 / M3."""
from __future__ import annotations
import subprocess, hashlib, json
from pathlib import Path

def inspect_media(path: str | Path) -> dict:
    result = {"valid": False, "path": str(path), "errors": []}
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        result["errors"].append("Zero bytes or missing")
        return result
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height,duration,avg_frame_rate", "-show_entries", "format=duration,size", "-of", "json", str(p)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            result["errors"].append(f"FFprobe failed: {r.stderr}")
            return result
        data = json.loads(r.stdout)
        stream = data.get("streams", [{}])[0]
        fmt = data.get("format", {})
        result["valid"] = True
        result["codec"] = stream.get("codec_name")
        result["width"] = stream.get("width")
        result["height"] = stream.get("height")
        result["duration_sec"] = float(fmt.get("duration", 0) or 0)
        result["file_size_bytes"] = fmt.get("size")
    except Exception as exc:
        result["errors"].append(str(exc))
    return result

def compute_checksum(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
