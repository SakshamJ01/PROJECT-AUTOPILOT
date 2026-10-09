"""Reddit trend provider (Round-2 upgrade #4).

Implements the :class:`TrendProvider` protocol by mining hot posts from
category-mapped subreddits — a zero-key, community-signal alternative (or
complement) to the mock/Google-Trends sources. Inspired by ``reddit-idea-miner``.

The network fetch is injectable (``fetch_json``) so the provider is fully
testable offline and fail-soft in production: any network/API error yields an
empty signal list rather than raising.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from autopilot.core.contracts import ProvenanceRecord, TrendSignal
from autopilot.providers.contracts import (
    REGISTRY,
    CapabilityMetadata,
    CostUsageMetadata,
    ProviderErrorType,
    ProviderHealth,
    TrendProvider,
)

logger = logging.getLogger(__name__)

# Subreddits mined per topic category (hot posts).
SUBREDDITS_BY_CATEGORY: Dict[str, List[str]] = {
    "technology": ["technology", "programming", "artificial", "MachineLearning"],
    "science": ["science", "space", "physics", "biology"],
    "history": ["history", "AskHistorians", "Archaeology"],
    "finance": ["finance", "investing", "economics", "stocks"],
    "general": ["todayilearned", "interestingasfuck", "Damnthatsinteresting"],
}
_DEFAULT_SUBREDDIT = "todayilearned"
_REDDIT_UA = "autopilot-trend-miner/1.0"


def _default_fetch_json(url: str, timeout: float) -> Any:
    """Default HTTP GET returning parsed JSON (urllib only, no extra deps)."""
    import json
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": _REDDIT_UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


class RedditTrendProvider:
    """TrendProvider backed by Reddit hot-post mining."""

    provider_name: str = "reddit_trend"
    capability: CapabilityMetadata = CapabilityMetadata(
        max_resolution="N/A",
        supports_9_16=True,
        local_only=False,
        license_note="Community-sourced trend signals from public Reddit posts",
    )
    error_type: ProviderErrorType = ProviderErrorType.NOT_AVAILABLE
    cost_meta: CostUsageMetadata = CostUsageMetadata(
        estimated_usd=0.0,
        provider_type="community",
    )

    def __init__(
        self,
        fetch_json: Optional[Callable[[str, float], Any]] = None,
        timeout: float = 10.0,
    ):
        # Injectable fetch keeps the provider offline-testable.
        self._fetch_json = fetch_json or _default_fetch_json
        self._timeout = timeout

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            healthy=True,
            provider_name=self.provider_name,
            error="",
            details={"mode": "reddit_hot_posts", "status": "AVAILABLE"},
        )

    def _subreddits_for(self, category: Optional[str]) -> List[str]:
        if category and category.lower() not in ("", "all"):
            return SUBREDDITS_BY_CATEGORY.get(
                category.lower().strip(), [_DEFAULT_SUBREDDIT]
            )
        # "all"/None: a little from every category.
        out: List[str] = []
        for subs in SUBREDDITS_BY_CATEGORY.values():
            out.extend(subs[:1])
        return out

    def discover_trends(
        self,
        category: Optional[str] = None,
        limit: int = 10,
        **kwargs: Any,
    ) -> List[TrendSignal]:
        """Mine hot Reddit posts into :class:`TrendSignal` objects (fail-soft)."""
        subreddits = self._subreddits_for(category)
        now = datetime.now(timezone.utc)
        now_ts = now.timestamp()
        signals: List[TrendSignal] = []

        for sub in subreddits:
            if len(signals) >= limit:
                break
            url = f"https://www.reddit.com/r/{sub}/hot.json?limit={max(1, limit)}"
            try:
                data = self._fetch_json(url, self._timeout)
            except Exception as exc:
                logger.warning("reddit fetch failed for r/%s: %s", sub, exc)
                continue

            posts = (((data or {}).get("data") or {}).get("children")) or []
            for child in posts:
                if len(signals) >= limit:
                    break
                post = (child or {}).get("data") or {}
                title = (post.get("title") or "").strip()
                if not title:
                    continue

                created = float(post.get("created_utc") or now_ts)
                age_hours = max(0.0, (now_ts - created) / 3600.0)
                # Fresher posts score higher; decay to 0.5 by ~48h.
                freshness = max(0.1, min(1.0, 1.0 - (age_hours / 96.0)))
                ups = float(post.get("ups") or 0)
                # Normalize engagement; log dampens huge threads.
                relevance = max(0.1, min(1.0, (ups / 5000.0) ** 0.5))
                permalink = post.get("permalink") or ""
                full_url = f"https://www.reddit.com{permalink}" if permalink else url

                sig_hash = hashlib.sha256(
                    f"reddit:{sub}:{title}".encode("utf-8")
                ).hexdigest()[:10]
                signal_id = f"trend-reddit-{sig_hash}"

                signals.append(
                    TrendSignal(
                        signal_id=signal_id,
                        topic=title,
                        source=self.provider_name,
                        source_url=full_url,
                        detected_at=now.isoformat(),
                        freshness_score=round(freshness, 3),
                        relevance_score=round(relevance, 3),
                        category=(category or "general").lower(),
                        confidence=0.7,
                        evidence_text=(post.get("selftext") or "")[:500],
                        provenance=ProvenanceRecord(
                            provider=self.provider_name,
                            source_ids=[full_url],
                            deterministic_idempotency_key=signal_id,
                        ),
                    )
                )

        return signals


# Register so it is discoverable via REGISTRY (inject via AutonomyEngine).
REGISTRY.register(RedditTrendProvider())
