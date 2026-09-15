"""Tests for MockAnalyticsProvider."""
import pytest
from autopilot.providers.mock_analytics import MockAnalyticsProvider
from autopilot.core.contracts import MetricType, PerformanceWindow


def test_mock_analytics_health():
    provider = MockAnalyticsProvider()
    h = provider.health_check()
    assert h.healthy is True
    assert h.provider_name == "mock"
    assert h.details["mode"] == "synthetic_mock"


def test_mock_analytics_deterministic_metrics():
    provider = MockAnalyticsProvider()
    snap1 = provider.fetch_snapshot(remote_id="yt-video-123", window="lifetime")
    snap2 = provider.fetch_snapshot(remote_id="yt-video-123", window="lifetime")

    assert snap1.metrics["views"].normalized_value == snap2.metrics["views"].normalized_value
    assert snap1.metrics["likes"].normalized_value == snap2.metrics["likes"].normalized_value
    assert snap1.metrics["comments"].normalized_value == snap2.metrics["comments"].normalized_value
    assert snap1.is_synthetic is True
    assert snap1.metrics["views"].metric_type == MetricType.SYNTHETIC


def test_mock_analytics_window_scaling():
    provider = MockAnalyticsProvider()
    snap_1h = provider.fetch_snapshot(remote_id="yt-scale-01", window="1h")
    snap_24h = provider.fetch_snapshot(remote_id="yt-scale-01", window="24h")
    snap_7d = provider.fetch_snapshot(remote_id="yt-scale-01", window="7d")
    snap_life = provider.fetch_snapshot(remote_id="yt-scale-01", window="lifetime")

    v_1h = snap_1h.metrics["views"].normalized_value
    v_24h = snap_24h.metrics["views"].normalized_value
    v_7d = snap_7d.metrics["views"].normalized_value
    v_life = snap_life.metrics["views"].normalized_value

    assert v_1h <= v_24h <= v_7d <= v_life
    assert snap_1h.window == PerformanceWindow.WINDOW_1H
    assert snap_24h.window == PerformanceWindow.WINDOW_24H


def test_mock_analytics_empty_remote_id_rejected():
    provider = MockAnalyticsProvider()
    with pytest.raises(ValueError, match="remote_id cannot be empty"):
        provider.fetch_snapshot(remote_id="")
