"""Unit tests for Milestone 9 Autonomy SQLite persistence and migrations."""
import pytest
from pathlib import Path

from autopilot.db.manager import DBManager, DB_SCHEMA_VERSION
from autopilot.core.contracts import (
    AutonomyLevel,
    ProposalStatus,
    DecisionAction,
    PerformanceTier,
    StrategyStatus,
    TrendSignal,
    TopicCandidate,
    TopicScore,
    IdeaDecision,
    IdeaProposal,
    FeedbackSignal,
    StrategyVersion,
)


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test_autonomy.db"
    manager = DBManager(db_path)
    manager.init_schema()
    return manager


def test_schema_version_is_six(db):
    assert DB_SCHEMA_VERSION >= 6
    with db._connect() as conn:
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        assert row["version"] >= 6


def test_autonomy_run_lifecycle(db):
    run_id = "run-test-001"
    db.record_autonomy_run(run_id, autonomy_level=2, strategy_version="strat-v1")
    
    run = db.get_autonomy_run(run_id)
    assert run is not None
    assert run["run_id"] == run_id
    assert run["autonomy_level"] == 2
    assert run["strategy_version"] == "strat-v1"
    assert run["status"] == "running"

    db.update_autonomy_run(
        run_id=run_id,
        status="completed",
        signals_discovered=5,
        candidates_generated=4,
        proposals_created=3,
        jobs_queued=0,
    )

    updated = db.get_autonomy_run(run_id)
    assert updated["status"] == "completed"
    assert updated["signals_discovered"] == 5
    assert updated["proposals_created"] == 3
    assert updated["completed_at"] is not None


def test_trend_signals_persistence(db):
    run_id = "run-test-002"
    db.record_autonomy_run(run_id, autonomy_level=1)

    signal = TrendSignal(
        signal_id="sig-db-001",
        topic="Breakthrough in Quantum Computing",
        source="mock_trend",
        freshness_score=0.9,
        relevance_score=0.85,
        category="technology",
    )
    db.record_trend_signal(signal, run_id=run_id)

    signals = db.get_trend_signals_for_run(run_id)
    assert len(signals) == 1
    assert signals[0]["signal_id"] == "sig-db-001"
    assert signals[0]["topic"] == "Breakthrough in Quantum Computing"
    assert signals[0]["freshness_score"] == 0.9


def test_topic_candidate_and_score_persistence(db):
    run_id = "run-test-003"
    db.record_autonomy_run(run_id, autonomy_level=2)
    cand = TopicCandidate(
        candidate_id="tc-db-001",
        run_id=run_id,
        proposed_topic="Solid State Battery Innovations",
        angle="Engineering Revolution",
        hook_hypothesis="Did you know EV charging will soon take 5 minutes?",
        confidence=0.85,
    )
    db.record_topic_candidate(cand)

    retrieved = db.get_topic_candidate("tc-db-001")
    assert retrieved is not None
    assert retrieved["proposed_topic"] == "Solid State Battery Innovations"

    score = TopicScore(
        score_id="sc-db-001",
        candidate_id="tc-db-001",
        freshness=0.9,
        relevance=0.8,
        total_score=0.85,
        explanation="High test score",
    )
    db.record_topic_score(score)

    retrieved_score = db.get_topic_score("tc-db-001")
    assert retrieved_score is not None
    assert retrieved_score["total_score"] == 0.85


def test_idea_proposal_and_decision_lifecycle(db):
    run_id = "run-test-004"
    db.record_autonomy_run(run_id, autonomy_level=2)

    cand = TopicCandidate(
        candidate_id="tc-db-002",
        run_id=run_id,
        proposed_topic="AI Code Agents in 2026",
    )
    score = TopicScore(
        score_id="sc-db-002",
        candidate_id="tc-db-002",
        total_score=0.88,
    )
    decision = IdeaDecision(
        decision_id="dec-db-002",
        action=DecisionAction.APPROVE,
        autonomy_level=2,
        reason="Passed all policy checks",
    )
    proposal = IdeaProposal(
        proposal_id="prop-db-002",
        run_id=run_id,
        candidate=cand,
        score=score,
        decision=decision,
        status=ProposalStatus.PROPOSED,
    )

    db.record_idea_proposal(proposal)

    p_row = db.get_idea_proposal("prop-db-002")
    assert p_row is not None
    assert p_row["status"] == "proposed"
    assert p_row["proposed_topic"] == "AI Code Agents in 2026"
    assert p_row["total_score"] == 0.88

    # Check decision
    d_row = db.get_decision_for_proposal("prop-db-002")
    assert d_row is not None
    assert d_row["action"] == "approve"

    # Status transition
    db.update_proposal_status("prop-db-002", "approved", reason="Approved by operator")
    p_updated = db.get_idea_proposal("prop-db-002")
    assert p_updated["status"] == "approved"
    assert p_updated["decision_reason"] == "Approved by operator"


def test_strategy_version_persistence_and_activation(db):
    strat1 = StrategyVersion(
        version_id="strat-v1",
        niche_weights={"technology": 1.0, "science": 1.0},
        rationale="Initial baseline",
    )
    db.record_strategy_version(strat1)

    active = db.get_active_strategy()
    assert active.version_id == "strat-v1"

    strat2 = StrategyVersion(
        version_id="strat-v2",
        parent_version_id="strat-v1",
        status=StrategyStatus.ACTIVE,
        niche_weights={"technology": 1.2, "science": 0.9},
        rationale="Updated based on high engagement with technology topics",
    )
    db.record_strategy_version(strat2)

    active_now = db.get_active_strategy()
    assert active_now.version_id == "strat-v2"

    # Verify strat-v1 is now deprecated
    all_strats = db.list_strategy_versions()
    assert len(all_strats) == 2
    strat_map = {s.version_id: s.status for s in all_strats}
    assert strat_map["strat-v1"] == StrategyStatus.DEPRECATED
    assert strat_map["strat-v2"] == StrategyStatus.ACTIVE

    # Rollback to strat-v1
    db.set_active_strategy("strat-v1")
    active_rollback = db.get_active_strategy()
    assert active_rollback.version_id == "strat-v1"


def test_feedback_observation_persistence(db):
    signal = FeedbackSignal(
        signal_id="fb-db-001",
        job_id="job-001",
        snapshot_id="snap-db-001",
        topic="Neural Interfaces",
        observed_views=4200,
        observed_engagement_rate=0.075,
        performance_tier=PerformanceTier.TOP,
        association_note="Strong topic engagement",
    )
    db.record_feedback_observation(signal)

    obs_list = db.list_feedback_observations()
    assert len(obs_list) == 1
    assert obs_list[0]["observation_id"] == "fb-db-001"
    assert obs_list[0]["topic"] == "Neural Interfaces"
    assert obs_list[0]["performance_tier"] == "top"


def test_recent_topics_and_daily_job_count(db):
    # Enqueue standard job
    db.create_job("job-rec-001", topic="Recent Topic 1")

    # Autonomous proposal
    run_id = "run-rec-001"
    db.record_autonomy_run(run_id, autonomy_level=2)
    cand = TopicCandidate(
        candidate_id="tc-rec-002",
        run_id=run_id,
        proposed_topic="Recent Autonomous Topic",
    )
    score = TopicScore(
        score_id="sc-rec-002",
        candidate_id="tc-rec-002",
        total_score=0.8,
    )
    proposal = IdeaProposal(
        proposal_id="prop-rec-002",
        run_id=run_id,
        candidate=cand,
        score=score,
        status=ProposalStatus.APPROVED,
    )
    db.record_idea_proposal(proposal)

    recent = db.get_recent_topics(days=14)
    assert "Recent Topic 1" in recent
    assert "Recent Autonomous Topic" in recent

    # Count autonomous jobs queued today
    assert db.count_autonomous_jobs_queued_today() == 0
    db.enqueue_item(
        queue_id="q-auto-001",
        job_id="job-auto-001",
        content_id="job-auto-001",
        payload={"topic": "Auto Topic", "origin": "autonomous"},
    )
    assert db.count_autonomous_jobs_queued_today() == 1


def test_get_recent_topics_includes_proposed_status_and_enforces_cooldown(db):
    """Proves that a recent proposal in 'proposed' status is returned by get_recent_topics()
    and correctly provides duplicate protection on a subsequent autonomy cycle.
    """
    run_id = "run-test-proposed-cooldown"
    db.record_autonomy_run(run_id, autonomy_level=2)

    # Title as formatted by IdeationEngine for technology niche
    topic_title = "The Truth Behind Quantum Computing Post-Quantum Cryptography Breakthrough"
    cand = TopicCandidate(
        candidate_id="tc-prop-001",
        run_id=run_id,
        proposed_topic=topic_title,
    )
    score = TopicScore(
        score_id="sc-prop-001",
        candidate_id="tc-prop-001",
        total_score=0.85,
    )
    proposal = IdeaProposal(
        proposal_id="prop-test-001",
        run_id=run_id,
        candidate=cand,
        score=score,
        status=ProposalStatus.PROPOSED,  # Status is PROPOSED (pending / unapproved)
    )
    db.record_idea_proposal(proposal)

    # 1. get_recent_topics must return the proposed topic
    recent = db.get_recent_topics(days=14)
    assert topic_title in recent

    # 2. Subsequent autonomy cycle candidate generation detects duplicate risk against the proposed topic
    from autopilot.core.ideation import IdeationEngine, DiversityFilter
    from autopilot.providers.mock_trend import MockTrendProvider

    provider = MockTrendProvider()
    signals = provider.discover_trends(limit=1)
    engine = IdeationEngine(diversity_filter=DiversityFilter())
    new_candidates = engine.generate_candidates(
        signals=signals,
        recent_topics=recent,
        run_id="run-cycle-2",
    )
    assert len(new_candidates) > 0
    matching_cand = next(c for c in new_candidates if "Quantum Computing" in c.proposed_topic)
    assert matching_cand.duplicate_risk >= 0.70


