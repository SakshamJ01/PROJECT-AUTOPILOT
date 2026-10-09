"""Multimodal creative QA (Round-2 upgrade #6) — Gemini video analysis.

Uploads the rendered video to Gemini and requests structured relevance /
dynamism scores, turning creative QA from heuristics into a real multimodal
judgment (inspired by the ``fineVideo`` pattern).

Gemini access is lazy and optional: when no API key is configured or the
``google-genai`` package is missing, the analyzer reports ``available=False``
and callers keep their existing heuristic verdicts (fail-open). Fully
offline-testable via an injected ``analyze_fn``.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


def gemini_available() -> bool:
    """Return True when the optional ``google-genai`` package is importable."""
    try:
        import google.genai  # noqa: F401
        return True
    except Exception:
        return False


def _resolve_api_key(api_key: Optional[str]) -> Optional[str]:
    return api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


@dataclass
class MultimodalQAResult:
    """Structured multimodal judgment of a rendered video."""

    available: bool = False
    backend: str = "none"
    relevance_score: Optional[float] = None  # 0-100
    dynamism_score: Optional[float] = None  # 0-100
    hook_effectiveness: Optional[float] = None  # 0-100
    notes: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


_PROMPT = (
    "You are a short-form video creative QA reviewer. Watch this vertical video "
    "and score three dimensions from 0 to 100:\n"
    "1) relevance: how well the visuals and narration match the topic.\n"
    "2) dynamism: how visually dynamic it is (cuts, motion, no static dead air).\n"
    "3) hook: how strong the opening hook is.\n"
    "Return ONLY a JSON object with keys relevance, dynamism, hook (integers)."
)


def _default_analyze(video_path: Path, api_key: str, model: str) -> Dict[str, Any]:
    """Upload the video to Gemini and request structured scores."""
    from google import genai

    client = genai.Client(api_key=api_key)
    uploaded = client.files.upload(file=str(video_path))
    response = client.models.generate_content(
        model=model,
        contents=[uploaded, _PROMPT],
    )
    text = getattr(response, "text", "") or ""
    # Extract the first JSON object from the response text.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])
    return json.loads(text)


def analyze_video(
    video_path: Path,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    analyze_fn: Optional[Callable[[Path, str, str], Dict[str, Any]]] = None,
) -> MultimodalQAResult:
    """Analyze a rendered video with Gemini. Fail-soft on any problem."""
    video_path = Path(video_path)
    if not video_path.exists():
        return MultimodalQAResult(available=False, notes=f"video not found: {video_path}")

    key = _resolve_api_key(api_key)
    if analyze_fn is None and (not key or not gemini_available()):
        return MultimodalQAResult(
            available=False,
            notes="gemini unavailable (missing API key or google-genai package)",
        )

    model_name = model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    fn = analyze_fn or _default_analyze

    try:
        data = fn(video_path, key or "", model_name)

        def _score(name: str) -> Optional[float]:
            val = data.get(name)
            try:
                return max(0.0, min(100.0, float(val))) if val is not None else None
            except (TypeError, ValueError):
                return None

        relevance = _score("relevance")
        dynamism = _score("dynamism")
        hook = _score("hook")
        return MultimodalQAResult(
            available=True,
            backend="gemini",
            relevance_score=relevance,
            dynamism_score=dynamism,
            hook_effectiveness=hook,
            notes="multimodal scores returned by Gemini",
            raw=data,
        )
    except Exception as exc:  # pragma: no cover - only hit on live Gemini call
        logger.warning("multimodal QA analysis failed: %s", exc)
        return MultimodalQAResult(available=False, notes=f"gemini analysis failed: {exc}")
