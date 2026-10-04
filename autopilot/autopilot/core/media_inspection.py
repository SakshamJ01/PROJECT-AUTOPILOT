"""Safe media download + inspection utilities — Phase 3 / M3."""
from __future__ import annotations
import subprocess, hashlib, json
from pathlib import Path


# A downloaded asset is rejected when essentially every sampled frame is this
# dark. Real footage — even a night or deep-sea shot — keeps a mean luma well
# above this; broken or placeholder downloads sit near zero.
BLACK_LUMA_THRESHOLD = 16.0
BLACK_FRAME_RATIO_THRESHOLD = 0.95


def measure_visual_validity(
    path: str | Path,
    samples: int = 16,
    scale: int = 32,
    black_luma_threshold: float = BLACK_LUMA_THRESHOLD,
    black_ratio_threshold: float = BLACK_FRAME_RATIO_THRESHOLD,
) -> dict:
    """Sample frames across a clip and detect assets that are effectively black.

    Stream/codec inspection cannot see this class of defect: a fully black
    download is a perfectly valid H.264 file, so it passes every container
    check and then renders as a black scene.
    """
    result = {
        "checked": False,
        "is_black": False,
        "frames_sampled": 0,
        "mean_luma": None,
        "black_frame_ratio": None,
        "luma_stddev": None,
        "error": None,
    }
    p = Path(path)
    if not p.exists():
        result["error"] = "missing"
        return result

    try:
        dur_cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(p),
        ]
        dur_res = subprocess.run(dur_cmd, capture_output=True, text=True, timeout=15)
        duration = float(dur_res.stdout.strip()) if dur_res.returncode == 0 else 0.0
        if duration <= 0:
            result["error"] = "no_duration"
            return result

        # Sample uniformly across the whole clip so a black tail is caught too.
        step = max(duration / max(1, samples), 1e-3)
        cmd = [
            "ffmpeg", "-v", "error", "-i", str(p),
            "-vf", f"fps={1.0 / step:.6f},scale={scale}:{scale}",
            "-frames:v", str(max(1, samples * 2)),
            "-f", "rawvideo", "-pix_fmt", "gray", "-",
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=60)
        if r.returncode != 0 or not r.stdout:
            result["error"] = f"decode_failed: {r.stderr.decode('utf-8', 'ignore')[:120]}"
            return result

        import numpy as np

        raw = np.frombuffer(r.stdout, dtype=np.uint8)
        n = raw.size // (scale * scale)
        if n == 0:
            result["error"] = "no_frames"
            return result
        frame_luma = raw[: n * scale * scale].reshape(n, scale * scale).mean(axis=1)

        black_ratio = float((frame_luma < black_luma_threshold).mean())
        result.update({
            "checked": True,
            "frames_sampled": int(n),
            "mean_luma": round(float(frame_luma.mean()), 2),
            "luma_stddev": round(float(frame_luma.std()), 2),
            "black_frame_ratio": round(black_ratio, 4),
            "duration_sec": round(duration, 3),
            "is_black": bool(black_ratio >= black_ratio_threshold),
        })
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


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

        # A black/blank download is structurally valid but renders as a black
        # scene, so it must be rejected here rather than shipped.
        if v_stream:
            validity = measure_visual_validity(p)
            result["visual_validity"] = validity
            if validity.get("is_black"):
                result["valid"] = False
                result["errors"].append(
                    "Asset is effectively black/blank: "
                    f"{validity.get('black_frame_ratio'):.0%} of {validity.get('frames_sampled')} "
                    f"sampled frames below luma {BLACK_LUMA_THRESHOLD} "
                    f"(mean {validity.get('mean_luma')})"
                )
    except Exception as exc:
        result["errors"].append(str(exc))
    return result

def compute_checksum(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
