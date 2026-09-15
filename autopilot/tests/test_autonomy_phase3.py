"""Phase 3 Tests — Autonomous Ideation, Strategy Auto-Tuning, and Approval Governance."""
import pytest
from autopilot.core.config import Config
from autopilot.db.manager import DBManager
from autopilot.core.contracts import (
    StrategyVersion,
    LearningObservation,
    TrendSignal,
    TopicCandidate,
    AutonomyLevel,
)
from autopilot.core.feedback import StrategyManager, FeedbackAnalyzer
from autopilot.core.ideation import IdeationEngine, DiversityFilter
from autopilot.core.autonomy import AutonomyEngine


@pytest.fixture
def mock_db(tmp_path):
    db_path = tmp_path / "test_autonomy_phase3.db"
    db = DBManager(str(db_path))
    db.init_schema()
    return db


def test_strategy_learning_minimum_sample_size(mock_db):
    """Strategy manager must NOT propose strategy adjustments if sample size < 3."""
    sm = StrategyManager(db=mock_db)
    strat = StrategyVersion(version_id="strat-v1", niche_weights={"technology": 1.0})
    mock_db.record_strategy_version(strat)

    # Empty or insufficient observations (< 3)
    obs_empty = []
    res_none = sm.propose_strategy_version(parent_version=strat, observations=obs_empty, rationale="Empty test")
    assert res_none is None

    # Only 2 signals should not yield learning observation in FeedbackAnalyzer
    fa = FeedbackAnalyzer(db=mock_db)
    obs = fa.analyze_learning_observations(signals=[], min_sample_size=3)
    assert len(obs) == 0


def test_strategy_learning_bounded_weight_updates(mock_db):
    """Weight adjustments must be strictly bounded (max step 0.15, clamped 0.5 to 1.5)."""
    sm = StrategyManager(db=mock_db)
    parent_strat = StrategyVersion(
        version_id="strat-v1",
        niche_weights={"technology": 1.0, "science": 1.45},
    )
    mock_db.record_strategy_version(parent_strat)

    obs = [
        LearningObservation(
            observation_id="learn-1",
            sample_size=4,
            confidence=0.8,
            recommended_adjustment={"niche_boost": 0.10},  # within 0.15 step
        )
    ]

    new_strat = sm.propose_strategy_version(parent_version=parent_strat, observations=obs, rationale="Test bounded update")
    assert new_strat is not None
    assert new_strat.version_id == "strat-v2"
    # technology: 1.0 + 0.10 = 1.10
    assert new_strat.niche_weights["technology"] == 1.10
    # science: min(1.5, 1.45 + 0.10) = 1.50 (clamped)
    assert new_strat.niche_weights["science"] == 1.50


def test_topic_deduplication_recency_window():
    """Duplicate topics within cooldown window are flagged with high duplicate risk."""
    div = DiversityFilter()
    recent = [
        "Inside Artificial Intelligence: Technical Breakdown",
        "Understanding Quantum Computers",
    ]

    # Exact match -> risk 1.0
    risk_exact = div.calculate_duplicate_risk("Inside Artificial Intelligence: Technical Breakdown", recent)
    assert risk_exact == 1.0

    # Strong similarity -> risk > 0.6
    risk_similar = div.calculate_duplicate_risk("Artificial Intelligence Technical Breakdown", recent)
    assert risk_similar >= 0.6

    # Novel topic -> risk < 0.2
    risk_novel = div.calculate_duplicate_risk("The Complete History of the Roman Republic", recent)
    assert risk_novel < 0.2


def test_assisted_mode_approval_boundary(mock_db, tmp_path):
    """In assisted mode, publishing MUST halt at approval boundary and persist pending approval."""
    cfg = Config(artifacts_dir=str(tmp_path), db_path=str(mock_db.db_path))
    engine = AutonomyEngine(config=cfg, db=mock_db)

    cycle_res = engine.run_autonomous_cycle(
        seed_topic="3 surprising facts about artificial intelligence",
        channel_id="tech_shorts",
        mode="assisted",
        production_engine="ffmpeg",
        dry_run=True,
    )

    assert cycle_res["mode"] == "assisted"
    assert cycle_res["approval_status"] == "pending"

    # Verify persistent approval record exists in DB
    job_id = cycle_res["job_id"]
    approval = mock_db.get_publish_approval(job_id)
    assert approval is not None
    assert approval["status"] == "pending"

    # Rejecting updates DB
    rej_res = engine.reject_publish(job_id, notes="Test reject")
    assert rej_res["status"] == "rejected"
    assert mock_db.get_publish_approval(job_id)["status"] == "rejected"
