"""Tests for AnalyticsEngine & derived metrics calculations."""
import pytest
from pathlib import Path

from autopilot.core.config import Config
from autopilot.db.manager import DBManager
from autopilot.core.contracts import MetricObservation, MetricType, PerformanceWindow
from autopilot.core.analytics import AnalyticsEngine


def test_calculate_derived_metrics_full_inputs():
    engine = AnalyticsEngine()
    metrics = {
        "views": MetricObservation(metric_name="views", raw_value=10000.0, normalized_value=10000.0),
        "likes": MetricObservation(metric_name="likes", raw_value=800.0, normalized_value=800.0),
        "comments": MetricObservation(metric_name="comments", raw_value=50.0, normalized_value=50.0),
        "shares": MetricObservation(metric_name="shares", raw_value=30.0, normalized_value=30.0),
        "average_view_duration_seconds": MetricObservation(metric_name="average_view_duration_seconds", raw_value=30.0, normalized_value=30.0),
        "video_duration_seconds": MetricObservation(metric_name="video_duration_seconds", raw_value=40.0, normalized_value=40.0),
    }

    derived = engine.calculate_derived_metrics(
        metrics=metrics,
        published_at="2026-09-10T10:00:00Z",
        observed_at="2026-09-11T10:00:00Z",  # 24 hours later
    )

    # Engagement: (800 + 50 + 30) / 10000 = 0.088
    assert derived["engagement_rate"].value == 0.088
    # Like ratio: 800 / 10000 = 0.08
    assert derived["like_ratio"].value == 0.08
    # Comment ratio: 50 / 10000 = 0.005
    assert derived["comment_ratio"].value == 0.005
    # Completion rate: 30 / 40 = 0.75
    assert derived["completion_rate"].value == 0.75
    # Velocity: 10000 / 24 hrs = 416.67
    assert derived["view_velocity_per_hour"].value == 416.67


def test_calculate_derived_metrics_zero_views_safety():
    engine = AnalyticsEngine()
    metrics = {
        "views": MetricObservation(metric_name="views", raw_value=0.0, normalized_value=0.0),
        "likes": MetricObservation(metric_name="likes", raw_value=0.0, normalized_value=0.0),
    }
    # Must NOT raise ZeroDivisionError
    derived = engine.calculate_derived_metrics(metrics)
    assert "engagement_rate" not in derived
    assert "like_ratio" not in derived
    assert "view_velocity_per_hour" not in derived


def test_calculate_derived_metrics_zero_duration_safety():
    engine = AnalyticsEngine()
    metrics = {
        "views": MetricObservation(metric_name="views", raw_value=100.0, normalized_value=100.0),
        "average_view_duration_seconds": MetricObservation(metric_name="average_view_duration_seconds", raw_value=0.0, normalized_value=0.0),
        "video_duration_seconds": MetricObservation(metric_name="video_duration_seconds", raw_value=0.0, normalized_value=0.0),
    }
    derived = engine.calculate_derived_metrics(metrics)
    assert "completion_rate" not in derived


def test_sync_job_mock_provider(tmp_path):
    db_path = tmp_path / "test_sync.db"
    db = DBManager(db_path)
    db.init_schema()

    cfg = Config(artifacts_dir=tmp_path, db_path=db_path)
    engine = AnalyticsEngine(config=cfg, db=db)

    job_id = "job-sync-001"
    db.create_job(job_id, topic="Marine Biology")

    # Dry-run test
    res_dry = engine.sync_job(job_id, dry_run=True)
    assert res_dry["status"] == "simulated"
    assert res_dry["dry_run"] is True
    # Verify no snapshots written
    assert len(db.list_analytics_snapshots_for_job(job_id)) == 0

    # Live sync test
    res_live = engine.sync_job(job_id, dry_run=False, window="24h")
    assert res_live["status"] == "success"
    assert res_live["snapshot_id"] is not None
    assert res_live["metrics"]["views"] > 0
    assert "engagement_rate" in res_live["derived_metrics"]

    # Verify snapshot persisted in SQLite
    snaps = db.list_analytics_snapshots_for_job(job_id)
    assert len(snaps) == 1
    assert snaps[0].window == PerformanceWindow.WINDOW_24H

    # Verify artifact written
    snap_art = tmp_path / "jobs" / job_id / "analytics" / f"snapshot_{res_live['snapshot_id']}.json"
    assert snap_art.exists()


def test_sync_all_batch_and_report(tmp_path):
    db_path = tmp_path / "test_batch.db"
    db = DBManager(db_path)
    db.init_schema()

    cfg = Config(artifacts_dir=tmp_path, db_path=db_path)
    engine = AnalyticsEngine(config=cfg, db=db)

    # Create 3 published jobs (publish_records drive sync_all targeting)
    for i in range(1, 4):
        db.create_job(f"job-b-{i}", topic=f"Topic {i}")
        db.update_job_status(f"job-b-{i}", "PUBLISHED")
        with db._connect() as conn:
            conn.execute(
                """INSERT INTO publish_records (
                    publish_id, job_id, content_id, platform, provider, status,
                    visibility, remote_video_id, remote_url, idempotency_key,
                    media_checksum_sha256, metadata_json, receipt_json, created_at
                ) VALUES (?, ?, ?, 'youtube', 'youtube', 'PUBLISHED', 'private',
                          ?, '', ?, 'checksum', '{}', '{}', CURRENT_TIMESTAMP)""",
                (f"publish-b-{i}", f"job-b-{i}", f"job-b-{i}", f"remote-vid-{i}", f"idem-job-b-{i}"),
            )
            conn.commit()

    # Unpublished jobs must NOT be targeted (P1-02 fix)
    db.create_job("job-unpublished", topic="Unpublished")
    db.update_job_status("job-unpublished", "APPROVED")

    # Run batch sync
    batch_res = engine.sync_all(limit=10, dry_run=False)
    assert batch_res["total_targeted"] == 3
    assert batch_res["synced_count"] == 3

    # Generate performance report
    report = engine.get_performance_report(limit=10)
    assert len(report) == 3
    for entry in report:
        assert entry["views"] > 0
        assert entry["snapshots_recorded"] == 1
