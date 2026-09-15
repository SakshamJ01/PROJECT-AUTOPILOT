"""Adversarial and Edge Case Tests for Analytics Subsystem — Milestone 8."""
import pytest
from pydantic import ValidationError

from autopilot.core.contracts import MetricObservation, AnalyticsSnapshot, AnalyticsProvenance, PerformanceWindow
from autopilot.providers.youtube_analytics import YouTubeAnalyticsProvider
from autopilot.core.analytics import AnalyticsEngine
from autopilot.db.manager import DBManager


def test_adversarial_negative_metrics_rejected_in_contracts():
    with pytest.raises(ValidationError):
        MetricObservation(metric_name="views", raw_value=-1.0)

    with pytest.raises(ValidationError):
        MetricObservation(metric_name="likes", raw_value=-50.0)


def test_adversarial_youtube_negative_metrics_rejected():
    def mock_http_negative(url, headers):
        return {
            "items": [
                {
                    "id": "yt-neg",
                    "statistics": {
                        "viewCount": "-500",
                        "likeCount": "10",
                        "commentCount": "0",
                    },
                }
            ]
        }

    provider = YouTubeAnalyticsProvider(http_client=mock_http_negative)
    with pytest.raises(ValueError, match="Impossible negative metric"):
        provider.fetch_snapshot(remote_id="yt-neg")


def test_adversarial_missing_remote_id_rejected():
    provider = YouTubeAnalyticsProvider()
    with pytest.raises(ValueError, match="remote_id cannot be empty"):
        provider.fetch_snapshot(remote_id="")

    with pytest.raises(ValueError, match="remote_id cannot be empty"):
        provider.fetch_snapshot(remote_id="   ")


def test_adversarial_malformed_api_statistics():
    def mock_http_malformed(url, headers):
        return {
            "items": [
                {
                    "id": "yt-bad",
                    "statistics": {
                        "viewCount": "NOT_A_NUMBER",
                        "likeCount": "TEN",
                    },
                }
            ]
        }

    provider = YouTubeAnalyticsProvider(http_client=mock_http_malformed)
    with pytest.raises(ValueError, match="Malformed statistics"):
        provider.fetch_snapshot(remote_id="yt-bad")


def test_adversarial_zero_denominators_handled_gracefully():
    engine = AnalyticsEngine()
    metrics = {
        "views": MetricObservation(metric_name="views", raw_value=0.0, normalized_value=0.0),
        "likes": MetricObservation(metric_name="likes", raw_value=10.0, normalized_value=10.0),
        "average_view_duration_seconds": MetricObservation(metric_name="average_view_duration_seconds", raw_value=15.0, normalized_value=15.0),
        "video_duration_seconds": MetricObservation(metric_name="video_duration_seconds", raw_value=0.0, normalized_value=0.0),
    }

    derived = engine.calculate_derived_metrics(
        metrics=metrics,
        published_at="2026-09-11T12:00:00Z",
        observed_at="2026-09-11T12:00:00Z",  # 0 elapsed time
    )
    # None of the zero-denominator metrics should be present or cause crash
    assert "engagement_rate" not in derived
    assert "like_ratio" not in derived
    assert "completion_rate" not in derived
    assert "view_velocity_per_hour" not in derived


def test_adversarial_inconsistent_future_publication_timestamp():
    engine = AnalyticsEngine()
    metrics = {
        "views": MetricObservation(metric_name="views", raw_value=100.0, normalized_value=100.0),
    }
    # Published in the future relative to observation
    derived = engine.calculate_derived_metrics(
        metrics=metrics,
        published_at="2026-10-01T00:00:00Z",
        observed_at="2026-09-01T00:00:00Z",
    )
    # Velocity should not compute negative or crash
    assert "view_velocity_per_hour" not in derived


def test_adversarial_duplicate_snapshot_does_not_multiply_rows(tmp_path):
    db = DBManager(tmp_path / "adv_db.db")
    db.init_schema()

    db.create_job("job-adv-01", topic="Testing")

    snap = AnalyticsSnapshot(
        snapshot_id="snap-adv-1",
        job_id="job-adv-01",
        platform="youtube",
        remote_id="yt-adv-01",
        window=PerformanceWindow.LIFETIME,
        metrics={"views": MetricObservation(metric_name="views", raw_value=100.0, normalized_value=100.0)},
        provenance=AnalyticsProvenance(provider="mock", raw_response_hash="hash-adv-fixed"),
    )

    # Ingest 5 times
    for _ in range(5):
        db.record_analytics_snapshot(snap)

    history = db.list_analytics_snapshots_for_job("job-adv-01")
    assert len(history) == 1
