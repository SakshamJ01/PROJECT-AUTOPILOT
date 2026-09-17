"""Phase 3 Analytics-Driven Feedback & Strategy Learning tests.

Covers the full learning boundary contract (28+ required scenarios):
aggregation, eligibility, sync-failure handling, channel isolation, bounds,
versioning, idempotency, dry-run, policy/publishing/production immutability,
category resolution (lineage/keyword/unresolved), strategy-bonus ideation
integration, explainability, CLI, scheduler opt-in stage, operation-loop
compatibility, restart safety, and provider isolation.

TEST ISOLATION: per-test tmp_path DBs, deterministic synthetic analytics only,
no network providers, no writes to the production DB or artifact tree.  The
structured loggers used by scheduler/autonomy are stubbed to in-memory
recorders (same convention as test_operation_loop.py).
"""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autopilot.core.autonomy import AutonomyEngine
from autopilot.core.channel import ChannelManager
from autopilot.core.config import Config, CONFIG
from autopilot.core.constants import LEARNING_FORBIDDEN_MODULES
from autopilot.core.contracts import (
    AutoProduceSummary,
    AutonomyCycleSummary,
    ChannelProfile,
    LearningRunStatus,
    LearningRunSummary,
    NicheConfig,
    StrategyVersion,
    TopicCandidate,
    TrendSignal,
)
from autopilot.core.feedback import FeedbackAnalyzer, StrategyManager
from autopilot.core.learning import PUBLISHED_STATUSES, LearningEngine
from autopilot.core.scheduler import ScheduleEngine
from autopilot.core.topic_scoring import TopicScorer
from autopilot.db.manager import DBManager
from autopilot.providers.mock_trend import MockTrendProvider

NOW = datetime(2026, 9, 17, 10, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

class _Recorder:
    """In-memory structured-logger stub (no file writes during tests)."""

    def __init__(self, *args, **kwargs):
        self.events = []

    def _record(self, level, event, details, error):
        self.events.append({"level": level, "event": event, "details": details or {}, "error": error})

    def info(self, event, details=None):
        self._record("INFO", event, details, None)

    def warning(self, event, details=None):
        self._record("WARNING", event, details, None)

    def error(self, event, error=None, details=None):
        self._record("ERROR", event, details, error)

    def debug(self, event, details=None):
        self._record("DEBUG", event, details, None)


@pytest.fixture(autouse=True)
def _stub_loggers(monkeypatch):
    from autopilot.core import autonomy as autonomy_mod
    from autopilot.core import scheduler as scheduler_mod
    from autopilot.core import worker as worker_mod

    monkeypatch.setattr(scheduler_mod, "StructuredLogger", _Recorder)
    monkeypatch.setattr(autonomy_mod, "StructuredLogger", _Recorder)
    monkeypatch.setattr(worker_mod, "StructuredLogger", _Recorder)


@pytest.fixture
def db(tmp_path):
    d = DBManager(str(tmp_path / "test_analytics_learning.db"))
    d.init_schema()
    return d


@pytest.fixture
def cfg(tmp_path, db):
    return Config(
        artifacts_dir=str(tmp_path / "artifacts"),
        db_path=str(db.db_path),
        learning_min_samples=5,
        learning_min_category_observations=2,
        strategy_max_params_per_update=3,
    )


def make_engine(cfg, db):
    return LearningEngine(config=cfg, db=db)


def age_published(db, job_id, days):
    dt = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with db._connect() as conn:
        conn.execute("UPDATE publish_records SET created_at = ? WHERE job_id = ?", (dt, job_id))
        conn.commit()


def seed_published(
    db,
    job_id,
    topic,
    views,
    likes=10.0,
    comments=1.0,
    shares=0.0,
    channel="default",
    age_days=5.0,
    status="SUCCESS",
    snapshot=True,
    is_synthetic=False,
):
    """Deterministic published job + analytics snapshot (ageable publish date)."""
    db.create_job(job_id=job_id, channel_id=channel, topic=topic)
    db.record_publish_record(job_id=job_id, remote_video_id=f"vid-{job_id}", status=status)
    if age_days is not None:
        age_published(db, job_id, age_days)
    if snapshot:
        db.record_analytics_snapshot(
            snapshot_id=f"snap-{job_id}",
            job_id=job_id,
            platform="youtube",
            provider="mock",
            remote_id=f"vid-{job_id}",
            window="lifetime",
            metrics={
                "views": float(views),
                "likes": float(likes),
                "comments": float(comments),
                "shares": float(shares),
            },
            is_synthetic=is_synthetic,
        )


def seed_standard(db, channel="default", synthetic=False):
    """4 strong technology + 2 weak finance -> a clean technology-boost signal."""
    seed_published(db, "job-t1", "Quantum Computing Breakthrough Explained", 4500, 200, 10, 5, channel=channel, is_synthetic=synthetic)
    seed_published(db, "job-t2", "Neural Network Architecture Deep Dive", 5000, 220, 12, 6, channel=channel, is_synthetic=synthetic)
    seed_published(db, "job-t3", "AI Robot Engineering Advances", 5500, 240, 14, 7, channel=channel, is_synthetic=synthetic)
    seed_published(db, "job-t4", "Cybersecurity Software Guide", 4000, 150, 8, 4, channel=channel, is_synthetic=synthetic)
    seed_published(db, "job-f1", "Stock Market Investing Basics", 500, 2, 0, 0, channel=channel, is_synthetic=synthetic)
    seed_published(db, "job-f2", "Personal Finance Money Tips", 800, 4, 1, 0, channel=channel, is_synthetic=synthetic)


def create_channel(db, channel_id, niche="technology"):
    cm = ChannelManager(db)
    cm.create_channel(ChannelProfile(channel_id=channel_id, channel_name=channel_id.title(), niche=NicheConfig(niche_name=niche)))
    return cm


def make_scheduler(tmp_path, db, engine, **kwargs):
    cfg = Config(artifacts_dir=str(tmp_path / "artifacts"), db_path=str(db.db_path))
    return ScheduleEngine(config=cfg, db=db, engine=engine, **kwargs)


# 1. Basic aggregation ---------------------------------------------------------

def test_basic_aggregation_signals_and_weights(cfg, db):
    seed_standard(db)
    eng = make_engine(cfg, db)

    obs, excluded = eng.collect_observations(channel_id="default")
    assert len(obs) == 6
    assert excluded == {}

    tiers = eng.compute_tiers(obs)
    assert tiers["job-f1"].value == "low"
    assert tiers["job-t1"].value == "top"

    summary = eng.update_strategy(channel_id="default")
    assert summary.status == LearningRunStatus.APPLIED.value
    assert summary.observations_used == 6
    # Technology boosted upward, finance pulled downward.  Both bounded.
    params = {d.parameter: d for d in summary.deltas}
    assert params["technology"].new_value > params["technology"].old_value
    assert params["finance"].new_value < params["finance"].old_value
    assert summary.signals_summary["bounds"]["min_samples"] == cfg.learning_min_samples
    assert summary.signals_summary["tier_counts"]["low"] == 2


# 2. Incomplete analytics is excluded ------------------------------------------

def test_incomplete_analytics_excluded(cfg, db):
    seed_standard(db)
    seed_published(db, "job-noanalytics-1", "Quantum Mystery", 9999, snapshot=False)
    seed_published(db, "job-noanalytics-2", "Tech Topic Unmeasured", 8888, snapshot=False)
    eng = make_engine(cfg, db)

    summary = eng.update_strategy(channel_id="default")
    assert summary.status == LearningRunStatus.APPLIED.value
    assert summary.excluded_reasons.get("no_analytics") == 2
    assert "job-noanalytics-1" not in summary.observation_ids
    assert "job-noanalytics-2" not in summary.observation_ids


# 3. Failed sync is never poor performance -------------------------------------

def test_sync_failure_not_poor_performance(cfg, db):
    seed_standard(db)
    # The "failed sync" job published but its analytics never landed => no snapshot.
    seed_published(db, "job-syncfail", "Finance Fail Topic", 0, snapshot=False)
    eng = make_engine(cfg, db)

    summary = eng.update_strategy(channel_id="default")
    assert summary.status == LearningRunStatus.APPLIED.value
    assert summary.excluded_reasons.get("no_analytics") == 1
    # Absolutely never allowed to contribute to a LOW/TOP signal.
    all_source_ids = [jid for d in summary.deltas for jid in d.source_job_ids]
    assert "job-syncfail" not in all_source_ids


# 4 & 23. Channel isolation (observation scope) ---------------------------------

def test_channel_isolation_observations(cfg, db):
    for i in range(1, 7):
        seed_published(db, f"job-a-{i}", f"AI Technology Innovation {i}", 5000 + i * 10, channel="tech_a")
    for i in range(1, 7):
        seed_published(db, f"job-b-{i}", f"Biology Ocean Science {i}", 100 + i * 10, channel="science_b")
    eng = make_engine(cfg, db)

    obs_a, _ = eng.collect_observations(channel_id="tech_a")
    obs_b, _ = eng.collect_observations(channel_id="science_b")
    assert obs_a and all(o["channel_id"] == "tech_a" for o in obs_a)
    assert obs_b and all(o["channel_id"] == "science_b" for o in obs_b)
    assert not ({o["job_id"] for o in obs_a} & {o["job_id"] for o in obs_b})


# 5. Min sample gate --------------------------------------------------------------

def test_min_sample_gate(cfg, db):
    seed_published(db, "job-1", "Quantum Tech", 5000)
    seed_published(db, "job-2", "Finance Basics", 400)
    eng = make_engine(cfg, db)

    summary = eng.update_strategy(channel_id="default")
    assert summary.status == LearningRunStatus.INSUFFICIENT.value
    assert summary.resulting_strategy_version is None
    assert eng.strategy_manager.get_active_strategy().version_id == "strat-v1"


# 6 & 7. Bounded deltas (cap at max_weight_delta) --------------------------------

def test_bounded_delta_hard_cap(cfg, db):
    eng = make_engine(cfg, db)
    bounds = eng._bounds()
    max_delta = bounds["max_weight_delta"]
    parent = StrategyVersion(version_id="strat-v1")

    positive = {
        "finance": {
            "category": "finance", "sample_size": 10, "tier_score": 1.0, "signal": 1.0,
            "confidence": 1.0, "avg_engagement_rate": 0.01, "avg_views_per_day": 100,
            "source_job_ids": ["j"] * 10, "synthetic_count": 0,
        }
    }
    neg = {
        "finance": {
            "category": "finance", "sample_size": 10, "tier_score": 0.0, "signal": -1.0,
            "confidence": 1.0, "avg_engagement_rate": 0.01, "avg_views_per_day": 10,
            "source_job_ids": ["j"] * 10, "synthetic_count": 0,
        }
    }
    up = eng.compute_bounded_deltas(parent, positive, bounds)
    down = eng.compute_bounded_deltas(parent, neg, bounds)
    assert len(up) == 1 and up[0].applied_delta == pytest.approx(max_delta)
    assert len(down) == 1 and down[0].applied_delta == pytest.approx(-max_delta)
    assert up[0].new_value <= cfg.strategy_weight_ceiling
    assert down[0].new_value >= cfg.strategy_weight_floor


# 8. Max params per update --------------------------------------------------------

def test_max_params_per_update_cap(cfg, db):
    eng = make_engine(cfg, db)
    bounds = eng._bounds()
    parent = StrategyVersion(version_id="strat-v1")
    signals = {}
    for i, cat in enumerate(["technology", "science", "history", "finance", "general"]):
        signals[cat] = {
            "category": cat, "sample_size": 8, "tier_score": 0.5 + (0.1 if i % 2 else -0.1),
            "signal": 0.2 if i % 2 else -0.2, "confidence": 1.0,
            "avg_engagement_rate": 0.01, "avg_views_per_day": 100,
            "source_job_ids": ["j"] * 8, "synthetic_count": 0,
        }
    deltas = eng.compute_bounded_deltas(parent, signals, bounds)
    assert 0 < len(deltas) <= cfg.strategy_max_params_per_update


# 9. Outlier damping -----------------------------------------------------------------

def test_outlier_damping():
    normal = [1000, 1100, 1200, 1300, 1400]
    with_outlier = FeedbackAnalyzer.compute_view_thresholds(list(normal) + [999999.0])
    without = FeedbackAnalyzer.compute_view_thresholds(normal)
    classified_with = [FeedbackAnalyzer.classify_tier(v, with_outlier).value for v in normal]
    classified_without = [FeedbackAnalyzer.classify_tier(v, without).value for v in normal]
    # A single viral value must not shift the taxonomy for the others.
    assert classified_with == classified_without
    assert FeedbackAnalyzer.classify_tier(999999.0, with_outlier).value == "top"


def test_degenerate_distribution_is_neutral():
    thresholds = FeedbackAnalyzer.compute_view_thresholds([100, 100, 100, 100, 1000000])
    for v in (100, 1000000):
        assert FeedbackAnalyzer.classify_tier(v, thresholds).value == "average"


# 10. Recency / window / min-age -----------------------------------------------------

def test_recency_window_and_min_age(cfg, db):
    seed_published(db, "job-fresh", "Quantum Technology", 5000, age_days=5)
    seed_published(db, "job-too-recent", "AI Advances", 4200, age_days=1)
    seed_published(db, "job-stale", "Old Finance Data", 600, age_days=60)
    eng = make_engine(cfg, db)

    summary = eng.update_strategy(channel_id="default", min_samples=1)
    # Stale content is dropped before any eligibility accounting; too-recent is
    # enumerated and explicitly excluded.
    assert summary.observation_ids == ["job-fresh"]
    assert summary.excluded_reasons.get("too_recent") == 1
    assert summary.observations_considered == 2  # 1 used + 1 excluded (stale never counts)


# 11. Versioning & parent chain -------------------------------------------------------

def test_versioning_and_parent_chain(cfg, db):
    seed_standard(db)
    eng = make_engine(cfg, db)
    summary = eng.update_strategy(channel_id="default")

    assert summary.resulting_strategy_version == "strat-v2"
    active = eng.strategy_manager.get_active_strategy()
    assert active.version_id == "strat-v2"
    assert active.parent_version_id == "strat-v1"
    chain = db.get_strategy_ancestor_chain("strat-v2")
    assert chain == ["strat-v2", "strat-v1"]


# 12. Idempotency: identical input cannot mint new versions ---------------------------

def test_idempotency_same_input(cfg, db):
    seed_standard(db)
    eng = make_engine(cfg, db)

    run1 = eng.update_strategy(channel_id="default")
    assert run1.status == LearningRunStatus.APPLIED.value
    assert run1.input_fingerprint

    run2 = eng.update_strategy(channel_id="default")
    assert run2.status == LearningRunStatus.NO_CHANGE.value
    assert run2.input_fingerprint == run1.input_fingerprint
    assert eng.strategy_manager.get_active_strategy().version_id == "strat-v2"
    # Only 2 strategy versions should ever exist (strat-v1 + strat-v2).
    versions = {s.version_id for s in db.list_strategy_versions(limit=50)}
    assert versions == {"strat-v1", "strat-v2"}


# 13. New data -> new (next) version ---------------------------------------------------

def test_new_data_new_version(cfg, db):
    seed_standard(db)
    eng = make_engine(cfg, db)
    r1 = eng.update_strategy(channel_id="default")
    assert r1.resulting_strategy_version == "strat-v2"

    seed_published(db, "job-t5", "AI Chipset Engineering Breakthrough", 6000, 260, 15, 8)
    r2 = eng.update_strategy(channel_id="default")
    assert r2.status == LearningRunStatus.APPLIED.value
    assert r2.resulting_strategy_version == "strat-v3"
    assert eng.strategy_manager.get_active_strategy().parent_version_id == "strat-v2"


# 14. Dry-run proposes but never applies ------------------------------------------------

def test_dry_run_proposes_without_applying(cfg, db):
    seed_standard(db)
    eng = make_engine(cfg, db)

    before = {s.version_id for s in db.list_strategy_versions(limit=50)}
    assert before == set()  # fresh DB: no baseline recorded yet
    summary = eng.update_strategy(channel_id="default", dry_run=True)
    assert summary.status == LearningRunStatus.DRY_RUN.value
    assert summary.resulting_strategy_version is None
    after = {s.version_id for s in db.list_strategy_versions(limit=50)}
    # Only a lazy baseline registration (strat-v1) may appear — never strat-v2.
    assert "strat-v2" not in after
    assert len(after - before) <= 1
    assert eng.strategy_manager.get_active_strategy().version_id == "strat-v1"

    # A dry run must never block a later real application of the same data.
    real = eng.update_strategy(channel_id="default")
    assert real.status == LearningRunStatus.APPLIED.value
    assert real.resulting_strategy_version == "strat-v2"


# 15. Policy / non-tunable fields are immutable --------------------------------------------

def test_policy_and_config_immutability(cfg, db):
    # Pre-seed a baseline with non-trivial safety configuration.
    db.record_strategy_version(
        StrategyVersion(
            version_id="strat-v1",
            hook_patterns=["provocative_question", "stat_hook"],
            topic_rules={"blocked_topics": ["crypto_hype", "politics"], "min_evidence": 2},
            niche_weights={"technology": 1.0, "science": 1.0, "history": 0.8, "finance": 0.8, "general": 0.5},
        )
    )
    seed_standard(db)
    eng = make_engine(cfg, db)
    summary = eng.update_strategy(channel_id="default")
    active = eng.strategy_manager.get_active_strategy()

    assert summary.status == LearningRunStatus.APPLIED.value
    assert active.parent_version_id == "strat-v1"
    parent = db.get_strategy_version("strat-v1")
    assert parent is not None
    assert active.hook_patterns == parent.hook_patterns == ["provocative_question", "stat_hook"]
    assert active.topic_rules == parent.topic_rules == {
        "blocked_topics": ["crypto_hype", "politics"],
        "min_evidence": 2,
    }
    # Only niche weights (and lifecycle fields) may differ from the baseline.
    assert active.niche_weights["technology"] != parent.niche_weights["technology"]
    for key, parent_weight in parent.niche_weights.items():
        if key not in ("technology", "finance"):
            assert active.niche_weights[key] == pytest.approx(parent_weight)


def _count_publish_records(db):
    with db._connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM publish_records").fetchone()[0]


def _count_queue_items(db):
    with db._connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM queue_items").fetchone()[0]


# 16. Publishing boundary: learning never publishes -----------------------------------------

def test_zero_publish_calls(cfg, db):
    seed_standard(db)
    eng = make_engine(cfg, db)
    pubs_before = _count_publish_records(db)
    items_before = _count_queue_items(db)

    summary = eng.update_strategy(channel_id="default")
    assert summary.status == LearningRunStatus.APPLIED.value
    assert _count_publish_records(db) == pubs_before
    assert _count_queue_items(db) == items_before


# 17. Production boundary: learning never produces -------------------------------------------

def test_zero_production_calls(cfg, db):
    seed_standard(db)
    eng = make_engine(cfg, db)
    jobs_before = len(db.list_queue_items(limit=500))
    items_before = _count_queue_items(db)

    eng.update_strategy(channel_id="default")
    assert len(db.list_queue_items(limit=500)) == jobs_before
    assert _count_queue_items(db) == items_before


# 18. Ideation integration: explicit strategy_bonus ---------------------------------------------

def test_ideation_strategy_bonus_integration(cfg, db):
    seed_standard(db)
    eng = make_engine(cfg, db)
    r = eng.update_strategy(channel_id="default")
    assert r.resulting_strategy_version == "strat-v2"

    learned = eng.strategy_manager.get_active_strategy()
    assert abs(learned.niche_weights["technology"] - 1.075) < 1e-3

    cand = TopicCandidate(
        candidate_id="cand-1", run_id="run-1", proposed_topic="AI Systems Deep Dive",
        category="technology", commercial_relevance=0.6,
    )
    scorer = TopicScorer(strategy_influence_scale=cfg.strategy_influence_scale)
    baseline = StrategyVersion(version_id="strat-v1")
    s_base = scorer.score_candidate(cand, strategy=baseline)
    s_learned = scorer.score_candidate(cand, strategy=learned)

    assert s_base.strategy_bonus == 0.0
    assert s_learned.strategy_bonus == pytest.approx((1.075 - 1.0) * 0.25, abs=1e-3)
    assert s_learned.strategy_version == "strat-v2"
    assert s_learned.breakdown["strategy_version"] == "strat-v2"
    assert s_learned.total_score > s_base.total_score


# 19. Invalid data -> no strategy change ---------------------------------------------------------

def test_invalid_data_no_strategy_change(cfg, db):
    # Published but zero impressions => intentionally limited, never learned.
    for i in range(1, 7):
        seed_published(db, f"job-zero-{i}", f"Tech Topic {i}", 0)
    eng = make_engine(cfg, db)
    summary = eng.update_strategy(channel_id="default")

    assert summary.status == LearningRunStatus.INSUFFICIENT.value
    assert summary.excluded_reasons.get("zero_views") == 6
    assert eng.strategy_manager.get_active_strategy().version_id == "strat-v1"


# 20. Failed / non-published content is excluded ---------------------------------------------------

def test_failed_or_unpublished_excluded(cfg, db):
    seed_standard(db)
    seed_published(db, "job-failed", "Tech Failure Topic", 9000, status="FAILED")
    seed_published(db, "job-dryrun", "Finance Dry Topic", 100, status="DRY_RUN")
    eng = make_engine(cfg, db)

    summary = eng.update_strategy(channel_id="default")
    assert summary.status == LearningRunStatus.APPLIED.value
    assert "job-failed" not in summary.observation_ids
    assert "job-dryrun" not in summary.observation_ids


# 21. Only genuinely published states are consumed ---------------------------------------------------

def test_only_published_states_consumed(cfg, db):
    assert PUBLISHED_STATUSES == ("SUCCESS", "PUBLISHED")
    seed_published(db, "job-succ", "Technology Success Topic High", 6000, status="SUCCESS")
    seed_published(db, "job-pub", "Technology Published Topic High", 6500, status="PUBLISHED")
    seed_published(db, "job-dry", "Technology Dry Run Topic", 8000, status="DRY_RUN")
    seed_published(db, "job-fail", "Technology Failed Topic", 7000, status="FAILED")
    eng = make_engine(cfg, db)

    obs, excluded = eng.collect_observations(channel_id="default", min_age_days=0)
    ids = {o["job_id"] for o in obs}
    assert {"job-succ", "job-pub"} <= ids
    assert "job-dry" not in ids and "job-fail" not in ids


# 22. Explainability -----------------------------------------------------------------------------------

def test_strategy_explainability(cfg, db):
    seed_standard(db)
    eng = make_engine(cfg, db)
    summary = eng.update_strategy(channel_id="default")

    assert "strat-v1" in summary.reason and "strat-v2" in summary.reason
    assert all(d.reason and d.sample_size >= 2 for d in summary.deltas)
    assert "Applied bounded strategy update" in summary.reason
    assert summary.input_fingerprint

    row = db.find_applied_learning_run("default", summary.input_fingerprint)
    assert row is not None
    assert row["status"] == "applied"
    assert json.loads(row["category_signals_json"])
    assert json.loads(row["deltas_json"])
    assert set(json.loads(row["observation_ids_json"])) == {o["job_id"] for o in eng.collect_observations(channel_id="default")[0]}

    active = eng.strategy_manager.get_active_strategy()
    assert "Learning run" in active.rationale


# 5b/23. Cross-channel strategy isolation ---------------------------------------------------------------

def test_cross_channel_strategy_isolation(cfg, db):
    create_channel(db, "tech_shorts", niche="technology")
    for i in range(6):
        seed_published(db, f"job-a-{i}", f"AI Technology Innovation {i}", 9000 + i * 10, channel="tech_shorts")
    eng = make_engine(cfg, db)

    # Default channel untouched by tech_shorts learning.
    before_default = eng.strategy_manager.get_active_strategy(channel_id="default")
    assert before_default.version_id == "strat-v1"

    summary = eng.update_strategy(channel_id="tech_shorts")
    assert summary.status == LearningRunStatus.APPLIED.value
    assert summary.resulting_strategy_version == "strat-tech_shorts-v2"

    bound = eng.strategy_manager.get_active_strategy(channel_id="tech_shorts")
    assert bound.version_id == "strat-tech_shorts-v2"
    # Default is unchanged; the learned boost stays channel-bound.
    after_default = eng.strategy_manager.get_active_strategy(channel_id="default")
    assert after_default.version_id == "strat-v1"

    # Per-channel idempotency also holds.
    run2 = eng.update_strategy(channel_id="tech_shorts")
    assert run2.status == LearningRunStatus.NO_CHANGE.value


# 24. CLI ------------------------------------------------------------------------------------------------

def test_cli_learn_and_summary(cfg, db, tmp_path, monkeypatch, capsys):
    seed_standard(db)
    monkeypatch.setattr(CONFIG, "db_path", str(tmp_path / "test_analytics_learning.db"))
    monkeypatch.setattr(CONFIG, "artifacts_dir", str(tmp_path / "artifacts"))

    from autopilot.cli import main as cli

    rc = cli.run_analytics_learn(channel_id="default", dry_run=True)
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "dry_run" in out.lower()

    rc = cli.run_analytics_learn(channel_id="default")
    assert rc == 0
    out = capsys.readouterr().out
    assert "applied" in out

    rc = cli.run_analytics_summary(channel_id="default")
    assert rc == 0
    out = capsys.readouterr().out
    assert "strat-v2" in out


# 25. Scheduler opt-in learning stage ---------------------------------------------------------------------

def _mock_scheduler_engine():
    engine = MagicMock()
    engine.run_cycle.return_value = AutonomyCycleSummary(
        run_id="run-l3", channel_id="c1", autonomy_level=3, status="completed", jobs_queued=1
    )
    engine.run_auto_produce_cycle.return_value = AutoProduceSummary(
        run_id="run-l4", channel_id="c1", status="completed", jobs_ready_to_publish=1,
        decision_reasons=[{"queue_id": "q1", "job_id": "job-fresh-1", "status": "succeeded"}],
    )
    engine.run_learning_cycle.return_value = LearningRunSummary(
        run_id="learn-1", channel_id="c1", status=LearningRunStatus.APPLIED.value,
        observations_used=5, resulting_strategy_version="strat-c1-v2",
    )
    return engine


def test_scheduler_include_learning_opt_in(tmp_path, db):
    engine = _mock_scheduler_engine()
    sched = make_scheduler(tmp_path, db, engine)

    sch = sched.create_schedule(
        channel_id="c1", autonomy_level=4, operation_mode="level3_then_level4",
        cadence="daily", include_learning=True, next_run_at=NOW.isoformat(), now=NOW,
    )
    assert sch.include_learning is True

    summary = sched.run_now(sch.schedule_id, now=NOW)
    assert summary.status == "completed"
    assert summary.learning_status == LearningRunStatus.APPLIED.value
    assert summary.learning_run_id == "learn-1"
    # The freshly produced job from THIS run is excluded from learning.
    kwargs = engine.run_learning_cycle.call_args.kwargs
    assert "job-fresh-1" in kwargs.get("exclude_job_ids", [])

    # Without the opt-in flag learning must never run.
    engine.run_learning_cycle.reset_mock()
    sch2 = sched.create_schedule(
        schedule_id="sch-nolearn", channel_id="c2", autonomy_level=3, cadence="daily",
        next_run_at=NOW.isoformat(), now=NOW,
    )
    assert sch2.include_learning is False
    s2 = sched.run_now("sch-nolearn", now=NOW)
    assert s2.learning_status is None
    engine.run_learning_cycle.assert_not_called()


def test_scheduler_learning_failure_isolated(tmp_path, db):
    engine = _mock_scheduler_engine()
    engine.run_learning_cycle.side_effect = RuntimeError("boom")
    sched = make_scheduler(tmp_path, db, engine)
    sch = sched.create_schedule(
        schedule_id="sch-lrn-fail", channel_id="c3", autonomy_level=4,
        operation_mode="level3_then_level4", cadence="daily",
        include_learning=True, next_run_at=NOW.isoformat(), now=NOW,
    )
    summary = sched.run_now(sch.schedule_id, now=NOW)
    # A failing learning stage must NOT flip the Level 3/4 terminal status.
    assert summary.learning_status == "failed"
    assert summary.status == "completed"


# 26. Operation loop never triggers learning itself -----------------------------------------------

def test_operation_loop_no_recursive_learning(tmp_path, db):
    cfg = Config(artifacts_dir=str(tmp_path / "artifacts"), db_path=str(db.db_path))
    eng = AutonomyEngine(config=cfg, db=db)

    summary = eng.run_cycle(autonomy_level=3, dry_run=True)
    assert summary.status not in ("failed", "blocked")

    # A plain ideation cycle must leave the strategy and learning tables untouched.
    assert db.list_learning_runs(channel_id="default") == []
    assert eng.strategy_manager.get_active_strategy().version_id == "strat-v1"

    # Learning only happens through the explicit, idempotent entry point.
    seed_standard(db)
    lr = eng.run_learning_cycle(dry_run=True)
    assert lr.status == LearningRunStatus.DRY_RUN.value
    runs = db.list_learning_runs(channel_id="default")
    assert runs and runs[0]["dry_run"] == 1


# 27. Restart / repeat safety --------------------------------------------------------------------------

def test_restart_repeat_safety(cfg, db):
    seed_standard(db)
    eng = make_engine(cfg, db)
    r1 = eng.update_strategy(channel_id="default")
    assert r1.status == LearningRunStatus.APPLIED.value

    # Simulate a process restart: brand-new engine & strategy managers, same DB.
    fresh_eng = LearningEngine(config=cfg, db=db)
    fresh_mgr = StrategyManager(db)
    fresh_eng.strategy_manager = fresh_mgr

    r2 = fresh_eng.update_strategy(channel_id="default")
    assert r2.status == LearningRunStatus.NO_CHANGE.value
    assert fresh_mgr.get_active_strategy().version_id == "strat-v2"


# 28. Provider isolation (import graph) ----------------------------------------------------------------

def test_provider_isolation_import_graph():
    probe = (
        "import sys;"
        "import autopilot.core.learning as L;"
        "bad=[m for m in L.LEARNING_FORBIDDEN_MODULES if m in sys.modules];"
        "print('BAD=' + '|'.join(bad))"
    )
    repo_root = Path(__file__).resolve().parents[1]
    out = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True, text=True, cwd=str(repo_root), timeout=120,
    )
    assert out.returncode == 0, out.stderr
    line = out.stdout.strip().splitlines()[-1]
    assert line == "BAD=", f"forbidden modules imported: {line}"


def test_learning_allowlist_static():
    """Source-level guardrail: learning.py never imports any forbidden module."""
    src = Path(__file__).resolve().parents[1] / "autopilot" / "core" / "learning.py"
    text = src.read_text(encoding="utf-8")
    forbid = LEARNING_FORBIDDEN_MODULES
    # Strip trailing provider/'' segment to match prefix-bound module names.
    for mod in forbid:
        assert f"import {mod}" not in text
        assert f"from {mod}" not in text


# 29. Category resolution: lineage ------------------------------------------------------------------------

def test_category_lineage_resolution(cfg, db):
    sig = TrendSignal(signal_id="sig-sci-1", topic="Space Discovery", source="test",
                      category="science", confidence=0.9)
    db.record_trend_signal(sig)
    db.enqueue_item(
        queue_id="q-lineage", job_id="job-lineage", content_id="job-lineage",
        payload={"topic": "Space Exploration Findings", "parent_signal_ids": ["sig-sci-1"], "channel_id": "default"},
    )
    db.record_publish_record(job_id="job-lineage", remote_video_id="vid-lineage", status="SUCCESS")
    age_published(db, "job-lineage", 5)
    db.record_analytics_snapshot(
        snapshot_id="snap-lineage", job_id="job-lineage", platform="youtube", provider="mock",
        remote_id="vid-lineage", window="lifetime", metrics={"views": 5000.0, "likes": 100.0},
    )

    assert db.resolve_job_category("job-lineage") == ("science", "lineage")
    eng = make_engine(cfg, db)
    obs, _ = eng.collect_observations(channel_id="default", min_age_days=0)
    entry = next(o for o in obs if o["job_id"] == "job-lineage")
    assert entry["category"] == "science"
    assert entry["category_source"] == "lineage"


def test_category_keyword_fallback(cfg, db):
    db.create_job(job_id="job-kw", channel_id="default", topic="Deep Neural Network Tech")
    db.record_publish_record(job_id="job-kw", remote_video_id="vid-kw", status="SUCCESS")
    age_published(db, "job-kw", 5)
    db.record_analytics_snapshot(
        snapshot_id="snap-kw", job_id="job-kw", platform="youtube", provider="mock",
        remote_id="vid-kw", window="lifetime", metrics={"views": 4000.0, "likes": 80.0},
    )

    assert db.resolve_job_category("job-kw") == ("technology", "keyword")
    eng = make_engine(cfg, db)
    obs, _ = eng.collect_observations(channel_id="default", min_age_days=0)
    entry = next(o for o in obs if o["job_id"] == "job-kw")
    assert entry["category"] == "technology"
    assert entry["category_source"] == "keyword"


def test_unresolved_buckets_general(cfg, db):
    db.create_job(job_id="job-unk", channel_id="default", topic="Mysterious Unknown Niche Spectrum")
    db.record_publish_record(job_id="job-unk", remote_video_id="vid-unk", status="SUCCESS")
    age_published(db, "job-unk", 5)
    db.record_analytics_snapshot(
        snapshot_id="snap-unk", job_id="job-unk", platform="youtube", provider="mock",
        remote_id="vid-unk", window="lifetime", metrics={"views": 3000.0, "likes": 60.0},
    )

    assert db.resolve_job_category("job-unk") == (None, "unresolved")
    eng = make_engine(cfg, db)
    obs, excluded = eng.collect_observations(channel_id="default", min_age_days=0)
    entry = next(o for o in obs if o["job_id"] == "job-unk")
    assert entry["category"] == "general"
    assert entry["category_source"] == "unresolved"
    assert excluded.get("unresolved_category") == 1


# 30. Synthetic analytics is visible in learning runs ---------------------------------------------------

def test_synthetic_input_flagged(cfg, db):
    seed_standard(db, synthetic=True)
    eng = make_engine(cfg, db)
    summary = eng.update_strategy(channel_id="default")

    assert summary.is_synthetic_input is True
    assert summary.status == LearningRunStatus.APPLIED.value
    tech_sig = summary.category_signals["technology"]
    assert tech_sig["synthetic_count"] == tech_sig["sample_size"]
    assert db.find_applied_learning_run("default", summary.input_fingerprint)["is_synthetic_input"] == 1


def test_mixed_synthetic_flag(cfg, db):
    seed_standard(db)  # measured by default
    seed_published(db, "job-mixed", "Quantum AI Advancements", 6500, is_synthetic=True)
    eng = make_engine(cfg, db)
    summary = eng.update_strategy(channel_id="default")
    assert summary.is_synthetic_input is True


# 31. Recency weighting property ---------------------------------------------------------------------------

def test_recency_weight_monotonic(cfg, db):
    eng = make_engine(cfg, db)
    w_fresh = eng._recency_weight(0.0, window_days=30, recency_weight=0.6)
    w_old = eng._recency_weight(30.0, window_days=30, recency_weight=0.6)
    w_mid = eng._recency_weight(15.0, window_days=30, recency_weight=0.6)
    assert w_fresh == pytest.approx(1.0)
    assert w_old == pytest.approx(0.4)
    assert w_fresh >= w_mid >= w_old


# 32. DB-level learning-run persistence ---------------------------------------------------------------------

def test_learning_run_persistence_fields(cfg, db):
    seed_standard(db)
    eng = make_engine(cfg, db)
    summary = eng.update_strategy(channel_id="default")

    row = db.get_learning_run(summary.run_id)
    assert row is not None
    assert row["parent_strategy_version"] == "strat-v1"
    assert row["resulting_strategy_version"] == "strat-v2"
    assert row["observations_used"] == 6
    assert row["dry_run"] == 0
    assert json.loads(row["category_signals_json"])["technology"]
    linked = db.list_learning_runs_for_strategy("strat-v2")
    assert linked and linked[0]["run_id"] == summary.run_id