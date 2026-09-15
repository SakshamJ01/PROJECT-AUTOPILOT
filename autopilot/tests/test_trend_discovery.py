"""Unit tests for Milestone 9 Trend Discovery & Providers."""
import pytest
from autopilot.providers.mock_trend import MockTrendProvider
from autopilot.providers.contracts import REGISTRY
from autopilot.core.contracts import TrendSignal


def test_mock_trend_provider_registered():
    provider = REGISTRY.get("mock_trend")
    assert provider is not None
    assert isinstance(provider, MockTrendProvider)
    assert provider.provider_name == "mock_trend"


def test_discover_trends_default():
    provider = MockTrendProvider()
    signals = provider.discover_trends(limit=10)
    assert len(signals) > 0
    assert len(signals) <= 10
    for sig in signals:
        assert isinstance(sig, TrendSignal)
        assert len(sig.signal_id) > 0
        assert len(sig.topic) > 0
        assert 0.0 <= sig.freshness_score <= 1.0
        assert 0.0 <= sig.relevance_score <= 1.0
        assert len(sig.evidence_text) > 0
        assert sig.provenance is not None
        assert sig.provenance.provider == "mock_trend"


def test_discover_trends_category_filtering():
    provider = MockTrendProvider()
    tech_signals = provider.discover_trends(category="technology", limit=10)
    assert len(tech_signals) > 0
    for s in tech_signals:
        assert s.category == "technology"

    science_signals = provider.discover_trends(category="science", limit=10)
    assert len(science_signals) > 0
    for s in science_signals:
        assert s.category == "science"

    # Category with no matches returns empty list without error
    empty_signals = provider.discover_trends(category="nonexistent_niche", limit=10)
    assert empty_signals == []


def test_discover_trends_limit():
    provider = MockTrendProvider()
    signals = provider.discover_trends(limit=2)
    assert len(signals) == 2


def test_trend_signal_provenance_preserved():
    provider = MockTrendProvider()
    signals = provider.discover_trends(limit=1)
    sig = signals[0]
    assert sig.provenance is not None
    assert sig.provenance.source_ids[0].startswith("https://")
    assert sig.source_url.startswith("https://")
    assert "mock" in sig.source.lower()
