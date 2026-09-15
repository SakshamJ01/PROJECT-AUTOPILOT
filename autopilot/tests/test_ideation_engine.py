"""Unit tests for Milestone 9 Ideation & Diversity Engine."""
import pytest
from autopilot.core.ideation import IdeationEngine, DiversityFilter
from autopilot.core.contracts import TrendSignal, StrategyVersion


def test_diversity_filter_tokenization():
    filter_engine = DiversityFilter()
    tokens = filter_engine.tokenize("What is the Future of Quantum Computing in 2026?")
    assert "quantum" in tokens
    assert "computing" in tokens
    assert "future" in tokens
    assert "2026" in tokens
    # Stop words filtered
    assert "what" not in tokens
    assert "is" not in tokens
    assert "the" not in tokens


def test_diversity_filter_similarity():
    filter_engine = DiversityFilter()
    # High similarity
    sim1 = filter_engine.calculate_similarity(
        "Quantum Computing Breakthrough in Cryptography",
        "Quantum Computing Cryptography Breakthrough Announced",
    )
    assert sim1 >= 0.70

    # Low similarity
    sim2 = filter_engine.calculate_similarity(
        "Quantum Computing Cryptography",
        "Ancient Egyptian Pyramids Mystery",
    )
    assert sim2 == 0.0


def test_diversity_filter_duplicate_risk():
    filter_engine = DiversityFilter()
    existing_topics = [
        "Quantum Computing Cryptography Revolution",
        "Solid State Batteries for Electric Vehicles",
    ]

    # Matching topic gets high duplicate risk
    risk_high = filter_engine.calculate_duplicate_risk(
        "Quantum Computing Cryptography Revolution",
        existing_topics,
    )
    assert risk_high >= 0.9

    # Novel topic gets low duplicate risk
    risk_low = filter_engine.calculate_duplicate_risk(
        "Deep Sea Bioluminescence Mysteries",
        existing_topics,
    )
    assert risk_low == 0.0


def test_ideation_engine_generation():
    engine = IdeationEngine()
    signals = [
        TrendSignal(
            signal_id="sig-001",
            topic="Artificial General Intelligence Reasoning",
            category="technology",
            freshness_score=0.9,
            relevance_score=0.9,
            evidence_text="New paper reveals multi-step reasoning benchmarks.",
        ),
        TrendSignal(
            signal_id="sig-002",
            topic="Nuclear Fusion Energy Milestone",
            category="science",
            freshness_score=0.88,
            relevance_score=0.85,
            evidence_text="Steady state high-confinement maintained for 1000 seconds.",
        ),
    ]

    strategy = StrategyVersion(
        version_id="strat-v1",
        hook_patterns=["Did you know {fact}? Here is what happens next."],
    )

    candidates = engine.generate_candidates(
        signals=signals,
        run_id="run-test",
        strategy=strategy,
        existing_topics=["Ancient Rome History"],
    )

    assert len(candidates) == 2
    for cand in candidates:
        assert len(cand.candidate_id) > 0
        assert len(cand.proposed_topic) > 0
        assert len(cand.angle) > 0
        assert len(cand.hook_hypothesis) > 0
        assert cand.content_format == "short_vertical"
        assert len(cand.supporting_signal_ids) == 1
        assert cand.duplicate_risk < 0.5


def test_ideation_engine_empty_signals():
    engine = IdeationEngine()
    candidates = engine.generate_candidates(signals=[], run_id="run-test")
    assert candidates == []
