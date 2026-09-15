"""Unit tests for Milestone 9 Deterministic Topic Scoring."""
import pytest
from autopilot.core.topic_scoring import TopicScorer
from autopilot.core.contracts import (
    TopicCandidate,
    TrendSignal,
    FeedbackSignal,
    PerformanceTier,
)


def test_topic_scoring_basic():
    scorer = TopicScorer()
    cand = TopicCandidate(
        candidate_id="tc-001",
        run_id="run-001",
        proposed_topic="Quantum Computing Cryptography Breakthrough",
        duplicate_risk=0.0,
        commercial_relevance=0.85,
    )
    signal = TrendSignal(
        signal_id="sig-001",
        topic="Quantum Computing",
        freshness_score=0.9,
        relevance_score=0.85,
    )

    score = scorer.score_candidate(
        candidate=cand,
        supporting_signals=[signal],
        feedback_signals=[],
    )

    assert 0.0 <= score.total_score <= 1.0
    assert score.freshness == 0.9
    assert score.relevance == 0.85
    assert score.content_novelty == 1.0  # 1.0 - 0.0 duplicate_risk
    assert score.duplicate_risk_penalty == 0.0
    assert len(score.explanation) > 0
    assert "freshness" in score.breakdown


def test_topic_scoring_duplicate_penalty():
    scorer = TopicScorer()
    cand = TopicCandidate(
        candidate_id="tc-002",
        run_id="run-001",
        proposed_topic="Repeated Topic",
        duplicate_risk=0.8,
    )
    score = scorer.score_candidate(candidate=cand)

    assert score.duplicate_risk_penalty > 0.20
    assert score.content_novelty == pytest.approx(0.2)  # 1.0 - 0.8
    assert score.total_score < 0.5


def test_topic_scoring_with_historical_feedback():
    scorer = TopicScorer()
    cand = TopicCandidate(
        candidate_id="tc-003",
        run_id="run-001",
        proposed_topic="Quantum Advances",
        duplicate_risk=0.0,
    )
    signal = TrendSignal(
        signal_id="sig-001",
        topic="Quantum",
        freshness_score=0.8,
        relevance_score=0.8,
    )
    top_feedback = [
        FeedbackSignal(
            signal_id="fb-001",
            job_id="job-001",
            snapshot_id="snap-001",
            topic="Quantum Computing Overview",
            performance_tier=PerformanceTier.TOP,
            observed_views=8000,
            observed_engagement_rate=0.09,
        )
    ]

    score_top = scorer.score_candidate(
        candidate=cand,
        supporting_signals=[signal],
        feedback_signals=top_feedback,
    )
    assert score_top.historical_performance_factor > 0.5

    low_feedback = [
        FeedbackSignal(
            signal_id="fb-002",
            job_id="job-002",
            snapshot_id="snap-002",
            topic="Quantum Computing Overview",
            performance_tier=PerformanceTier.LOW,
            observed_views=200,
            observed_engagement_rate=0.01,
        )
    ]
    score_low = scorer.score_candidate(
        candidate=cand,
        supporting_signals=[signal],
        feedback_signals=low_feedback,
    )
    assert score_low.historical_performance_factor < 0.5
    assert score_top.total_score > score_low.total_score


def test_topic_scoring_explainability():
    scorer = TopicScorer()
    cand = TopicCandidate(
        candidate_id="tc-004",
        run_id="run-001",
        proposed_topic="Artificial Intelligence in Healthcare",
    )
    score = scorer.score_candidate(candidate=cand)
    assert "Score" in score.explanation
    assert "Freshness=" in score.explanation
    assert "Relevance=" in score.explanation
    assert "Novelty=" in score.explanation
