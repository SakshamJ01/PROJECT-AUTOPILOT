"""Unit tests for Milestone 9 Learning Feedback & Strategy Management."""
import pytest
from autopilot.core.feedback import FeedbackAnalyzer, StrategyManager
from autopilot.core.contracts import (
    FeedbackSignal,
    LearningObservation,
    StrategyVersion,
    StrategyStatus,
    PerformanceTier,
)
from autopilot.db.manager import DBManager


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test_feedback.db"
    manager = DBManager(db_path)
    manager.init_schema()
    return manager


def test_feedback_analyzer_sample_size_protection(db):
    analyzer = FeedbackAnalyzer(db)

    # Only 1 video recorded (small sample size)
    db.create_job("job-001", topic="Quantum Computing")
    db.record_publish_record(
        job_id="job-001",
        platform="youtube",
        provider="mock",
        visibility="public",
        remote_video_id="vid-001",
        idempotency_key="idem-001",
        media_checksum_sha256="chk001",
    )
    db.record_analytics_snapshot(
        snapshot_id="snap-001",
        job_id="job-001",
        platform="youtube",
        provider="mock",
        remote_id="vid-001",
        window="lifetime",
        metrics={"views": 50000.0, "likes": 2000.0},  # Massive viral outlier
    )

    signals = analyzer.extract_feedback_signals()
    assert len(signals) == 1

    # Learning observations require min sample size (default 3)
    observations = analyzer.generate_learning_observations(signals, min_sample_size=3)
    # 1 video does NOT trigger an aggressive strategy adjustment
    assert len(observations) == 0


def test_feedback_analyzer_cluster_learning(db):
    analyzer = FeedbackAnalyzer(db)

    # Record 4 videos with strong performance
    for i in range(1, 5):
        jid = f"job-tech-{i}"
        snap_id = f"snap-tech-{i}"
        db.create_job(jid, topic=f"Technology Computing Deep Dive {i}")
        db.record_publish_record(
            job_id=jid,
            platform="youtube",
            provider="mock",
            visibility="public",
            remote_video_id=f"vid-tech-{i}",
            idempotency_key=f"idem-tech-{i}",
            media_checksum_sha256=f"chk-tech-{i}",
        )
        db.record_analytics_snapshot(
            snapshot_id=snap_id,
            job_id=jid,
            platform="youtube",
            provider="mock",
            remote_id=f"vid-tech-{i}",
            window="lifetime",
            metrics={"views": 4000.0 + (i * 500), "likes": 300.0},
        )

    # Record 2 low performing videos
    for i in range(1, 3):
        jid = f"job-low-{i}"
        snap_id = f"snap-low-{i}"
        db.create_job(jid, topic=f"Low Performance Topic {i}")
        db.record_publish_record(
            job_id=jid,
            platform="youtube",
            provider="mock",
            visibility="public",
            remote_video_id=f"vid-low-{i}",
            idempotency_key=f"idem-low-{i}",
            media_checksum_sha256=f"chk-low-{i}",
        )
        db.record_analytics_snapshot(
            snapshot_id=snap_id,
            job_id=jid,
            platform="youtube",
            provider="mock",
            remote_id=f"vid-low-{i}",
            window="lifetime",
            metrics={"views": 100.0, "likes": 5.0},
        )

    signals = analyzer.extract_feedback_signals()
    assert len(signals) == 6

    # Generate observations with min_sample_size=2
    observations = analyzer.generate_learning_observations(signals, min_sample_size=2)
    assert len(observations) > 0
    top_obs = [o for o in observations if o.pattern_type == "high_performing_cluster"]
    assert len(top_obs) > 0
    assert "associated with stronger observed performance" in top_obs[0].observation_text


def test_strategy_manager_proposal_and_rollback(db):
    manager = StrategyManager(db)
    base = manager.get_active_strategy()
    assert base.version_id == "strat-v1"

    observations = [
        LearningObservation(
            observation_id="obs-001",
            pattern_type="high_performing_cluster",
            observation_text="Technology topics showed strong engagement.",
            sample_size=4,
            confidence=0.85,
            recommended_adjustment={"niche_weight_boost": {"technology": 0.2}},
        )
    ]

    proposed = manager.propose_strategy_version(
        parent_version=base,
        observations=observations,
        rationale="Boost technology niche based on observed performance.",
    )

    assert proposed is not None
    assert proposed.version_id == "strat-v2"
    assert proposed.status == StrategyStatus.PROPOSED
    assert proposed.niche_weights["technology"] == 1.2

    # Active strategy is still strat-v1
    assert manager.get_active_strategy().version_id == "strat-v1"

    # Activate proposed strategy
    success = manager.activate_strategy("strat-v2")
    assert success is True
    assert manager.get_active_strategy().version_id == "strat-v2"

    # Rollback to strat-v1
    success_rollback = manager.activate_strategy("strat-v1")
    assert success_rollback is True
    assert manager.get_active_strategy().version_id == "strat-v1"
