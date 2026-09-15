"""Phase 3 Tests — Analytics Data Model, Historical Persistence, and Performance Attribution."""
import pytest
from datetime import datetime, timezone
from autopilot.core.config import Config
from autopilot.db.manager import DBManager
from autopilot.core.contracts import VideoPerformance
from autopilot.core.analytics import AnalyticsEngine


@pytest.fixture
def mock_db(tmp_path):
    db_path = tmp_path / "test_analytics_phase3.db"
    db = DBManager(str(db_path))
    db.init_schema()
    return db


def test_video_performance_persistence_and_history(mock_db):
    """Verify persisting multiple snapshots for the same job records historical progression."""
    job_id = "job-perf-hist-1"
    mock_db.create_job(job_id=job_id, topic="Historical Analytics Topic", channel_id="tech_shorts")

    perf1 = VideoPerformance(
        performance_id="perf-snap-1",
        job_id=job_id,
        platform="youtube",
        remote_id="yt-vid-101",
        collected_at="2026-09-12T10:00:00Z",
        views=150,
        likes=12,
        comments=2,
        watch_time_seconds=320.0,
        avg_view_duration_seconds=22.5,
        topic="Historical Analytics Topic",
        channel_id="tech_shorts",
        hook="Did you know this about AI?",
        duration_sec=35.0,
        production_engine="moneyprinterturbo",
    )
    mock_db.record_video_performance(perf1)

    perf2 = VideoPerformance(
        performance_id="perf-snap-2",
        job_id=job_id,
        platform="youtube",
        remote_id="yt-vid-101",
        collected_at="2026-09-12T14:00:00Z",
        views=450,
        likes=35,
        comments=8,
        watch_time_seconds=980.0,
        avg_view_duration_seconds=24.0,
        topic="Historical Analytics Topic",
        channel_id="tech_shorts",
        hook="Did you know this about AI?",
        duration_sec=35.0,
        production_engine="moneyprinterturbo",
    )
    mock_db.record_video_performance(perf2)

    history = mock_db.get_video_performance_history(job_id)
    assert len(history) == 2
    assert history[0]["views"] == 150
    assert history[1]["views"] == 450
    assert history[0]["collected_at"] < history[1]["collected_at"]


def test_channel_performance_attribution(mock_db, tmp_path):
    """Verify attribute_channel_performance correctly ranks duration and engines."""
    cfg = Config(artifacts_dir=str(tmp_path), db_path=str(mock_db.db_path))
    engine = AnalyticsEngine(config=cfg, db=mock_db)

    cid = "science_shorts"
    mock_db.create_job(job_id="job-s1", topic="Topic 1", channel_id=cid)
    mock_db.create_job(job_id="job-s2", topic="Topic 2", channel_id=cid)
    mock_db.create_job(job_id="job-s3", topic="Topic 3", channel_id=cid)

    # Add 3 videos with different durations and metrics
    mock_db.record_video_performance(
        VideoPerformance(
            performance_id="snap-s1",
            job_id="job-s1",
            platform="youtube",
            remote_id="vid-1",
            views=1200,
            channel_id=cid,
            duration_sec=25.0,  # short
            production_engine="moneyprinterturbo",
            hook="Quantum mechanics explained",
        )
    )
    mock_db.record_video_performance(
        VideoPerformance(
            performance_id="snap-s2",
            job_id="job-s2",
            platform="youtube",
            remote_id="vid-2",
            views=1500,
            channel_id=cid,
            duration_sec=28.0,  # short
            production_engine="moneyprinterturbo",
            hook="Black hole anomaly detected",
        )
    )
    mock_db.record_video_performance(
        VideoPerformance(
            performance_id="snap-s3",
            job_id="job-s3",
            platform="youtube",
            remote_id="vid-3",
            views=400,
            channel_id=cid,
            duration_sec=55.0,  # long
            production_engine="ffmpeg",
            hook="The mystery of dark matter",
        )
    )

    attr = engine.attribute_channel_performance(cid)
    assert attr["status"] == "ready"
    assert attr["total_videos"] == 3
    assert attr["average_views"] > 0

    # Short videos should rank higher in duration performance
    top_dur = attr["top_durations"][0]
    assert "short" in top_dur["category"]
    assert top_dur["avg_views"] > 1000

    # MoneyPrinterTurbo should outperform ffmpeg
    top_eng = attr["top_engines"][0]
    assert top_eng["category"] == "moneyprinterturbo"
    assert top_eng["avg_views"] > 1000


def test_unavailable_metrics_not_fabricated():
    """Verify that optional metrics that are not returned remain None rather than 0."""
    perf = VideoPerformance(
        performance_id="snap-nulls",
        job_id="job-nulls",
        views=100,
        retention_rate=None,
        ctr=None,
        impressions=None,
        subscriber_change=None,
    )
    assert perf.retention_rate is None
    assert perf.ctr is None
    assert perf.impressions is None
    assert perf.subscriber_change is None
