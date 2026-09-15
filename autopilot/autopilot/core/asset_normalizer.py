"""Asset Normalization Engine — Standardizes image and video assets into vertical 9:16 format.
Uses Pillow for images and FFmpeg subprocess with safe arguments for video.
Raw source artifacts are preserved untouched.
"""
from __future__ import annotations
import subprocess
import shutil
from pathlib import Path
from typing import Optional, Dict, Any

from autopilot.core.config import CONFIG
from autopilot.core.asset_cache import compute_file_sha256
from autopilot.core.ffmpeg_runner import FFmpegRunner


def normalize_image(
    src_path: str | Path,
    out_path: str | Path,
    target_width: Optional[int] = None,
    target_height: Optional[int] = None,
    strategy: str = "crop",  # crop | fit | pad
) -> Dict[str, Any]:
    """Normalize an image to target aspect ratio and resolution using Pillow."""
    from PIL import Image, ImageOps, ImageFilter

    src = Path(src_path)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    tw = target_width or CONFIG.asset_target_width
    th = target_height or CONFIG.asset_target_height

    if not src.exists() or src.stat().st_size == 0:
        raise FileNotFoundError(f"Source image not found or empty: {src}")

    with Image.open(src) as img:
        # Auto-orient based on EXIF tags
        img = ImageOps.exif_transpose(img)
        # Convert to RGB (or RGBA)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")

        orig_w, orig_h = img.size
        orig_ar = orig_w / max(1, orig_h)
        target_ar = tw / max(1, th)

        if strategy == "crop":
            # Scale so image completely covers target canvas, then center crop
            scale = max(tw / orig_w, th / orig_h)
            new_w = int(round(orig_w * scale))
            new_h = int(round(orig_h * scale))
            resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            left = (new_w - tw) // 2
            top = (new_h - th) // 2
            final_img = resized.crop((left, top, left + tw, top + th))
        elif strategy == "pad":
            # Scale to fit inside target canvas, pad with black
            scale = min(tw / orig_w, th / orig_h)
            new_w = int(round(orig_w * scale))
            new_h = int(round(orig_h * scale))
            resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            final_img = Image.new("RGB", (tw, th), (0, 0, 0))
            left = (tw - new_w) // 2
            top = (th - new_h) // 2
            final_img.paste(resized, (left, top))
        elif strategy == "fit":
            # Blurred background + scaled foreground
            # 1. Background: scaled to cover canvas and blurred
            bg_scale = max(tw / orig_w, th / orig_h)
            bg_w = int(round(orig_w * bg_scale))
            bg_h = int(round(orig_h * bg_scale))
            bg = img.resize((bg_w, bg_h), Image.Resampling.BILINEAR)
            bg_left = (bg_w - tw) // 2
            bg_top = (bg_h - th) // 2
            final_img = bg.crop((bg_left, bg_top, bg_left + tw, bg_top + th)).filter(ImageFilter.GaussianBlur(radius=20))
            # 2. Foreground: scaled to fit and centered
            fg_scale = min(tw / orig_w, th / orig_h)
            fg_w = int(round(orig_w * fg_scale))
            fg_h = int(round(orig_h * fg_scale))
            fg = img.resize((fg_w, fg_h), Image.Resampling.LANCZOS)
            fg_left = (tw - fg_w) // 2
            fg_top = (th - fg_h) // 2
            final_img.paste(fg, (fg_left, fg_top))
        else:
            raise ValueError(f"Unsupported normalization strategy: {strategy}")

        # Save to output
        if out.suffix.lower() in (".jpg", ".jpeg"):
            final_img.save(str(out), "JPEG", quality=92)
        else:
            final_img.save(str(out), "PNG")

    sha256 = compute_file_sha256(out)
    return {
        "output_path": str(out.resolve()),
        "width": tw,
        "height": th,
        "aspect_ratio": "9:16",
        "strategy": strategy,
        "original_dimensions": {"width": orig_w, "height": orig_h},
        "checksum_sha256": sha256,
        "format": out.suffix.lower().replace(".", ""),
    }


def normalize_video(
    src_path: str | Path,
    out_path: str | Path,
    target_width: Optional[int] = None,
    target_height: Optional[int] = None,
    fps: int = 25,
) -> Dict[str, Any]:
    """Normalize a video file to target 9:16 dimensions using FFmpeg."""
    src = Path(src_path)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    tw = target_width or CONFIG.asset_target_width
    th = target_height or CONFIG.asset_target_height

    if not src.exists() or src.stat().st_size == 0:
        raise FileNotFoundError(f"Source video not found or empty: {src}")

    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-vf", f"scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},format=yuv420p",
        "-r", str(fps),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        str(out),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg normalization failed: {result.stderr}")

    sha256 = compute_file_sha256(out)
    return {
        "output_path": str(out.resolve()),
        "width": tw,
        "height": th,
        "aspect_ratio": "9:16",
        "fps": fps,
        "checksum_sha256": sha256,
        "format": "mp4",
    }


def normalize_asset(
    src_path: str | Path,
    out_path: str | Path,
    asset_type: str = "image",
    strategy: str = "crop",
    **kwargs,
) -> str:
    """Entrypoint: Normalize either image or video asset."""
    src = Path(src_path)
    atype = asset_type.lower()
    if atype == "image" or src.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
        res = normalize_image(src, out_path, strategy=strategy, **kwargs)
    elif atype == "video" or src.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm"):
        res = normalize_video(src, out_path, **kwargs)
    else:
        # Fallback: copy directly
        shutil.copy2(str(src), str(out_path))
        return str(Path(out_path).resolve())
    return res["output_path"]
