"""Trend provider factory — resolves a config name to a TrendProvider instance.

Keeps autonomy decoupled from concrete providers. Default (``mock``) preserves
existing offline behavior; ``reddit`` enables community-signal mining.
"""
from __future__ import annotations

from typing import Optional

from autopilot.providers.contracts import TrendProvider


def get_trend_provider(name: Optional[str] = None) -> TrendProvider:
    """Return a TrendProvider for ``name``.

    Options:
      - ``mock`` (default) -> MockTrendProvider (deterministic offline signals)
      - ``reddit`` / ``reddit_trend`` -> RedditTrendProvider (community signals)
    """
    name_clean = (name or "mock").lower().strip()
    if name_clean in ("reddit", "reddit_trend", "reddit-miner"):
        from autopilot.providers.reddit_trend import RedditTrendProvider

        return RedditTrendProvider()
    if name_clean in ("mock", "mock_trend", "none", ""):
        from autopilot.providers.mock_trend import MockTrendProvider

        return MockTrendProvider()
    # Fail loud: an unknown trend provider name used to silently resolve to the
    # mock provider, hiding misconfiguration from the operator.
    raise ValueError(
        f"Unknown trend provider {name!r}. Known providers: 'mock' (default), 'reddit'."
    )
