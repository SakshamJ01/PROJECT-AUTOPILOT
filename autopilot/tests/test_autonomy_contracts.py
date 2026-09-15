"""Unit tests for Milestone 9 Autonomy Contracts & Pydantic models."""
import pytest
from pydantic import ValidationError

from autopilot.core.contracts import (
    AutonomyLevel,
    ProposalStatus,
    DecisionAction,
    PerformanceTier,
    StrategyStatus,
    TrendSignal,
    TopicCandidate,
    TopicScore,
    AutonomyPolicy,
    PolicyCheckResult,
    IdeaDecision,
    IdeaProposal,
    FeedbackSignal,
    LearningObservation,
    StrategyVersion,
    AutonomyCycleSummary,
    ProvenanceRecord,
)


def test_autonomy_level_enums():
    assert AutonomyLevel.LEVEL_0_MANUAL.value == 0
    assert AutonomyLevel.LEVEL_1_DISCOVERY.value == 1
    assert AutonomyLevel.LEVEL_2_PROPOSAL.value == 2
    assert AutonomyLevel.LEVEL_3_AUTO_QUEUE.value == 3
    assert AutonomyLevel.LEVEL_4_AUTO_PRODUCE.value == 4


def test_proposal_status_enums():
    assert ProposalStatus.PROPOSED.value == "proposed"
    assert ProposalStatus.APPROVED.value == "approved"
    assert ProposalStatus.REJECTED.value == "rejected"
    assert ProposalStatus.QUEUED.value == "queued"


def test_decision_action_enums():
    assert DecisionAction.APPROVE.value == "approve"
    assert DecisionAction.REJECT.value == "reject"
    assert DecisionAction.QUEUE.value == "queue"
    assert DecisionAction.BLOCK.value == "block"


def test_trend_signal_validation():
    sig = TrendSignal(
        signal_id="sig-001",
        topic="Solid State Battery Breakthrough",
        source="arxiv_feed",
        source_url="https://arxiv.org/abs/example",
        freshness_score=0.95,
        relevance_score=0.9,
        category="science",
        confidence=0.88,
        evidence_text="Researchers demonstrated 1000 cycle stability at room temperature.",
    )
    assert sig.signal_id == "sig-001"
    assert sig.topic == "Solid State Battery Breakthrough"
    assert sig.freshness_score == 0.95

    # Bounds validation
    with pytest.raises(ValidationError):
        TrendSignal(signal_id="sig-bad", topic="Bad", freshness_score=1.5)


def test_topic_candidate_validation():
    cand = TopicCandidate(
        candidate_id="tc-001",
        run_id="run-001",
        proposed_topic="Solid State Battery Innovation",
        angle="Engineering Revolution",
        hook_hypothesis="Did you know EV range could double next year?",
        content_format="short_vertical",
        rationale="High trending interest and strong scientific evidence.",
        supporting_signal_ids=["sig-001"],
        confidence=0.85,
    )
    assert cand.candidate_id == "tc-001"
    assert cand.proposed_topic == "Solid State Battery Innovation"
    assert len(cand.supporting_signal_ids) == 1

    # Empty proposed_topic fails validation
    with pytest.raises(ValidationError):
        TopicCandidate(candidate_id="tc-002", run_id="run-001", proposed_topic="")


def test_topic_score_breakdown():
    score = TopicScore(
        score_id="sc-001",
        candidate_id="tc-001",
        freshness=0.9,
        relevance=0.8,
        historical_performance_factor=0.7,
        content_novelty=0.85,
        production_effort_factor=0.75,
        duplicate_risk_penalty=0.0,
        total_score=0.82,
        breakdown={"freshness": 0.9, "relevance": 0.8},
        explanation="High freshness and relevance.",
    )
    assert score.score_id == "sc-001"
    assert score.total_score == 0.82
    assert "freshness" in score.breakdown


def test_autonomy_policy_defaults():
    policy = AutonomyPolicy()
    assert policy.max_ideas_per_cycle == 10
    assert policy.max_auto_queue_per_cycle == 3
    assert policy.max_jobs_per_day == 10
    assert policy.topic_cooldown_days == 14
    assert policy.similarity_threshold == 0.70
    assert policy.min_score_threshold == 0.60
    assert policy.require_evidence is True
    assert "hate speech" in policy.prohibited_topics


def test_idea_decision_and_proposal():
    cand = TopicCandidate(
        candidate_id="tc-010",
        run_id="run-010",
        proposed_topic="Quantum Computing Post-Quantum Crypto",
    )
    score = TopicScore(
        score_id="sc-010",
        candidate_id="tc-010",
        total_score=0.85,
    )
    decision = IdeaDecision(
        decision_id="dec-010",
        action=DecisionAction.APPROVE,
        autonomy_level=2,
        reason="Passed all policy checks",
        checks=[
            PolicyCheckResult(check_name="evidence", passed=True, reason="Evidence verified"),
            PolicyCheckResult(check_name="duplicate", passed=True, reason="No duplicate found"),
        ],
    )
    proposal = IdeaProposal(
        proposal_id="prop-010",
        run_id="run-010",
        candidate=cand,
        score=score,
        decision=decision,
        status=ProposalStatus.PROPOSED,
    )
    assert proposal.proposal_id == "prop-010"
    assert proposal.status == ProposalStatus.PROPOSED
    assert proposal.decision.action == DecisionAction.APPROVE
    assert len(proposal.decision.checks) == 2


def test_strategy_version_and_feedback():
    strat = StrategyVersion(
        version_id="strat-v1",
        niche_weights={"technology": 1.0, "science": 1.0},
        rationale="Initial baseline",
    )
    assert strat.version_id == "strat-v1"
    assert strat.status == StrategyStatus.ACTIVE

    feedback = FeedbackSignal(
        signal_id="fb-001",
        job_id="job-001",
        snapshot_id="snap-001",
        topic="Quantum Breakthrough",
        performance_tier=PerformanceTier.TOP,
        observed_views=5000,
        observed_engagement_rate=0.08,
        association_note="Strong topic engagement",
    )
    assert feedback.performance_tier == PerformanceTier.TOP
    assert feedback.observed_views == 5000


def test_cycle_summary():
    summary = AutonomyCycleSummary(
        run_id="run-100",
        autonomy_level=2,
        signals_discovered=5,
        candidates_generated=5,
        proposals_created=3,
        jobs_queued=0,
        jobs_blocked=2,
        status="completed",
        active_strategy_version="strat-v1",
    )
    assert summary.run_id == "run-100"
    assert summary.signals_discovered == 5
    assert summary.jobs_blocked == 2
    assert summary.status == "completed"
