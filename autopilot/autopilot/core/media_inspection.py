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
        cmd = ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,codec_type,width,height,duration,avg_frame_rate", "-show_entries", "format=duration,size,format_name", "-of", "json", str(p)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            result["errors"].append(f"FFprobe failed: {r.stderr}")
            return result
        data = json.loads(r.stdout)
        streams = data.get("streams", [])
        stream = streams[0] if streams else {}
        v_stream = next((s for s in streams if s.get("codec_type") == "video"), stream)
        a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
        fmt = data.get("format", {})
        codec = v_stream.get("codec_name") or stream.get("codec_name")
        if codec in ("svg", "svg_pipe"):
            result["errors"].append("Vector SVG format unsupported; raster image required")
            return result
        dur = float(fmt.get("duration", 0) or v_stream.get("duration", 0) or 0)
        result["valid"] = True
        result["codec"] = codec
        result["width"] = v_stream.get("width")
        result["height"] = v_stream.get("height")
        result["duration_sec"] = dur
        result["file_size_bytes"] = fmt.get("size")
        result["format"] = fmt
        result["video"] = v_stream
        result["audio"] = a_stream
    except Exception as exc:
        result["errors"].append(str(exc))
    return result

def compute_checksum(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
