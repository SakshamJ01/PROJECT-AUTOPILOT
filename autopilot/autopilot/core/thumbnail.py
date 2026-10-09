"""Custom thumbnail generation (Phase 4.1).

YouTube picks the first frame of the upload unless a custom thumbnail is set.
The first frame of these shorts is an arbitrary mid-fade frame from the render,
which is a poor browse-surface: it has no hook text and no brand cue.

This module builds a deterministic 1080x1920 thumbnail from the strongest
scene of the render (highest real CLIP similarity, i.e. the frame whose pixels
genuinely matched the scene's visual requirement), overlaid with the video
hook and the channel badge.

Design constraints:
  * Deterministic: no randomness, no network, no model inference. The scene
    choice comes from evidence already measured during asset acquisition.
  * Fail-soft: a thumbnail must never block or fail publication. Every
    failure returns ``None`` so the caller simply publishes without one and
    YouTube keeps its own default frame.
  * Uses the already-rendered, already-provenance-verified scene assets, so
    the thumbnail cannot introduce rights or provenance questions that the
    render QA did not already clear.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

THUMB_WIDTH = 1080
THUMB_HEIGHT = 1920

# Overlay tuning. The hook must dominate the browse surface, but the scene has
# to stay legible, so the scrim is a gradient rather than a flat wash.
_SCRIM_TOP_ALPHA = 210
_SCRIM_BOTTOM_ALPHA = 60
_BADGE_HEIGHT = 150
_HOOK_MAX_CHARS = 44
_HOOK_MAX_LINES = 3
_FONT_CANDIDATES_BOLD = (
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)
_ACCENT = (255, 215, 0)  # brand secondary gold
_TEXT = (255, 255, 255)
_SHADOW = (0, 0, 0)


def _load_font(size: int) -> ImageFont.ImageFont:
    for candidate in _FONT_CANDIDATES_BOLD:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _probe_frame(video_path: Path, at_sec: float) -> Optional[Image.Image]:
    """Extract a single frame from a video asset as a PIL image."""
    try:
        cmd = [
            "ffmpeg", "-y", "-ss", f"{max(at_sec, 0.0):.3f}",
            "-i", str(video_path), "-frames:v", "1",
            "-f", "image2pipe", "-vcodec", "png", "-",
        ]
        res = subprocess.run(cmd, capture_output=True)
        if res.returncode != 0 or not res.stdout:
            return None
        import io

        return Image.open(io.BytesIO(res.stdout)).convert("RGB")
    except Exception:
        return None


def _load_image(source: Path) -> Optional[Image.Image]:
    """Load an image asset, or a representative frame if it is a video."""
    ext = source.suffix.lower()
    if ext in (".mp4", ".mov", ".mkv", ".webm"):
        return _probe_frame(source, 0.5)
    try:
        return Image.open(source).convert("RGB")
    except Exception:
        return None


def _cover_fit(img: Image.Image, width: int, height: int) -> Image.Image:
    """Scale+crop to fully cover the target box, preserving aspect ratio."""
    src_w, src_h = img.size
    if src_w <= 0 or src_h <= 0:
        return Image.new("RGB", (width, height), (12, 12, 16))
    scale = max(width / src_w, height / src_h)
    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - width) // 2
    top = (new_h - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _wrap_hook(text: str, font: ImageFont.ImageFont, max_width: int) -> List[str]:
    """Greedy word wrap constrained by both a char budget and pixel width."""
    words = [w for w in str(text or "").split() if w]
    if not words:
        return []
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        too_wide = False
        try:
            too_wide = font.getbbox(candidate)[2] > max_width
        except Exception:
            too_wide = len(candidate) * 20 > max_width
        if (too_wide or len(candidate) > _HOOK_MAX_CHARS) and current:
            lines.append(current)
            current = word
            if len(lines) == _HOOK_MAX_LINES:
                break
        else:
            current = candidate
    if current and len(lines) < _HOOK_MAX_LINES:
        lines.append(current)

    # Hard character budget on the last line so an over-long hook cannot grow
    # past the safe area.
    if lines:
        while len(lines) > 1 and len(lines[-1]) > _HOOK_MAX_CHARS:
            overflow = len(lines[-1]) - _HOOK_MAX_CHARS
            lines[-2] = lines[-2] + " " + lines[-1][: max(0, len(lines[-1]) - overflow)]
            lines[-1] = lines[-1][-max(1, _HOOK_MAX_CHARS):]
    return lines


def _apply_vertical_scrim(img: Image.Image) -> Image.Image:
    """Darken top and bottom so overlaid text always has contrast."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    height = img.size[1]
    for y in range(height):
        if y < _BADGE_HEIGHT * 3:
            t = y / float(_BADGE_HEIGHT * 3)
        elif y > height - _BADGE_HEIGHT * 3:
            t = (height - y) / float(_BADGE_HEIGHT * 3)
        else:
            t = 0.0
        t = max(0.0, min(1.0, t))
        alpha = int(_SCRIM_BOTTOM_ALPHA + (_SCRIM_TOP_ALPHA - _SCRIM_BOTTOM_ALPHA) * t)
        draw.line([(0, y), (img.size[0], y)], fill=(0, 0, 0, alpha))
    return Image.alpha_composite(img.convert("RGBA"), overlay)


def _draw_badge(draw: ImageDraw.ImageDraw, channel_name: str) -> None:
    """Draw the channel badge bar at the top of the frame."""
    name = (channel_name or "").strip() or "FACTS"
    font = _load_font(54)
    try:
        bbox = font.getbbox(name)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
    except Exception:
        text_w, text_h = len(name) * 30, 54
    pad_x, pad_y = 34, 20
    bar_w = min(THUMB_WIDTH - 60, text_w + pad_x * 2 + 46)
    bar_h = text_h + pad_y * 2

    draw.rounded_rectangle(
        [40, 44, 40 + bar_w, 44 + bar_h],
        radius=bar_h // 2,
        fill=(0, 0, 0, 190),
        outline=_ACCENT,
        width=4,
    )
    # Accent dot as a brand mark before the name.
    dot_r = 13
    dot_cx = 40 + pad_x + dot_r
    dot_cy = 44 + bar_h // 2
    draw.ellipse(
        [dot_cx - dot_r, dot_cy - dot_r, dot_cx + dot_r, dot_cy + dot_r],
        fill=_ACCENT,
    )
    draw.text(
        (dot_cx + dot_r + 16, dot_cy),
        name,
        font=font,
        fill=_TEXT,
        anchor="lm",
        stroke_width=2,
        stroke_fill=_SHADOW,
    )


def _draw_hook(draw: ImageDraw.ImageDraw, hook: str) -> None:
    """Draw the hook text, anchored in the lower safe area of a Shorts frame."""
    font = _load_font(96)
    max_width = THUMB_WIDTH - 120
    lines = _wrap_hook(hook, font, max_width)
    if not lines:
        return
    line_h = 116
    block_h = line_h * len(lines)
    top = THUMB_HEIGHT - _BADGE_HEIGHT - block_h - 90
    if top < _BADGE_HEIGHT + 40:
        top = _BADGE_HEIGHT + 40
    for i, line in enumerate(lines):
        y = top + i * line_h
        draw.text(
            (THUMB_WIDTH // 2, y),
            line,
            font=font,
            fill=_TEXT,
            anchor="ma",
            stroke_width=8,
            stroke_fill=_SHADOW,
        )
    # Accent rule under the hook for visual separation from the scrim edge.
    rule_y = top + block_h + 18
    draw.rounded_rectangle(
        [THUMB_WIDTH // 2 - 130, rule_y, THUMB_WIDTH // 2 + 130, rule_y + 12],
        radius=6,
        fill=_ACCENT,
    )


def select_thumbnail_source(
    scenes: List[Dict[str, Any]],
    job_dir: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Pick the strongest scene to source the thumbnail frame from.

    "Strongest" means the highest real CLIP similarity that was measured on the
    downloaded pixels during asset acquisition (``semantic_score`` on the render
    scene, mirrored from ``AssetArtifact.semantic_score``). Scenes without
    evidence are still eligible, but never outrank a scene that has evidence.
    """
    best: Optional[Dict[str, Any]] = None
    best_key: Optional[Tuple[int, float, int]] = None

    for idx, scene in enumerate(scenes or []):
        if not isinstance(scene, dict):
            continue
        path = scene.get("asset_path") or scene.get("normalized_path")
        if not path:
            continue
        p = Path(str(path))
        if not p.exists() or p.stat().st_size == 0:
            continue
        raw_score = scene.get("semantic_score")
        try:
            score = float(raw_score) if raw_score is not None else None
        except Exception:
            score = None
        # Rank: has-evidence first, then score, then earliest (stable).
        key = (1 if score is not None else 0, score if score is not None else 0.0, -idx)
        if best_key is None or key > best_key:
            best_key = key
            best = {
                "scene_id": scene.get("scene_id"),
                "asset_path": str(p),
                "semantic_score": score,
                "asset_type": scene.get("asset_type"),
                "index": idx,
            }
    return best


def _generative_thumbnail_backend_available() -> bool:
    """True when the optional Gemini image backend can be used."""
    try:
        from google import genai  # noqa: F401
        from autopilot.core.config import CONFIG
        import os

        return bool(
            getattr(CONFIG, "thumbnail_generative", False)
            and (
                getattr(CONFIG, "gemini_api_key", None)
                or os.environ.get("GEMINI_API_KEY")
            )
        )
    except Exception:
        return False


def _generate_generative_thumbnail(
    hook: str,
    channel_name: str,
    out_path: Path,
    model: Optional[str] = None,
) -> Optional[Path]:
    """Generate a thumbnail with Gemini image model (lazy, fail-soft)."""
    try:
        import os
        import io
        from google import genai
        from google.genai import types as genai_types
        from autopilot.core.config import CONFIG

        api_key = getattr(CONFIG, "gemini_api_key", None) or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return None
        model_name = model or getattr(
            CONFIG, "thumbnail_generative_model", "gemini-2.0-flash-preview-image-generation"
        )
        client = genai.Client(api_key=api_key)

        prompt = (
            "Vertical 9:16 YouTube Shorts thumbnail, bold and high-contrast. "
            f"{(hook or channel_name or 'attention-grabbing subject').strip()}. "
            "Cinematic, vibrant, no text overlay, no watermarks."
        )
        resp = client.models.generate_images(
            model=model_name,
            prompt=prompt,
            config=genai_types.GenerateImagesConfig(
                aspect_ratio="9:16", number_of_images=1
            ),
        )
        images = getattr(resp, "generated_images", None) or []
        if not images:
            return None
        image_bytes = images[0].image.image_bytes
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Normalize to JPEG to match the publisher contract.
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        img = img.resize((THUMB_WIDTH, THUMB_HEIGHT))
        img.save(out_path, format="JPEG", quality=92, subsampling=0)
        return out_path
    except Exception:
        return None


def generate_thumbnail(
    render_scenes: List[Dict[str, Any]],
    job_dir: Path,
    hook: str = "",
    channel_name: str = "",
    filename: str = "thumb.jpg",
) -> Optional[Path]:
    """Render the custom thumbnail for a job. Returns None on any failure.

    Fail-soft by contract: a thumbnail is a nice-to-have and must never block
    publication, so every failure mode degrades to "publish without one".

    When the generative Gemini backend is enabled (Round-2 upgrade #7) it is
    tried first; on any failure the deterministic PIL composite below is used.
    """
    out_dir = Path(job_dir) / "thumbnails"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / filename

    if _generative_thumbnail_backend_available():
        generated = _generate_generative_thumbnail(hook, channel_name, out_path)
        if generated is not None:
            return generated

    try:
        chosen = select_thumbnail_source(render_scenes)
        if not chosen:
            return None
        source = Path(chosen["asset_path"])
        base = Image.open(source) if source.suffix.lower() not in (".mp4", ".mov", ".mkv", ".webm") else None
        if base is None:
            base = _probe_frame(source, 0.5)
        if base is None:
            return None

        canvas = _cover_fit(base.convert("RGB"), THUMB_WIDTH, THUMB_HEIGHT)
        canvas = _apply_vertical_scrim(canvas)
        draw = ImageDraw.Draw(canvas)

        _draw_badge(draw, channel_name)
        _draw_hook(draw, hook or "")

        canvas.convert("RGB").save(out_path, format="JPEG", quality=92, subsampling=0)
        return out_path
    except Exception:
        return None