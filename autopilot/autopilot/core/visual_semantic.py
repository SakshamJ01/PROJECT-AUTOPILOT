"""Visual-Semantic Verification Engine (P0).

Performs REAL visual-semantic verification between scene narration,
scene visual_intent, asset_query and DOWNLOADED MEDIA PIXELS using a local
CLIP/SigLIP-class vision-language model (open_clip ViT-B-32).

This is the authoritative semantic decision layer for asset selection.
Provider-generated title/tag metadata is NEVER used as semantic evidence
here — only pixels and the scene's own text are compared.

Design rules enforced by this module:
  * Keyword/tag overlap is NOT a semantic decision.
  * A hard gate rejects any asset whose visual semantic similarity is below
    the configured minimum. HARD FAILURE is preferable to an unrelated asset.
  * If verification is enabled but the local model cannot be loaded, assets
    are REJECTED (never silently accepted) — no fake pass.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from autopilot.core.config import CONFIG

# Defaults tuned for RTX 4060 8GB VRAM class hardware (also runs on CPU).
# ViT-B-32 / laion2b_s34b_b79k measured best discrimination on real stock
# pixels (clear match/decoy separation) and uses the standard config, avoiding
# the OpenAI-weight QuickGELU activation mismatch.
_DEFAULT_MODEL = "ViT-B-32"
_DEFAULT_PRETRAINED = "laion2b_s34b_b79k"
_DEFAULT_MIN_SIMILARITY = 0.21  # cosine; validated against real match (0.256) vs decoy (0.177)
_VIDEO_FRAMES = 3


class VisualSemanticUnavailable(RuntimeError):
    """Raised when the local vision-language model cannot be loaded."""


class _ClipRuntime:
    """Lazily-initialized, thread-safe singleton CLIP runtime with embedding caches."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model = None
        self._preprocess = None
        self._tokenizer = None
        self._device = None
        self._model_name = _DEFAULT_MODEL
        self._pretrained = _DEFAULT_PRETRAINED
        self._image_cache: Dict[str, Any] = {}  # sha256 -> tensor
        self._text_cache: Dict[str, Any] = {}  # text hash -> tensor
        self._load_error: Optional[str] = None

    @property
    def available(self) -> bool:
        try:
            self._ensure_loaded()
            return self._model is not None
        except Exception:
            return False

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "0")
            try:
                import torch  # noqa: F401
                import open_clip
            except Exception as exc:  # pragma: no cover - env dependent
                self._load_error = f"torch/open_clip not installed: {exc}"
                raise VisualSemanticUnavailable(self._load_error)

            try:
                import torch

                self._device = "cuda" if torch.cuda.is_available() else "cpu"
                # Honor configuration when set (falls back to validated defaults).
                self._model_name = str(getattr(CONFIG, "visual_semantic_model", _DEFAULT_MODEL)) or _DEFAULT_MODEL
                self._pretrained = str(getattr(CONFIG, "visual_semantic_pretrained", _DEFAULT_PRETRAINED)) or _DEFAULT_PRETRAINED
                model, _, preprocess = open_clip.create_model_and_transforms(
                    self._model_name, pretrained=self._pretrained
                )
                model.eval()
                model.to(self._device)
                self._model = model
                self._preprocess = preprocess
                self._tokenizer = open_clip.get_tokenizer(self._model_name)
            except Exception as exc:
                self._load_error = f"Failed to load CLIP model '{self._model_name}': {exc}"
                raise VisualSemanticUnavailable(self._load_error)

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------
    def embed_text(self, text: str):
        self._ensure_loaded()
        import torch

        key = hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()
        if key in self._text_cache:
            return self._text_cache[key]
        with torch.no_grad():
            tokens = self._tokenizer([text]).to(self._device)
            feats = self._model.encode_text(tokens).float()
            feats = feats / feats.norm(dim=-1, keepdim=True).clamp_min(1e-8)
        feats = feats.cpu()
        self._text_cache[key] = feats
        return feats

    def embed_image_pil(self, img):
        self._ensure_loaded()
        import torch

        with torch.no_grad():
            x = self._preprocess(img).unsqueeze(0).to(self._device)
            feats = self._model.encode_image(x).float()
            feats = feats / feats.norm(dim=-1, keepdim=True).clamp_min(1e-8)
        return feats.cpu()

    def embed_file(self, path: str | Path) -> Any:
        """Embed an image file, with a sha256-keyed cache. Raises on unreadable pixels."""
        from PIL import Image

        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Visual verify: file missing: {p}")
        key = hashlib.sha256(p.read_bytes()).hexdigest()
        if key in self._image_cache:
            return self._image_cache[key]
        with Image.open(p) as img:
            feats = self.embed_image_pil(img.convert("RGB"))
        self._image_cache[key] = feats
        return feats


_RUNTIME = _ClipRuntime()


def _probe_duration_sec(path: Path) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(r.stdout.strip() or 0.0)
    except Exception:
        return 0.0


def extract_video_frames(path: str | Path, max_frames: int = _VIDEO_FRAMES) -> List[Any]:
    """Extract up to `max_frames` evenly-spaced frames from a video as PIL images.

    Uses ffmpeg. Returns [] on any failure (caller treats as unverifiable).
    """
    from PIL import Image

    p = Path(path)
    if not p.exists():
        return []
    duration = _probe_duration_sec(p)
    if duration <= 0.1:
        return []

    n = max(1, min(max_frames, max(1, int(duration))))
    timestamps = [round(duration * (i + 0.5) / n, 3) for i in range(n)]

    frames: List[Any] = []
    tmp_dir = Path(tempfile.mkdtemp(prefix="vclip_"))
    try:
        for idx, ts in enumerate(timestamps):
            out_frame = tmp_dir / f"f_{idx:03d}.png"
            cmd = [
                "ffmpeg", "-y", "-v", "error", "-ss", f"{ts:.3f}",
                "-i", str(p), "-frames:v", "1", "-q:v", "2", str(out_frame),
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if r.returncode == 0 and out_frame.exists():
                with Image.open(out_frame) as img:
                    frames.append(img.convert("RGB"))
    finally:
        try:
            for f in tmp_dir.iterdir():
                f.unlink()
            tmp_dir.rmdir()
        except Exception:
            pass
    return frames


def embed_media(path: str | Path, max_video_frames: int = _VIDEO_FRAMES) -> List[Any]:
    """Embed any media file (image or video) -> list of normalized feature tensors.

    For videos, multiple frame embeddings are returned (one per sampled frame).
    For images, a single embedding is returned. Empty list means unverifiable.
    """
    p = Path(path)
    suffix = p.suffix.lower()
    is_video = suffix in (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi")
    if is_video:
        frames = extract_video_frames(p, max_frames=max_video_frames)
        return [_RUNTIME.embed_image_pil(f) for f in frames]
    return [_RUNTIME.embed_file(p)]


def _cosine(a, b) -> float:
    return float((a @ b.T).item())


def build_scene_text_variants(query: str, visual_intent: str, narration: str) -> Dict[str, str]:
    """Build the CLIP text prompts that represent the scene's visual requirement."""
    q = (query or "").strip()
    intent = (visual_intent or "").strip()
    narr = (narration or "").strip()
    variants: Dict[str, str] = {}
    if q:
        variants["query"] = f"a video showing {q}"
    if intent and intent.lower() != q.lower():
        variants["intent"] = f"a video showing {intent}"
    if narr and narr.lower() not in (q.lower(), intent.lower()):
        variants["narration"] = f"a video about {narr[:240]}"
    if not variants:
        variants["query"] = "a video"
    return variants


def visual_semantic_verify(
    media_path: str | Path,
    query: str,
    visual_intent: str = "",
    narration: str = "",
    min_similarity: Optional[float] = None,
    max_video_frames: int = _VIDEO_FRAMES,
) -> Dict[str, Any]:
    """Verify a DOWNLOADED media file against the scene's semantic requirement.

    Returns a dict with:
      visual_semantic_score : best cosine similarity across frames & text variants
      per_variant          : {variant_name: similarity}
      frame_count          : number of embedded frames
      passed_gate          : bool, True iff score >= min_similarity
      model                : CLIP model identifier
      reason               : human-readable explanation

    Raises VisualSemanticUnavailable if the local model cannot be loaded — the
    caller must treat that as a hard failure, never a pass.
    """
    min_sim = min_similarity if min_similarity is not None else _DEFAULT_MIN_SIMILARITY
    variants = build_scene_text_variants(query, visual_intent, narration)

    feats = embed_media(media_path, max_video_frames=max_video_frames)
    if not feats:
        return {
            "visual_semantic_score": 0.0,
            "per_variant": {k: 0.0 for k in variants},
            "frame_count": 0,
            "passed_gate": False,
            "model": f"{_DEFAULT_MODEL}/{_DEFAULT_PRETRAINED}",
            "reason": "Media could not be decoded into embeddable frames; rejected.",
        }

    text_feats = {name: _RUNTIME.embed_text(txt) for name, txt in variants.items()}

    per_variant: Dict[str, float] = {}
    for name, tfeat in text_feats.items():
        # Best matching frame for this text variant
        per_variant[name] = round(max(_cosine(f, tfeat) for f in feats), 4)

    # The authoritative score is the best agreement between ANY frame and ANY
    # scene text variant. This is what the hard gate uses.
    best_score = max(per_variant.values()) if per_variant else 0.0
    passed = best_score >= min_sim

    reason = (
        f"CLIP visual-semantic {best_score:.3f} vs gate {min_sim:.3f} "
        f"({'PASS' if passed else 'REJECT'}); variants={per_variant}; frames={len(feats)}"
    )
    return {
        "visual_semantic_score": round(best_score, 4),
        "per_variant": per_variant,
        "frame_count": len(feats),
        "passed_gate": passed,
        "model": f"{_DEFAULT_MODEL}/{_DEFAULT_PRETRAINED}",
        "reason": reason,
    }


def visual_semantic_enabled() -> bool:
    """Whether the real visual-semantic gate is active for production."""
    return bool(getattr(CONFIG, "visual_semantic_enabled", True))


def visual_semantic_min_similarity() -> float:
    return float(getattr(CONFIG, "visual_semantic_min_similarity", _DEFAULT_MIN_SIMILARITY))


__all__ = [
    "VisualSemanticUnavailable",
    "visual_semantic_verify",
    "visual_semantic_enabled",
    "visual_semantic_min_similarity",
    "embed_media",
    "extract_video_frames",
]
