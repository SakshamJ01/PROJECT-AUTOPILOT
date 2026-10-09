"""YouTube SEO keyword scoring (Round-2 upgrade #5).

Implements a KGR-style (Keyword Golden Ratio) opportunity scorer for topic /
keyword selection, complementing the research stage. Inspired by the SEO tooling
in ``tube-atlas-oss``.

Volume and competition are injectable so the scorer is fully offline-testable;
when the optional ``pytrends`` package is installed the default volume fetcher
pulls relative YouTube search interest (``gprop="youtube"``). Any missing signal
degrades gracefully (``None`` fields + a note) rather than raising.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# KGR: results-with-keyword-in-title / monthly searches. Below this is a good bet.
KGR_GOOD_THRESHOLD = 0.25


def pytrends_available() -> bool:
    try:
        import pytrends  # noqa: F401
        return True
    except Exception:
        return False


def _default_fetch_volume(keyword: str) -> Optional[float]:
    """Relative YouTube search volume via pytrends (lazy, optional)."""
    if not pytrends_available():
        return None
    try:
        from pytrends.request import TrendReq

        pytrends = TrendReq(hl="en-US", tz=0)
        pytrends.build_payload([keyword], timeframe="today 12-m", gprop="youtube")
        df = pytrends.interest_over_time()
        if df is None or keyword not in df.columns or df.empty:
            return None
        return float(df[keyword].mean())
    except Exception as exc:  # pragma: no cover - only hit on live network
        logger.warning("pytrends volume fetch failed for '%s': %s", keyword, exc)
        return None


def _default_fetch_competition(keyword: str) -> Optional[float]:
    """Competition proxy in [0,1] (offline heuristic by default).

    Longer, more specific phrases are treated as less competitive; a simple,
    dependency-free stand-in until a real results-count source is wired.
    """
    words = [w for w in re.split(r"\s+", keyword.strip()) if w]
    if not words:
        return None
    # More words -> longer tail -> less competition (clamped).
    return max(0.1, min(1.0, 1.0 - (len(words) - 1) * 0.15))


@dataclass
class KeywordOpportunity:
    """SEO opportunity for a single keyword/phrase."""

    keyword: str
    search_volume: Optional[float] = None
    competition: Optional[float] = None
    kgR: Optional[float] = None  # noqa: N815 - mirrors the industry term
    opportunity_score: Optional[float] = None  # 0..100
    backend: str = "heuristic"
    notes: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)


def score_keyword(
    keyword: str,
    fetch_volume: Optional[Callable[[str], Optional[float]]] = None,
    fetch_competition: Optional[Callable[[str], Optional[float]]] = None,
) -> KeywordOpportunity:
    """Score one keyword. Missing signals degrade to ``None`` fields."""
    kw = " ".join(str(keyword or "").split())
    if not kw:
        return KeywordOpportunity(keyword="", notes="empty keyword")

    volume_fn = fetch_volume or _default_fetch_volume
    comp_fn = fetch_competition or _default_fetch_competition

    volume = volume_fn(kw)
    competition = comp_fn(kw)

    kgR: Optional[float] = None
    if volume and volume > 0:
        # Proxy "results in title" ~ competition scaled into a results-like count.
        results_proxy = (competition if competition is not None else 0.5) * 10000.0
        kgR = round(results_proxy / max(volume, 1.0), 4)

    opportunity: Optional[float] = None
    if volume is not None and competition is not None:
        vol_norm = max(0.0, min(1.0, volume / 100.0))
        # High volume + low competition -> high opportunity.
        opportunity = round(100.0 * (0.6 * vol_norm + 0.4 * (1.0 - competition)), 1)

    notes_parts = []
    if volume is None:
        notes_parts.append("search volume unavailable")
    if competition is None:
        notes_parts.append("competition unavailable")
    if kgR is not None:
        notes_parts.append(
            "kgR good (<= %.2f)" % KGR_GOOD_THRESHOLD
            if kgR <= KGR_GOOD_THRESHOLD
            else "kgR high (> %.2f)" % KGR_GOOD_THRESHOLD
        )

    return KeywordOpportunity(
        keyword=kw,
        search_volume=round(volume, 2) if volume is not None else None,
        competition=round(competition, 3) if competition is not None else None,
        kgR=kgR,
        opportunity_score=opportunity,
        backend="pytrends" if (fetch_volume is None and volume is not None) else "heuristic",
        notes="; ".join(notes_parts),
        evidence={"volume": volume, "competition": competition, "kgR": kgR},
    )


def score_keywords(
    keywords: List[str],
    fetch_volume: Optional[Callable[[str], Optional[float]]] = None,
    fetch_competition: Optional[Callable[[str], Optional[float]]] = None,
) -> List[KeywordOpportunity]:
    """Score a batch of keywords."""
    return [score_keyword(k, fetch_volume, fetch_competition) for k in keywords]


def derive_keywords_from_topic(topic: str) -> List[str]:
    """Derive candidate keyword phrases from a topic string."""
    base = " ".join(str(topic or "").split())
    if not base:
        return []
    words = base.split()
    phrases = [base.lower()]
    # Add a shorter head term (first 2-3 words) as an additional candidate.
    if len(words) > 2:
        phrases.append(" ".join(words[:3]).lower())
    return phrases


def score_topic(topic: str, **kwargs: Any) -> KeywordOpportunity:
    """Score the primary keyword derived from a topic (best opportunity wins)."""
    candidates = derive_keywords_from_topic(topic)
    if not candidates:
        return KeywordOpportunity(keyword="", notes="empty topic")
    scored = [score_keyword(k, **kwargs) for k in candidates]

    def _key(item: KeywordOpportunity) -> float:
        return item.opportunity_score if item.opportunity_score is not None else -1.0

    return max(scored, key=_key)
