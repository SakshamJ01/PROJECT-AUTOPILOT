"""Audio intelligence (Round-2 upgrade #2) — stem-aware vocal clarity QA.

Uses Meta Demucs (lazy import, optional) to separate the final mix into
vocals vs. background (music/SFX), then measures whether the voice is actually
audible over the BGM bed. This turns the ``AUDIO_MIX`` / audio-balance QA from
a manifest-trusting heuristic into a measured check.

Demucs is heavy (torch model download); it is only imported when the caller
opts in and the package is installed. When unavailable the analyzer reports
``available=False`` and callers degrade to the existing manifest-based logic,
so this is safe under the hermetic test suite.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# A voice that is at least this many dB above the background is considered clear.
DEFAULT_MIN_VOCAL_TO_BACKGROUND_DB = 3.0
# Below this margin the voice is borderline; far below it is a defect.
DEFAULT_WARN_VOCAL_TO_BACKGROUND_DB = 0.0


def demucs_available() -> bool:
    """Return True when Demucs can be imported on this machine."""
    try:
        from demucs.api import Separator  # noqa: F401
        return True
    except Exception:
        return False


def _rms_db(tensor) -> float:
    """Root-mean-square level of a torch tensor expressed in dBFS."""
    import torch

    t = tensor.detach().float()
    rms = torch.sqrt(torch.mean(t * t)).item()
    return 20.0 * math.log10(rms + 1e-9)


@dataclass
class VocalClarityReport:
    """Result of a stem-based vocal-vs-background loudness analysis."""

    available: bool = False
    backend: str = "none"
    vocal_rms_db: Optional[float] = None
    background_rms_db: Optional[float] = None
    vocal_to_background_db: Optional[float] = None
    clear: Optional[bool] = None
    notes: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)


def analyze_vocal_clarity(
    media_path: Path,
    min_vocal_to_background_db: float = DEFAULT_MIN_VOCAL_TO_BACKGROUND_DB,
    model_name: str = "htdemucs",
) -> VocalClarityReport:
    """Separate ``media_path`` into stems and score vocal clarity.

    Returns a :class:`VocalClarityReport`. Fail-soft: any error (missing Demucs,
    unreadable media, model download failure) yields ``available=False`` with a
    note rather than raising, so QA never blocks on an optional tool.
    """
    media_path = Path(media_path)
    if not media_path.exists():
        return VocalClarityReport(available=False, notes=f"media not found: {media_path}")

    if not demucs_available():
        return VocalClarityReport(
            available=False, backend="none",
            notes="demucs not installed; vocal clarity not measured",
        )

    try:
        from demucs.api import Separator
        import torch

        separator = Separator(model=model_name, device="cpu")
        _, stems = separator.separate_audio_file(str(media_path))

        vocals = stems.get("vocals")
        if vocals is None:
            return VocalClarityReport(
                available=False, backend="demucs",
                notes="demucs returned no vocal stem",
            )

        background_parts = [t for name, t in stems.items() if name != "vocals"]
        if background_parts:
            background = torch.stack(background_parts).mean(dim=0)
        else:
            background = torch.zeros_like(vocals)

        vocal_db = _rms_db(vocals)
        background_db = _rms_db(background)
        margin = vocal_db - background_db

        return VocalClarityReport(
            available=True,
            backend="demucs",
            vocal_rms_db=round(vocal_db, 2),
            background_rms_db=round(background_db, 2),
            vocal_to_background_db=round(margin, 2),
            clear=bool(margin >= min_vocal_to_background_db),
            notes=(
                "vocal clarity measured via demucs stem separation"
                if margin >= min_vocal_to_background_db
                else "vocals are not sufficiently above the background mix"
            ),
            evidence={
                "model": model_name,
                "vocal_rms_db": round(vocal_db, 2),
                "background_rms_db": round(background_db, 2),
                "vocal_to_background_db": round(margin, 2),
                "min_threshold_db": min_vocal_to_background_db,
            },
        )
    except Exception as exc:  # pragma: no cover - only hit when demucs fails live
        logger.warning("vocal clarity analysis failed: %s", exc)
        return VocalClarityReport(
            available=False, backend="demucs",
            notes=f"demucs analysis failed: {exc}",
        )
