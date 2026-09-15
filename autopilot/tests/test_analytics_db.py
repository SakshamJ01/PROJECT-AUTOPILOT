"""Database tests for Milestone 8 Analytics SQLite Schema v5."""
import pytest
from pathlib import Path

from autopilot.db.manager import DBManager, DB_SCHEMA_VERSION
from autopilot.core.contracts import (
    AnalyticsSnapshot,
    MetricObservation,
    DerivedMetric,
    AnalyticsProvenance,
    PerformanceWindow,
    MetricType,
)


def test_schema_migration_v5(tmp_path):
    db_path = tmp_path / "test_v5.db"
    db = DBManager(db_path)
    db.init_schema()

    with db._connect() as conn:
        ver = conn.execute("SELECT version FROM schema_version").fetchone()["version"]
        assert ver >= 5

        # Check analytics tables exist
        tables = [r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "analytics_snapshots" in tables
        assert "metric_observations" in tables
        assert "derived_metrics" in tables


def test_record_and_get_snapshot(tmp_path):
    db_path = tmp_path / "test_snapshot.db"
    db = DBManager(db_path)
    db.init_schema()

    db.create_job("job-snap-01", topic="AI Robotics")

    prov = AnalyticsProvenance(
        provider="mock",
        source="synthetic",
        remote_content_id="vid-ai-01",
        raw_response_hash="hash-123",
    )
    snap = AnalyticsSnapshot(
        snapshot_id="snap-001",
        job_id="job-snap-01",
        content_id="job-snap-01",
        platform="youtube",
        remote_id="vid-ai-01",
        window=PerformanceWindow.WINDOW_24H,
        observed_at="2026-09-11T12:00:00Z",
        retrieved_at="2026-09-11T12:05:00Z",
        provider="mock",
        metrics={
            "views": MetricObservation(metric_name="views", raw_name="viewCount", raw_value=1200.0, normalized_value=1200.0, window=PerformanceWindow.WINDOW_24H),
            "likes": MetricObservation(metric_name="likes", raw_name="likeCount", raw_value=85.0, normalized_value=85.0, window=PerformanceWindow.WINDOW_24H),
        },
        derived_metrics={
            "engagement_rate": DerivedMetric(metric_name="engagement_rate", value=0.0708, formula="likes / views"),
        },
        provenance=prov,
        is_synthetic=True,
    )

    recorded_id = db.record_analytics_snapshot(snap)
    assert recorded_id == "snap-001"

    retrieved = db.get_analytics_snapshot("snap-001")
    assert retrieved is not None
    assert retrieved.snapshot_id == "snap-001"
    assert retrieved.job_id == "job-snap-01"
    assert retrieved.window == PerformanceWindow.WINDOW_24H
    assert len(retrieved.metrics) == 2
    assert retrieved.metrics["views"].normalized_value == 1200.0
    assert retrieved.derived_metrics["engagement_rate"].value == 0.0708
    assert retrieved.is_synthetic is True


def test_time_series_historical_snapshots(tmp_path):
    db_path = tmp_path / "test_history.db"
    db = DBManager(db_path)
    db.init_schema()

    db.create_job("job-ts-01", topic="Space Exploration")

    # Record 1h snapshot
    snap_1h = AnalyticsSnapshot(
        snapshot_id="snap-1h",
        job_id="job-ts-01",
        content_id="job-ts-01",
        platform="youtube",
        remote_id="vid-space",
        window=PerformanceWindow.WINDOW_1H,
        observed_at="2026-09-11T01:00:00Z",
        provider="mock",
        metrics={"views": MetricObservation(metric_name="views", raw_value=100.0, normalized_value=100.0, window=PerformanceWindow.WINDOW_1H)},
        provenance=AnalyticsProvenance(provider="mock", raw_response_hash="hash-1h"),
    )
    db.record_analytics_snapshot(snap_1h)

    # Record 24h snapshot
    snap_24h = AnalyticsSnapshot(
        snapshot_id="snap-24h",
        job_id="job-ts-01",
        content_id="job-ts-01",
        platform="youtube",
        remote_id="vid-space",
        window=PerformanceWindow.WINDOW_24H,
        observed_at="2026-09-11T24:00:00Z",
        provider="mock",
        metrics={"views": MetricObservation(metric_name="views", raw_value=1500.0, normalized_value=1500.0, window=PerformanceWindow.WINDOW_24H)},
        provenance=AnalyticsProvenance(provider="mock", raw_response_hash="hash-24h"),
    )
    db.record_analytics_snapshot(snap_24h)

    # Verify both snapshots are preserved
    history = db.list_analytics_snapshots_for_job("job-ts-01")
    assert len(history) == 2
    # Latest snapshot should be 24h
    latest = db.get_latest_snapshot_for_job("job-ts-01")
    assert latest is not None
    assert latest.snapshot_id == "snap-24h"
    assert latest.metrics["views"].normalized_value == 1500.0


def test_idempotency_duplicate_snapshot(tmp_path):
    db_path = tmp_path / "test_idempotency.db"
    db = DBManager(db_path)
    db.init_schema()

    db.create_job("job-idemp-01", topic="Ancient History")

    snap = AnalyticsSnapshot(
        snapshot_id="snap-idemp-1",
        job_id="job-idemp-01",
        content_id="job-idemp-01",
        platform="youtube",
        remote_id="vid-hist",
        window=PerformanceWindow.LIFETIME,
        provider="mock",
        metrics={"views": MetricObservation(metric_name="views", raw_value=300.0, normalized_value=300.0)},
        provenance=AnalyticsProvenance(provider="mock", raw_response_hash="unique-hash-abc"),
    )
    first_id = db.record_analytics_snapshot(snap)
    assert first_id == "snap-idemp-1"

    # Attempt to record another snapshot with identical raw hash
    snap_dup = AnalyticsSnapshot(
        snapshot_id="snap-idemp-2",
        job_id="job-idemp-01",
        content_id="job-idemp-01",
        platform="youtube",
        remote_id="vid-hist",
        window=PerformanceWindow.LIFETIME,
        provider="mock",
        metrics={"views": MetricObservation(metric_name="views", raw_value=300.0, normalized_value=300.0)},
        provenance=AnalyticsProvenance(provider="mock", raw_response_hash="unique-hash-abc"),
    )
    second_id = db.record_analytics_snapshot(snap_dup)
    assert second_id == first_id

    # Confirm only 1 snapshot exists in DB
    snaps = db.list_analytics_snapshots_for_job("job-idemp-01")
    assert len(snaps) == 1


def test_get_content_performance_linkage(tmp_path):
    db_path = tmp_path / "test_linkage.db"
    db = DBManager(db_path)
    db.init_schema()

    job_id = "job-link-001"
    db.create_job(job_id, topic="Microbiology")

    # Record publication
    from autopilot.core.contracts import PublicationReceipt, PublishStatus, PublishVisibility, PublishPlatform
    rcpt = PublicationReceipt(
        receipt_id="rcpt-001",
        job_id=job_id,
        content_id=job_id,
        render_checksum_sha256="sha-video-12345",
        platform=PublishPlatform.YOUTUBE,
        remote_video_id="yt-micro-999",
        remote_url="https://youtu.be/yt-micro-999",
        metadata_hash="meta-hash",
        idempotency_key="idemp-key",
    )
    db.record_publication(rcpt)

    # Record analytics snapshot
    snap = AnalyticsSnapshot(
        snapshot_id="snap-link-01",
        job_id=job_id,
        content_id=job_id,
        platform="youtube",
        remote_id="yt-micro-999",
        window=PerformanceWindow.LIFETIME,
        provider="mock",
        metrics={"views": MetricObservation(metric_name="views", raw_value=8888.0, normalized_value=8888.0)},
        derived_metrics={"engagement_rate": DerivedMetric(metric_name="engagement_rate", value=0.06)},
        provenance=AnalyticsProvenance(provider="mock", remote_content_id="yt-micro-999"),
    )
    db.record_analytics_snapshot(snap)

    perf = db.get_content_performance(job_id)
    assert perf is not None
    assert perf.job_id == job_id
    assert perf.topic == "Microbiology"
    assert perf.render_checksum_sha256 == "sha-video-12345"
    assert perf.remote_id == "yt-micro-999"
    assert perf.latest_snapshot is not None
    assert perf.latest_snapshot.metrics["views"].normalized_value == 8888.0
