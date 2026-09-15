"""Unit tests for Milestone 9 Policy Gate & Guardrails."""
import pytest
from autopilot.core.autonomy import PolicyGate
from autopilot.core.contracts import (
    TopicCandidate,
    TopicScore,
    AutonomyPolicy,
    DecisionAction,
)


def test_policy_gate_allow_valid_candidate():
    gate = PolicyGate(AutonomyPolicy())
    cand = TopicCandidate(
        candidate_id="tc-001",
        run_id="run-001",
        proposed_topic="Solid State Battery Technology",
        supporting_signal_ids=["sig-001"],
    )
    score = TopicScore(
        score_id="sc-001",
        candidate_id="tc-001",
        total_score=0.85,
    )

    decision = gate.evaluate_candidate(
        candidate=cand,
        score=score,
        autonomy_level=2,
        recent_topics=["Ancient History Secrets"],
        queue_pending_count=0,
        daily_job_count=0,
    )

    assert decision.action == DecisionAction.APPROVE
    assert "Passed all autonomy policy checks" in decision.reason
    assert all(c.passed for c in decision.checks)


def test_policy_gate_blocks_prohibited_keywords():
    policy = AutonomyPolicy(prohibited_topics=["get rich quick", "scam", "hate speech"])
    gate = PolicyGate(policy)

    cand = TopicCandidate(
        candidate_id="tc-002",
        run_id="run-001",
        proposed_topic="How to Get Rich Quick with Zero Effort in 2026",
        supporting_signal_ids=["sig-001"],
    )
    score = TopicScore(
        score_id="sc-002",
        candidate_id="tc-002",
        total_score=0.9,
    )

    decision = gate.evaluate_candidate(
        candidate=cand,
        score=score,
        autonomy_level=2,
        recent_topics=[],
        queue_pending_count=0,
        daily_job_count=0,
    )

    assert decision.action == DecisionAction.BLOCK
    assert any(("prohibited" in c.check_name or "content_safety" in c.check_name) and not c.passed for c in decision.checks)


def test_policy_gate_blocks_topic_cooldown_duplicate():
    policy = AutonomyPolicy(similarity_threshold=0.45)
    gate = PolicyGate(policy)

    cand = TopicCandidate(
        candidate_id="tc-003",
        run_id="run-001",
        proposed_topic="Quantum Computing Cryptography Revolution",
        supporting_signal_ids=["sig-001"],
    )
    score = TopicScore(
        score_id="sc-003",
        candidate_id="tc-003",
        total_score=0.85,
    )

    decision = gate.evaluate_candidate(
        candidate=cand,
        score=score,
        autonomy_level=2,
        recent_topics=["Quantum Computing Cryptography Breakthrough Announced"],
        queue_pending_count=0,
        daily_job_count=0,
    )

    assert decision.action == DecisionAction.BLOCK
    assert any(("duplicate" in c.check_name) and not c.passed for c in decision.checks)


def test_policy_gate_rejects_below_score_threshold():
    policy = AutonomyPolicy(min_score_threshold=0.60)
    gate = PolicyGate(policy)

    cand = TopicCandidate(
        candidate_id="tc-004",
        run_id="run-001",
        proposed_topic="Uncertain Low Quality Topic",
        supporting_signal_ids=["sig-001"],
    )
    score = TopicScore(
        score_id="sc-004",
        candidate_id="tc-004",
        total_score=0.45,  # Below threshold
    )

    decision = gate.evaluate_candidate(
        candidate=cand,
        score=score,
        autonomy_level=2,
        recent_topics=[],
        queue_pending_count=0,
        daily_job_count=0,
    )

    assert decision.action in (DecisionAction.BLOCK, DecisionAction.REJECT)
    assert any(("score" in c.check_name) and not c.passed for c in decision.checks)


def test_policy_gate_requires_evidence():
    policy = AutonomyPolicy(require_evidence=True)
    gate = PolicyGate(policy)

    cand = TopicCandidate(
        candidate_id="tc-005",
        run_id="run-001",
        proposed_topic="Unsubstantiated Rumor About Stars",
        supporting_signal_ids=[],  # No evidence
    )
    score = TopicScore(
        score_id="sc-005",
        candidate_id="tc-005",
        total_score=0.80,
    )

    decision = gate.evaluate_candidate(
        candidate=cand,
        score=score,
        autonomy_level=2,
        recent_topics=[],
        queue_pending_count=0,
        daily_job_count=0,
    )

    assert decision.action == DecisionAction.BLOCK
    assert any(("evidence" in c.check_name) and not c.passed for c in decision.checks)


def test_policy_gate_enforces_capacity_and_daily_limits():
    policy = AutonomyPolicy(max_concurrent_jobs=3, max_jobs_per_day=5)
    gate = PolicyGate(policy)

    cand = TopicCandidate(
        candidate_id="tc-006",
        run_id="run-001",
        proposed_topic="Solid State Battery Technology",
        supporting_signal_ids=["sig-001"],
    )
    score = TopicScore(score_id="sc-006", candidate_id="tc-006", total_score=0.85)

    # Queue at capacity
    decision_cap = gate.evaluate_candidate(
        candidate=cand,
        score=score,
        autonomy_level=3,
        queue_pending_count=5,
        daily_job_count=0,
    )
    assert decision_cap.action == DecisionAction.BLOCK

    # Daily limit exhausted
    decision_daily = gate.evaluate_candidate(
        candidate=cand,
        score=score,
        autonomy_level=3,
        queue_pending_count=0,
        daily_job_count=6,
    )
    assert decision_daily.action == DecisionAction.BLOCK
