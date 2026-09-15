"""Adversarial and Edge Case Tests for Milestone 9 Autonomous Ideation & Feedback Loop.
Covers the 14 explicit adversarial cases required by Section 29 of M9 specification.
"""
import pytest
from pathlib import Path

from autopilot.core.contracts import (
    AutonomyLevel,
    DecisionAction,
    TrendSignal,
    TopicCandidate,
    TopicScore,
    AutonomyPolicy,
    FeedbackSignal,
    LearningObservation,
    IdeaDecision,
    PerformanceTier,
    StrategyVersion,
    StrategyStatus,
)
from autopilot.core.ideation import IdeationEngine, DiversityFilter
from autopilot.core.topic_scoring import TopicScorer
from autopilot.core.feedback import FeedbackAnalyzer, StrategyManager
from autopilot.core.autonomy import PolicyGate, AutonomyEngine
from autopilot.db.manager import DBManager


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test_adversarial_autonomy.db"
    manager = DBManager(db_path)
    manager.init_schema()
    return manager


# 1. Same topic discovered from 10 sources
def test_case_1_same_topic_from_10_sources():
    engine = IdeationEngine()
    signals = [
        TrendSignal(
            signal_id=f"sig-{i}",
            topic="Quantum Computing Cryptography Breakthrough",
            source=f"source_{i}",
            freshness_score=0.9,
            relevance_score=0.9,
            evidence_text=f"Report from source {i}",
        )
        for i in range(10)
    ]
    candidates = engine.generate_candidates(signals=signals, run_id="run-adv-1")
    # Should consolidate rather than creating 10 identical candidates
    topics = [c.proposed_topic for c in candidates]
    unique_topics = set(topics)
    assert len(unique_topics) == len(topics)


# 2. Same topic generated repeatedly
def test_case_2_same_topic_generated_repeatedly():
    filter_engine = DiversityFilter()
    existing = ["Quantum Computing Cryptography Breakthrough"]
    risk = filter_engine.calculate_duplicate_risk(
        "Quantum Computing Cryptography Breakthrough",
        existing,
    )
    assert risk >= 0.95


# 3. One extremely successful video attempts to dominate future ideas
def test_case_3_outlier_dominance_protection(db):
    analyzer = FeedbackAnalyzer(db)
    # 1 video with 1,000,000 views
    signal = FeedbackSignal(
        signal_id="fb-outlier",
        job_id="job-outlier",
        snapshot_id="snap-outlier",
        topic="Extreme Viral Outlier Topic",
        performance_tier=PerformanceTier.TOP,
        observed_views=1000000,
        observed_engagement_rate=0.15,
    )
    # Should not trigger an automated strategy overhaul from a single video
    observations = analyzer.generate_learning_observations([signal], min_sample_size=3)
    assert len(observations) == 0


# 4. One extremely poor video attempts to eliminate an entire category
def test_case_4_poor_video_elimination_protection(db):
    analyzer = FeedbackAnalyzer(db)
    signal = FeedbackSignal(
        signal_id="fb-poor",
        job_id="job-poor",
        snapshot_id="snap-poor",
        topic="Technology Topic That Flop",
        performance_tier=PerformanceTier.LOW,
        observed_views=10,
        observed_engagement_rate=0.001,
    )
    observations = analyzer.generate_learning_observations([signal], min_sample_size=3)
    assert len(observations) == 0


# 5. Missing analytics (graceful fallback)
def test_case_5_missing_analytics_fallback():
    scorer = TopicScorer()
    cand = TopicCandidate(
        candidate_id="tc-adv-5",
        run_id="run-adv-5",
        proposed_topic="Unexplored Topic In Database",
    )
    # Score with empty feedback signals
    score = scorer.score_candidate(candidate=cand, feedback_signals=[])
    # Neutral historical factor (0.5), no exception raised
    assert score.historical_performance_factor == 0.5
    assert 0.0 <= score.total_score <= 1.0


# 6. Synthetic analytics handled properly
def test_case_6_synthetic_analytics_handled(db):
    analyzer = FeedbackAnalyzer(db)
    db.create_job("job-synth-1", topic="Synthetic Test Topic")
    db.record_publish_record(
        job_id="job-synth-1",
        platform="youtube",
        provider="mock",
        visibility="public",
        remote_video_id="vid-synth-1",
        idempotency_key="idem-synth-1",
        media_checksum_sha256="chk-synth-1",
    )
    db.record_analytics_snapshot(
        snapshot_id="snap-synth-1",
        job_id="job-synth-1",
        platform="youtube",
        provider="mock",
        remote_id="vid-synth-1",
        window="lifetime",
        metrics={"views": 500.0},
        is_synthetic=True,
    )
    signals = analyzer.extract_feedback_signals()
    assert len(signals) == 1
    assert signals[0].topic == "Synthetic Test Topic"


# 7. Missing source provenance detected
def test_case_7_missing_source_provenance():
    policy = AutonomyPolicy(require_evidence=True)
    gate = PolicyGate(policy)
    cand = TopicCandidate(
        candidate_id="tc-adv-7",
        run_id="run-adv-7",
        proposed_topic="Unverified Rumor Topic",
        supporting_signal_ids=[],  # Missing evidence provenance
    )
    score = TopicScore(score_id="sc-adv-7", candidate_id="tc-adv-7", total_score=0.8)
    decision = gate.evaluate_candidate(cand, score, autonomy_level=2)
    assert decision.action == DecisionAction.BLOCK
    assert any("evidence" in c.check_name and not c.passed for c in decision.checks)


# 8. Malformed trend response handled safely
def test_case_8_malformed_trend_response():
    engine = IdeationEngine()
    # Signal with weird characters or empty fields
    signal = TrendSignal(
        signal_id="sig-malformed",
        topic="!!!@#$%^&*()_+",
        evidence_text="",
    )
    candidates = engine.generate_candidates(signals=[signal], run_id="run-adv-8")
    assert isinstance(candidates, list)


# 9. Queue already at capacity
def test_case_9_queue_capacity_limit():
    policy = AutonomyPolicy(max_concurrent_jobs=3)
    gate = PolicyGate(policy)
    cand = TopicCandidate(
        candidate_id="tc-adv-9",
        run_id="run-adv-9",
        proposed_topic="Valid Topic",
        supporting_signal_ids=["sig-1"],
    )
    score = TopicScore(score_id="sc-adv-9", candidate_id="tc-adv-9", total_score=0.85)
    decision = gate.evaluate_candidate(cand, score, autonomy_level=3, queue_pending_count=4)
    assert decision.action == DecisionAction.BLOCK
    assert any(c.check_name == "queue_capacity" and not c.passed for c in decision.checks)


# 10. Daily autonomous limit exhausted
def test_case_10_daily_limit_exhausted():
    policy = AutonomyPolicy(max_jobs_per_day=5)
    gate = PolicyGate(policy)
    cand = TopicCandidate(
        candidate_id="tc-adv-10",
        run_id="run-adv-10",
        proposed_topic="Valid Topic",
        supporting_signal_ids=["sig-1"],
    )
    score = TopicScore(score_id="sc-adv-10", candidate_id="tc-adv-10", total_score=0.85)
    decision = gate.evaluate_candidate(cand, score, autonomy_level=3, daily_job_count=5)
    assert decision.action == DecisionAction.BLOCK
    assert any(c.check_name == "daily_limit" and not c.passed for c in decision.checks)


# 11. Strategy version changes during cycle
def test_case_11_strategy_version_change(db):
    manager = StrategyManager(db)
    active1 = manager.get_active_strategy()
    assert active1.version_id == "strat-v1"

    # Simulate mid-cycle change
    manager.propose_strategy_version(
        parent_version=active1,
        observations=[
            LearningObservation(
                observation_id="obs-11",
                pattern_type="high_performing_cluster",
                observation_text="Tech boost",
                sample_size=3,
                confidence=0.8,
                recommended_adjustment={"niche_weight_boost": {"technology": 0.5}},
            )
        ],
        rationale="Mid-cycle upgrade",
    )
    manager.activate_strategy("strat-v2")
    active2 = manager.get_active_strategy()
    assert active2.version_id == "strat-v2"

    # Verify previous version remains recorded
    versions = db.list_strategy_versions()
    assert len(versions) >= 2


# 12. Worker crashes halfway through cycle (recovery)
def test_case_12_crash_recovery(db):
    engine = AutonomyEngine(db=db)
    # Create stale running run
    db.record_autonomy_run("run-stale-001", autonomy_level=2)
    stale_run = db.get_autonomy_run("run-stale-001")
    assert stale_run["status"] == "running"

    recovered = engine.recover_stale_runs(max_age_seconds=0)
    assert "run-stale-001" in recovered
    updated_run = db.get_autonomy_run("run-stale-001")
    assert updated_run["status"] == "interrupted"


# 13. Candidate passes score but fails safety policy
def test_case_13_high_score_prohibited_content():
    policy = AutonomyPolicy(
        min_score_threshold=0.50,
        prohibited_topics=["harmful medical advice", "cure cancer with lemon"],
    )
    gate = PolicyGate(policy)
    cand = TopicCandidate(
        candidate_id="tc-adv-13",
        run_id="run-adv-13",
        proposed_topic="How to Cure Cancer with Lemon Juice in 3 Days",
        supporting_signal_ids=["sig-1"],
    )
    score = TopicScore(
        score_id="sc-adv-13",
        candidate_id="tc-adv-13",
        total_score=0.95,  # Very high score
    )
    decision = gate.evaluate_candidate(cand, score, autonomy_level=2)
    assert decision.action == DecisionAction.BLOCK
    assert any(("prohibited" in c.check_name or "content_safety" in c.check_name) and not c.passed for c in decision.checks)


# 14. Candidate passes ideation but fails downstream gates
def test_case_14_downstream_gates_mandatory(db):
    engine = AutonomyEngine(db=db)
    db.record_autonomy_run("run-adv-14", autonomy_level=2)
    # Propose and approve a topic
    cand = TopicCandidate(
        candidate_id="tc-adv-14",
        run_id="run-adv-14",
        proposed_topic="Legitimate Topic For Production",
        supporting_signal_ids=["sig-1"],
    )
    score = TopicScore(score_id="sc-adv-14", candidate_id="tc-adv-14", total_score=0.85)
    decision = IdeaDecision(
        decision_id="dec-adv-14",
        action=DecisionAction.APPROVE,
        autonomy_level=2,
        reason="Passed policy",
    )
    from autopilot.core.contracts import IdeaProposal, ProposalStatus
    prop = IdeaProposal(
        proposal_id="prop-adv-14",
        run_id="run-adv-14",
        candidate=cand,
        score=score,
        decision=decision,
        status=ProposalStatus.PROPOSED,
    )
    db.record_idea_proposal(prop)

    # Approve into queue
    res = engine.approve_proposal("prop-adv-14")
    assert res["status"] == "approved"
    job_id = res["job_id"]

    # Queue item starts at RESEARCH stage — NOT PUBLISH
    q_item = db.get_queue_item_by_job(job_id)
    assert q_item["stage"] == "RESEARCH"
    assert q_item["status"] == "queued"
