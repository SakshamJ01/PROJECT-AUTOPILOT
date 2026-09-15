"""Unit tests for Milestone 8 Analytics Contracts & Pydantic models."""
import pytest
from pydantic import ValidationError

from autopilot.core.contracts import (
    PerformanceWindow,
    MetricType,
    MetricObservation,
    DerivedMetric,
    AnalyticsProvenance,
    AnalyticsSnapshot,
    ContentPerformance,
)


def test_performance_window_enums():
    assert PerformanceWindow.WINDOW_1H.value == "1h"
    assert PerformanceWindow.WINDOW_24H.value == "24h"
    assert PerformanceWindow.WINDOW_7D.value == "7d"
    assert PerformanceWindow.WINDOW_28D.value == "28d"
    assert PerformanceWindow.LIFETIME.value == "lifetime"


def test_metric_type_enums():
    assert MetricType.MEASURED.value == "measured"
    assert MetricType.DERIVED.value == "derived"
    assert MetricType.SYNTHETIC.value == "synthetic"


def test_metric_observation_validation():
    obs = MetricObservation(
        metric_name="views",
        raw_name="viewCount",
        raw_value=1500.0,
        normalized_value=1500.0,
        unit="count",
        metric_type=MetricType.MEASURED,
        window=PerformanceWindow.WINDOW_24H,
    )
    assert obs.metric_name == "views"
    assert obs.raw_value == 1500.0
    assert obs.unit == "count"
    assert obs.window == PerformanceWindow.WINDOW_24H

    # Test rejection of negative values
    with pytest.raises(ValidationError):
        MetricObservation(
            metric_name="views",
            raw_value=-10.0,
        )


def test_derived_metric_validation():
    dm = DerivedMetric(
        metric_name="engagement_rate",
        value=0.085,
        formula="(likes + comments + shares) / views",
        input_metrics={"views": 1000.0, "likes": 80.0, "comments": 5.0},
        confidence=1.0,
    )
    assert dm.metric_name == "engagement_rate"
    assert dm.value == 0.085
    assert dm.input_metrics["views"] == 1000.0


def test_analytics_snapshot_round_trip():
    prov = AnalyticsProvenance(
        provider="mock",
        source="synthetic",
        remote_content_id="vid-test-01",
        raw_response_hash="abcdef123456",
    )
    snap = AnalyticsSnapshot(
        snapshot_id="snap-test-001",
        job_id="job-test-001",
        content_id="cnt-test-001",
        platform="youtube",
        remote_id="vid-test-01",
        window=PerformanceWindow.LIFETIME,
        provider="mock",
        metrics={
            "views": MetricObservation(
                metric_name="views",
                raw_value=5000.0,
                normalized_value=5000.0,
                metric_type=MetricType.SYNTHETIC,
            )
        },
        derived_metrics={
            "engagement_rate": DerivedMetric(
                metric_name="engagement_rate",
                value=0.05,
                formula="likes / views",
            )
        },
        provenance=prov,
        is_synthetic=True,
    )

    json_str = snap.model_dump_json()
    reloaded = AnalyticsSnapshot.model_validate_json(json_str)
    assert reloaded.snapshot_id == "snap-test-001"
    assert reloaded.metrics["views"].raw_value == 5000.0
    assert reloaded.derived_metrics["engagement_rate"].value == 0.05
    assert reloaded.is_synthetic is True
    assert reloaded.provenance.raw_response_hash == "abcdef123456"


def test_content_performance_model():
    cp = ContentPerformance(
        job_id="job-test-001",
        content_id="cnt-test-001",
        topic="Quantum Computing",
        platform="youtube",
        remote_id="yt-12345",
        published_at="2026-09-11T12:00:00Z",
    )
    assert cp.job_id == "job-test-001"
    assert cp.topic == "Quantum Computing"
    assert len(cp.snapshot_history) == 0
    assert cp.latest_snapshot is None
