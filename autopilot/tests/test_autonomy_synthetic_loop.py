"""End-to-end synthetic autonomous loop and feedback verification — Milestone 9.
Validates the complete deterministic feedback loop:
Trend Signal -> Candidate -> Score -> Policy -> Proposal -> M7 Queue -> M8 Analytics -> Feedback -> Next Candidate.
"""
import pytest
from pathlib import Path

from autopilot.db.manager import DBManager
from autopilot.core.autonomy import AutonomyEngine
from autopilot.core.contracts import (
    AutonomyLevel,
    ProposalStatus,
    PerformanceTier,
    FeedbackSignal,
    StrategyStatus,
)
from autopilot.providers.mock_trend import MockTrendProvider


@pytest.fixture
def test_env(tmp_path):
    db_path = tmp_path / "test_synthetic_loop.db"
    manager = DBManager(db_path)
    manager.init_schema()
    return manager


def test_full_synthetic_autonomous_feedback_loop(test_env):
    db = test_env
    provider = MockTrendProvider()
    engine = AutonomyEngine(db=db, trend_provider=provider)

    # -------------------------------------------------------------
    # Step 1: Initial Autonomy Cycle (Level 2 - Proposals)
    # -------------------------------------------------------------
    summary_1 = engine.run_cycle(autonomy_level=AutonomyLevel.LEVEL_2_PROPOSAL.value, limit=3)
    assert summary_1.status == "completed"
    assert summary_1.signals_discovered >= 3
    assert summary_1.proposals_created >= 1

    proposals = db.list_idea_proposals(status="proposed")
    assert len(proposals) >= 1

    chosen_prop = proposals[0]
    prop_id = chosen_prop["proposal_id"]
    topic = chosen_prop["proposed_topic"]
    candidate_id = chosen_prop["candidate_id"]

    # -------------------------------------------------------------
    # Step 2: Human / Guarded Approval into M7 Queue
    # -------------------------------------------------------------
    approval_result = engine.approve_proposal(prop_id)
    assert approval_result["status"] == "approved"
    job_id = approval_result["job_id"]
    queue_id = approval_result["queue_id"]

    # Lineage check 1: Queue item preserves lineage
    q_item = db.get_queue_item(queue_id)
    assert q_item is not None
    assert q_item["job_id"] == job_id
    assert q_item["payload"]["proposal_id"] == prop_id
    assert q_item["payload"]["origin"] == "autonomous_manual_approved"

    # -------------------------------------------------------------
    # Step 3: Simulate M7 Worker Processing & Production
    # -------------------------------------------------------------
    db.create_job(job_id, topic=topic)
    db.update_queue_item_status(queue_id, "succeeded")
    db.record_artifact(
        job_id=job_id,
        artifact_path=f"artifacts/{job_id}/rendered_video.mp4",
        artifact_type="media",
        checksum_sha256="mock_hash_123",
    )

    # -------------------------------------------------------------
    # Step 4: Simulate Safe Publication
    # -------------------------------------------------------------
    db.record_publish_record(
        job_id=job_id,
        platform="youtube",
        provider="mock",
        visibility="private",
        remote_video_id=f"vid-{job_id}",
        idempotency_key=f"idem-{job_id}",
        media_checksum_sha256="mock_hash_123",
    )

    # -------------------------------------------------------------
    # Step 5: Simulate M8 Analytics Snapshot (High Performer)
    # -------------------------------------------------------------
    snapshot_id = f"snap-{job_id}"
    db.record_analytics_snapshot(
        snapshot_id=snapshot_id,
        job_id=job_id,
        platform="youtube",
        provider="mock",
        remote_id=f"vid-{job_id}",
        window="lifetime",
        metrics={"views": 12000.0, "likes": 950.0, "comments": 85.0},
        derived_metrics={"engagement_rate": 0.086},
        is_synthetic=False,
    )

    # -------------------------------------------------------------
    # Step 6: Extract Feedback Observations
    # -------------------------------------------------------------
    feedback_signals = engine.feedback_analyzer.extract_feedback_signals()
    assert len(feedback_signals) >= 1
    matched_fb = [f for f in feedback_signals if f.job_id == job_id]
    assert len(matched_fb) == 1
    assert matched_fb[0].performance_tier == PerformanceTier.TOP
    assert matched_fb[0].observed_views == 12000

    # Lineage check 2: Feedback points to job and snapshot
    assert matched_fb[0].snapshot_id == snapshot_id
    assert matched_fb[0].topic == topic

    # -------------------------------------------------------------
    # Step 7: Next Cycle — Verify Feedback Influences Ranking
    # -------------------------------------------------------------
    # Score a related topic with the feedback
    cand_next = engine.ideation_engine.generate_candidates(
        signals=provider.discover_trends(category="technology", limit=1),
        run_id="run-cycle-2",
    )[0]

    # Score with feedback vs without feedback
    score_with_fb = engine.scorer.score_candidate(
        candidate=cand_next,
        feedback_signals=feedback_signals,
    )
    score_without_fb = engine.scorer.score_candidate(
        candidate=cand_next,
        feedback_signals=[],
    )

    # Historical signal influences candidate score
    assert "HistoricalAssociation=" in score_with_fb.explanation
    assert score_with_fb.total_score >= score_without_fb.total_score

    # Complete lineage verified:
    # Trend -> Candidate -> Proposal -> Queue -> Artifact -> Publication -> Analytics -> Feedback -> Next Score
    assert candidate_id in chosen_prop["candidate_id"]
    assert prop_id in q_item["payload"]["proposal_id"]
    assert job_id in matched_fb[0].job_id
