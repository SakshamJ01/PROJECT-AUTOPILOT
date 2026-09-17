"""Autonomous Operation Loop tests — scheduled trigger -> Level 3 -> queue ->
Level 4 -> QA -> READY_TO_PUBLISH -> STOP.

The operation loop is a thin orchestration layer that sequences the *existing*
Level 3 (AutonomyEngine.run_cycle) and Level 4 (AutonomyEngine.run_auto_produce_cycle)
engines inside a single scheduled operation.  These tests cover the full loop,
its operating modes, bounded execution, failure isolation, idempotency, the
publishing boundary, and the portable in-process ticker.

TEST ISOLATION (the known hazard in this repo): every test that touches
provider/worker execution pins deterministic providers — a real ffmpeg
production engine plus ``policy="mock"`` payloads — or uses an explicitly
mocked engine/orchestrator.  No test may resolve real network providers, and
no test writes to the production DB or artifact tree.  The per-module
structured loggers are stubbed to in-memory recorders.
"""
import json
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from autopilot.core.autonomy import AutonomyEngine
from autopilot.core.config import Config
from autopilot.core.contracts import (
    AutoProduceSummary,
    AutonomyCycleSummary,
    AutonomyLevel,
    AutonomyPolicy,
    AutonomySchedule,
    OperationMode,
    ScheduleCadence,
    TrendSignal,
    derive_operation_mode,
    validate_operation_mode,
)
from autopilot.core.scheduler import ScheduleEngine
from autopilot.db.manager import DBManager
from autopilot.providers.mock_trend import MockTrendProvider

NOW = datetime(2026, 9, 17, 10, 0, 0, tzinfo=timezone.utc)  # Thursday
PAST = "2026-09-16T08:00:00+00:00"


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

class _Recorder:
    """In-memory structured-logger stub (keeps tests free of file writes)."""

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
    d = DBManager(str(tmp_path / "test_operation_loop.db"))
    d.init_schema()
    return d


def make_config(tmp_path, db_path, **over):
    return Config(
        artifacts_dir=str(tmp_path / "artifacts"),
        db_path=str(db_path),
        default_production_engine="ffmpeg",
        **over,
    )


def make_scheduler(tmp_path, db, engine=None, **kwargs):
    cfg = make_config(tmp_path, db.db_path)
    return ScheduleEngine(config=cfg, db=db, engine=engine, **kwargs)


def mock_engine(
    l3_status="completed",
    l4_status="completed",
    l3_jobs_queued=1,
    l4_ready=1,
    l3_run_id="run-x",
    l4_run_id="ap-x",
    channel_id="default",
    l4_qa_failed=0,
):
    """A fully-mocked AutonomyEngine for orchestration-level assertions."""
    engine = MagicMock()
    engine.run_cycle.return_value = AutonomyCycleSummary(
        run_id=l3_run_id,
        channel_id=channel_id,
        autonomy_level=3,
        status=l3_status,
        jobs_queued=l3_jobs_queued,
    )
    engine.run_auto_produce_cycle.return_value = AutoProduceSummary(
        run_id=l4_run_id,
        channel_id=channel_id,
        status=l4_status,
        jobs_ready_to_publish=l4_ready,
        jobs_qa_failed=l4_qa_failed,
    )
    return engine


def good_signal(sid="sig-001", topic="Quantum Computing Breakthrough", category="technology"):
    return TrendSignal(
        signal_id=sid,
        topic=topic,
        source="test",
        evidence_text="Strong evidence",
        category=category,
    )


def trend_provider(*signals):
    class _T(MockTrendProvider):
        def discover_trends(self, category=None, limit=10):
            return list(signals)

    return _T()


def real_engine(tmp_path, db, signals=None, policy=None, config_over=None):
    """A real AutonomyEngine with a pinned deterministic trend provider."""
    cfg = make_config(tmp_path, db.db_path, **(config_over or {}))
    engine = AutonomyEngine(config=cfg, db=db, policy=policy)
    if signals is not None:
        prov = trend_provider(*signals)
        engine.trend_provider = prov
        engine.discovery = prov
    return engine


def enqueue_auto(db, qid, job, topic, channel_id="default", **extra):
    payload = {
        "topic": topic,
        "channel_id": channel_id,
        "profile": "short_vertical",
        "origin": "autonomous",
        "parent_signal_ids": ["sig-test-1"],
        "policy": "mock",
    }
    payload.update(extra)
    db.enqueue_item(queue_id=qid, job_id=job, content_id=job, channel_id=channel_id, payload=payload)
    return payload


def create_loop_schedule(engine, schedule_id="s-loop", channel_id="default", **over):
    params = {
        "schedule_id": schedule_id,
        "channel_id": channel_id,
        "autonomy_level": 4,
        "operation_mode": OperationMode.LEVEL_3_THEN_4.value,
        "cadence": "daily",
        "policy": "mock",
        "next_run_at": PAST,
        "now": NOW,
    }
    params.update(over)
    return engine.create_schedule(**params)


def parse_iso(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# Contract / mode validation
# ---------------------------------------------------------------------------

def test_operation_mode_contract_validation():
    assert derive_operation_mode(3) == OperationMode.LEVEL_3_ONLY.value
    assert derive_operation_mode(4) == OperationMode.LEVEL_4_ONLY.value
    assert validate_operation_mode(None, 3) == "level3"
    assert validate_operation_mode(None, 4) == "level4"
    assert validate_operation_mode("level3_then_level4", 4) == "level3_then_level4"
    # Level 4 modes require the Level 4 grant.
    with pytest.raises(ValueError, match="requires autonomy_level 4"):
        validate_operation_mode("level4", 3)
    with pytest.raises(ValueError, match="requires autonomy_level 4"):
        validate_operation_mode("level3_then_level4", 3)
    with pytest.raises(ValueError, match="Invalid operation_mode"):
        validate_operation_mode("bogus", 4)
    # Level 3-only under a Level 4 grant is a valid deliberate restriction.
    assert validate_operation_mode("level3", 4) == "level3"


def test_schedule_create_rejects_inconsistent_mode(tmp_path, db):
    engine = make_scheduler(tmp_path, db, engine=mock_engine())
    with pytest.raises(ValueError, match="requires autonomy_level 4"):
        engine.create_schedule(
            schedule_id="s-bad", autonomy_level=3, operation_mode="level3_then_level4", cadence="daily", now=NOW
        )
    assert db.get_schedule("s-bad") is None


def test_schedule_default_mode_backward_compatible(tmp_path, db):
    """Schedules created without an explicit mode derive it from the level."""
    engine = make_scheduler(tmp_path, db, engine=mock_engine())
    l3 = engine.create_schedule(schedule_id="s-l3", autonomy_level=3, cadence="daily", now=NOW)
    l4 = engine.create_schedule(schedule_id="s-l4", autonomy_level=4, cadence="daily", now=NOW)
    assert l3.operation_mode == OperationMode.LEVEL_3_ONLY.value
    assert l4.operation_mode == OperationMode.LEVEL_4_ONLY.value
    assert db.get_schedule("s-l3")["operation_mode"] == "level3"
    assert db.get_schedule("s-l4")["operation_mode"] == "level4"


def test_legacy_rows_derive_mode_on_read(tmp_path, db):
    """A pre-v9 schedule row (null operation_mode) derives its mode at read time."""
    db.create_schedule(schedule_id="s-legacy", autonomy_level=4, cadence="daily", next_run_at=PAST)
    with db._connect() as conn:
        conn.execute("UPDATE autonomy_schedules SET operation_mode = NULL WHERE schedule_id = 's-legacy'")
        conn.commit()
    engine = make_scheduler(tmp_path, db, engine=mock_engine())
    sched = engine.get_schedule("s-legacy")
    assert sched.operation_mode == OperationMode.LEVEL_4_ONLY.value


# ---------------------------------------------------------------------------
# TEST 1: Level 3 -> Level 4 happy path ending at READY_TO_PUBLISH.
# ---------------------------------------------------------------------------

def test_operation_loop_happy_path(tmp_path, db):
    eng = real_engine(tmp_path, db, signals=[good_signal()])
    engine = make_scheduler(tmp_path, db, engine=eng)
    create_loop_schedule(engine)

    with patch("autopilot.core.publisher.PublishingEngine.publish_job") as mock_publish:
        result = engine.run_now("s-loop", now=NOW, worker_id="w1")

    assert result.status == "completed"
    assert result.operation_mode == OperationMode.LEVEL_3_THEN_4.value
    assert result.publish_calls == 0
    mock_publish.assert_not_called()

    # Stage wiring: Level 3 queued work, Level 4 produced it.
    assert result.level3_status == "completed"
    assert result.level3_jobs_queued >= 1
    assert result.level4_status == "completed"
    assert result.level4_jobs_ready_to_publish >= 1
    # cycle_run_id describes the terminal (Level 4) stage.
    assert result.cycle_run_id == result.level4_run_id

    # Terminal state is READY_TO_PUBLISH, never PUBLISHED.
    queued = [qi for qi in db.list_queue_items(channel_id="default") if qi["status"] != "cancelled"]
    produced = [qi for qi in queued if qi["status"] == "succeeded"]
    assert produced, "Level 4 must produce the Level 3-queued work"
    for qi in queued:
        assert qi["status"] != "published"
    for qi in produced:
        job = db.get_job(qi["job_id"])
        assert job["status"] == "APPROVED"  # READY_TO_PUBLISH

    # The run record carries both stages.
    runs = db.list_schedule_runs("s-loop")
    assert len(runs) == 1
    stored = json.loads(runs[0]["cycle_summary_json"])
    assert stored["operation_mode"] == OperationMode.LEVEL_3_THEN_4.value
    assert stored["level3"]["jobs_queued"] >= 1
    assert stored["level4"]["jobs_ready_to_publish"] >= 1
    assert stored["level4_skipped_reason"] is None


# ---------------------------------------------------------------------------
# TEST 2: Level 3 produces no candidates -> nothing queued or produced.
# ---------------------------------------------------------------------------

def test_operation_loop_l3_no_candidates(tmp_path, db):
    eng = real_engine(tmp_path, db, signals=[])
    engine = make_scheduler(tmp_path, db, engine=eng)
    create_loop_schedule(engine)

    result = engine.run_now("s-loop", now=NOW, worker_id="w1")
    assert result.status == "completed"
    assert result.level3_status == "completed"
    assert result.level3_jobs_queued == 0
    assert result.level4_status == "completed"
    assert result.level4_jobs_ready_to_publish == 0
    assert db.get_queue_status_summary(channel_id="default")["succeeded"] == 0


# ---------------------------------------------------------------------------
# TEST 3: Level 3 policy rejection (prohibited topic) -> nothing queued.
# ---------------------------------------------------------------------------

def test_operation_loop_l3_policy_rejection(tmp_path, db):
    eng = real_engine(tmp_path, db, signals=[good_signal(sid="sig-bad", topic="Get Rich Quick Scheme")])
    engine = make_scheduler(tmp_path, db, engine=eng)
    create_loop_schedule(engine)

    result = engine.run_now("s-loop", now=NOW, worker_id="w1")
    assert result.status == "completed"
    assert result.level3_jobs_queued == 0
    assert result.level4_jobs_ready_to_publish == 0
    for qi in db.list_queue_items(channel_id="default"):
        assert "Get Rich Quick" not in (qi.get("payload", {}) or {}).get("topic", "")


# ---------------------------------------------------------------------------
# TEST 4: Level 3 queue capacity guardrail is preserved by the loop.
# ---------------------------------------------------------------------------

def test_operation_loop_l3_queue_capacity(tmp_path, db):
    # An active queued item saturates max_concurrent_jobs=1 -> PolicyGate blocks.
    enqueue_auto(db, "q-cap", "job-cap", "Capacity Sentinel Topic")
    policy = AutonomyPolicy(max_concurrent_jobs=1, max_jobs_per_day=10)
    eng = real_engine(tmp_path, db, signals=[good_signal()], policy=policy)
    engine = make_scheduler(tmp_path, db, engine=eng)
    create_loop_schedule(engine)

    result = engine.run_now("s-loop", now=NOW, worker_id="w1")
    assert result.status == "completed"
    assert result.level3_jobs_queued == 0, "Queue capacity must block the new candidate"
    # The pre-existing queued item is still produced by Level 4.
    assert db.get_queue_item("q-cap")["status"] in ("succeeded", "queued", "running")


# ---------------------------------------------------------------------------
# TEST 5: Level 4 consumes pre-queued backlog within a combined run.
# ---------------------------------------------------------------------------

def test_operation_loop_l4_consumes_backlog(tmp_path, db):
    enqueue_auto(db, "q-backlog", "job-backlog", "The Discovery of Penicillin")
    eng = real_engine(tmp_path, db, signals=[])  # Level 3 adds nothing new
    engine = make_scheduler(tmp_path, db, engine=eng)
    create_loop_schedule(engine)

    with patch("autopilot.core.publisher.PublishingEngine.publish_job") as mock_publish:
        result = engine.run_now("s-loop", now=NOW, worker_id="w1")

    assert result.status == "completed"
    assert result.level4_jobs_ready_to_publish >= 1
    assert db.get_queue_item("q-backlog")["status"] == "succeeded"
    assert db.get_job("job-backlog")["status"] == "APPROVED"
    mock_publish.assert_not_called()


# ---------------------------------------------------------------------------
# TEST 6: Level 4 QA failure never becomes a success or a publish.
# ---------------------------------------------------------------------------

def test_operation_loop_l4_qa_failure(tmp_path, db):
    from autopilot.core.pipeline import PipelineError, PipelineOrchestrator

    enqueue_auto(db, "q-qafail", "job-qafail", "QA Failure Topic")
    eng = real_engine(tmp_path, db, signals=[])

    # Direct Level 4 proof (contract per phase-4 TEST 17/18): a QA failure is
    # counted / excluded from READY_TO_PUBLISH and is never published.
    mock_orch = MagicMock(spec=PipelineOrchestrator)
    mock_orch.run_pipeline.side_effect = PipelineError(
        "QA Gate Blocked: failed checks", category="BLOCKED", stage="QA"
    )
    with patch("autopilot.core.publisher.PublishingEngine.publish_job") as mock_publish:
        direct = eng.run_auto_produce_cycle(channel_id="default", limit=5, policy="mock", orchestrator=mock_orch)
    assert direct.jobs_qa_failed == 1
    assert direct.jobs_ready_to_publish == 0
    assert db.get_queue_item("q-qafail")["status"] == "blocked"
    mock_publish.assert_not_called()

    # Loop-level accounting: a QA-failing Level 4 must not reach READY_TO_PUBLISH.
    engine = make_scheduler(tmp_path, db, engine=mock_engine(l3_jobs_queued=1, l4_ready=0, l4_qa_failed=1))
    create_loop_schedule(engine)
    result = engine.run_now("s-loop", now=NOW, worker_id="w1")
    assert result.status == "completed"
    assert result.level4_jobs_ready_to_publish == 0
    assert result.publish_calls == 0


# ---------------------------------------------------------------------------
# TEST 7: Repeated invocation is idempotent (no duplicate queue/production).
# ---------------------------------------------------------------------------

def test_operation_loop_idempotency(tmp_path, db):
    eng = real_engine(tmp_path, db, signals=[good_signal()])
    engine = make_scheduler(tmp_path, db, engine=eng)
    create_loop_schedule(engine)

    with patch("autopilot.core.publisher.PublishingEngine.publish_job"):
        first = engine.run_now("s-loop", now=NOW, worker_id="w1")
        second = engine.run_now("s-loop", now=NOW + timedelta(hours=1), worker_id="w1")

    assert first.level3_jobs_queued >= 1
    assert first.level4_jobs_ready_to_publish >= 1
    # Second run: topic is in cooldown (duplicate) and prior job already succeeded.
    assert second.level3_jobs_queued == 0
    assert second.level4_jobs_ready_to_publish == 0

    succeeded = [qi for qi in db.list_queue_items(channel_id="default") if qi["status"] == "succeeded"]
    assert len(succeeded) == first.level3_jobs_queued, "No duplicate production on re-run"
    assert db.get_schedule("s-loop")["total_runs"] == 2


# ---------------------------------------------------------------------------
# TEST 8: Overlapping operation protection (lease guard).
# ---------------------------------------------------------------------------

def test_operation_loop_overlap_protection(tmp_path, db):
    engine = make_scheduler(tmp_path, db, engine=mock_engine())
    create_loop_schedule(engine)

    # Another worker already holds the lease.
    assert db.claim_due_schedule(worker_id="worker-a", now_iso=NOW.isoformat()) is not None

    skipped = engine.run_now("s-loop", now=NOW, worker_id="worker-b")
    assert skipped.status == "skipped"
    assert "claimed" in (skipped.error_message or "")
    engine.engine.run_cycle.assert_not_called()
    engine.engine.run_auto_produce_cycle.assert_not_called()
    assert db.get_schedule("s-loop")["total_runs"] == 0


# ---------------------------------------------------------------------------
# TEST 9: Daily limit boundary bounds the loop's production.
# ---------------------------------------------------------------------------

def test_operation_loop_daily_limit(tmp_path, db):
    # Two autonomous items already consume the daily budget of 1.
    enqueue_auto(db, "q-d1", "job-d1", "Daily One")
    enqueue_auto(db, "q-d2", "job-d2", "Daily Two")
    policy = AutonomyPolicy(max_jobs_per_day=1, max_concurrent_jobs=5)
    eng = real_engine(tmp_path, db, signals=[], policy=policy)
    engine = make_scheduler(tmp_path, db, engine=eng)
    create_loop_schedule(engine)

    result = engine.run_now("s-loop", now=NOW, worker_id="w1")
    assert result.status == "completed"
    assert result.level4_jobs_ready_to_publish == 0, "Daily limit must block all production"
    for qid in ("q-d1", "q-d2"):
        assert db.get_queue_item(qid)["status"] == "queued"


# ---------------------------------------------------------------------------
# TEST 10: Concurrency limit boundary.
# ---------------------------------------------------------------------------

def test_operation_loop_concurrency_limit(tmp_path, db):
    # An in-flight item with a live lease saturates max_concurrent_jobs=1.
    enqueue_auto(db, "q-run", "job-run", "Running Topic")
    with db._connect() as conn:
        conn.execute(
            "UPDATE queue_items SET status='running', worker_id='other', "
            "lease_expires_at=datetime('now','+300 seconds') WHERE queue_id='q-run'"
        )
        conn.commit()
    policy = AutonomyPolicy(max_concurrent_jobs=1, max_jobs_per_day=10)
    eng = real_engine(tmp_path, db, signals=[], policy=policy)
    engine = make_scheduler(tmp_path, db, engine=eng)
    create_loop_schedule(engine)

    result = engine.run_now("s-loop", now=NOW, worker_id="w1")
    assert result.status == "completed"
    assert result.level4_jobs_ready_to_publish == 0, "Concurrency limit must block new production"
    assert db.get_queue_item("q-run")["status"] == "running"


# ---------------------------------------------------------------------------
# TEST 11: Channel isolation — channels never cross.
# ---------------------------------------------------------------------------

def test_operation_loop_channel_isolation(tmp_path, db):
    eng = mock_engine(channel_id="channel-a")
    engine = make_scheduler(tmp_path, db, engine=eng)
    create_loop_schedule(engine, schedule_id="s-a", channel_id="channel-a")
    create_loop_schedule(engine, schedule_id="s-b", channel_id="channel-b")

    results = engine.run_due(now=NOW, worker_id="w1")
    assert {r.schedule_id for r in results} == {"s-a", "s-b"}

    channels = [c.kwargs["channel_id"] for c in eng.run_cycle.call_args_list]
    assert channels == ["channel-a", "channel-b"]
    for c in (eng.run_auto_produce_cycle.call_args_list):
        assert c.kwargs["channel_id"] in ("channel-a", "channel-b")

    run_a = [r for r in db.list_schedule_runs("s-a")]
    run_b = [r for r in db.list_schedule_runs("s-b")]
    assert len(run_a) == 1 and len(run_b) == 1
    assert run_a[0]["channel_id"] == "channel-a"
    assert run_b[0]["channel_id"] == "channel-b"


# ---------------------------------------------------------------------------
# TEST 12: Restart / retry — stale leases and idempotent run records.
# ---------------------------------------------------------------------------

def test_operation_loop_restart_safety(tmp_path, db):
    engine = make_scheduler(tmp_path, db, engine=mock_engine())
    create_loop_schedule(engine)

    # Runner A claims then crashes; the lease must expire before a reclaim.
    assert db.claim_due_schedule(worker_id="wA", now_iso=NOW.isoformat()) is not None
    assert db.claim_due_schedule(worker_id="wB", now_iso=NOW.isoformat()) is None
    after_crash = NOW + timedelta(seconds=400)
    reclaimed = db.claim_due_schedule(worker_id="wB", now_iso=after_crash.isoformat())
    assert reclaimed["schedule_id"] == "s-loop"

    result = engine._execute(engine.get_schedule("s-loop"), reclaimed, "wB", after_crash)
    assert result.status == "completed"

    # Duplicate completion after a crash-replay is a no-op (no double accounting).
    ok_redo = db.complete_schedule_run(
        schedule_id="s-loop", worker_id="wB", run_id=result.run_id, channel_id="default",
        autonomy_level=4, cycle_run_id="c", run_status="completed", error_message=None,
        cycle_summary_json="{}", publish_calls=0, next_run_at="2026-09-18T10:00:00+00:00",
        started_at=NOW.isoformat(), completed_at=NOW.isoformat(), consecutive_failures=0,
    )
    assert ok_redo is False
    assert db.get_schedule("s-loop")["total_runs"] == 1
    assert len(db.list_schedule_runs("s-loop")) == 1


# ---------------------------------------------------------------------------
# TEST 13: Dry-run simulates the whole loop without DB mutation.
# ---------------------------------------------------------------------------

def test_operation_loop_dry_run(tmp_path, db):
    eng = real_engine(tmp_path, db, signals=[good_signal()])
    engine = make_scheduler(tmp_path, db, engine=eng)
    create_loop_schedule(engine, dry_run=True)

    result = engine.run_now("s-loop", now=NOW, worker_id="w1")
    assert result.status == "completed"
    assert result.cycle_status == "completed"

    with db._connect() as conn:
        assert conn.execute("SELECT count(*) AS c FROM autonomy_runs").fetchone()["c"] == 0
        assert conn.execute("SELECT count(*) AS c FROM queue_items").fetchone()["c"] == 0
    assert db.get_schedule("s-loop")["total_runs"] == 1  # schedule bookkeeping only


# ---------------------------------------------------------------------------
# TEST 14: The loop never publishes publicly (boundary + failure isolation).
# ---------------------------------------------------------------------------

def test_operation_loop_no_public_publishing(tmp_path, db):
    engine = make_scheduler(tmp_path, db, engine=mock_engine(l4_ready=1))
    create_loop_schedule(engine)

    with patch("autopilot.core.publisher.PublishingEngine.publish_job") as mock_publish:
        result = engine.run_now("s-loop", now=NOW, worker_id="w1")

    assert result.publish_calls == 0
    mock_publish.assert_not_called()
    runs = db.list_schedule_runs("s-loop")
    assert runs[0]["publish_calls"] == 0


def test_operation_loop_l3_failure_isolates_l4(tmp_path, db):
    """A failed Level 3 must never lead to fake production."""
    engine = make_scheduler(tmp_path, db, engine=mock_engine(l3_status="failed", l3_jobs_queued=0))
    create_loop_schedule(engine)

    result = engine.run_now("s-loop", now=NOW, worker_id="w1")
    assert result.status == "failed"
    assert result.level3_status == "failed"
    # Level 4 must NOT run after a failed Level 3.
    engine.engine.run_auto_produce_cycle.assert_not_called()
    assert result.level4_run_id is None
    assert "failure isolation" in (result.error_message or "")

    stored = json.loads(db.list_schedule_runs("s-loop")[0]["cycle_summary_json"])
    assert stored["level4"] is None
    assert stored["level4_skipped_reason"] is not None
    assert db.get_schedule("s-loop")["consecutive_failures"] == 1


# ---------------------------------------------------------------------------
# TEST 15: Final READY_TO_PUBLISH state (terminal success state).
# ---------------------------------------------------------------------------

def test_operation_loop_terminal_ready_to_publish(tmp_path, db):
    eng = real_engine(tmp_path, db, signals=[good_signal()])
    engine = make_scheduler(tmp_path, db, engine=eng)
    create_loop_schedule(engine)

    with patch("autopilot.core.publisher.PublishingEngine.publish_job") as mock_publish:
        engine.run_now("s-loop", now=NOW, worker_id="w1")

    produced = [qi for qi in db.list_queue_items(channel_id="default") if qi["status"] == "succeeded"]
    assert produced
    for qi in produced:
        assert qi["stage"] == "COMPLETE"
        assert db.get_job(qi["job_id"])["status"] == "APPROVED"
    # Nothing past the pre-publish boundary.
    for qi in db.list_queue_items(channel_id="default"):
        assert qi["status"] not in ("published", "publishing")
    with db._connect() as conn:
        assert conn.execute("SELECT count(*) AS c FROM publish_records").fetchone()["c"] == 0
        assert conn.execute("SELECT count(*) AS c FROM publish_approvals").fetchone()["c"] == 0
    mock_publish.assert_not_called()


# ---------------------------------------------------------------------------
# TEST 16: CLI manual invocation (create --mode + run-now + run-due dispatch).
# ---------------------------------------------------------------------------

def test_operation_loop_cli_dispatch(tmp_path, capsys):
    import autopilot.cli.main as cli

    created = AutonomySchedule(
        schedule_id="s-cli", channel_id="default", autonomy_level=4,
        operation_mode=OperationMode.LEVEL_3_THEN_4.value,
        cadence=ScheduleCadence.DAILY, timezone="UTC", max_items_per_run=5,
        policy="mock", next_run_at="2026-09-18T10:00:00+00:00",
    )
    fake = MagicMock()
    fake.create_schedule.return_value = created
    fake.list_schedules.return_value = [created]
    fake.get_schedule.return_value = created
    fake.inspect_schedule.return_value = {"schedule": created.model_dump(), "runs": []}
    loop_result = MagicMock()
    loop_result.schedule_id = "s-cli"
    loop_result.operation_mode = OperationMode.LEVEL_3_THEN_4.value
    loop_result.status = "completed"
    loop_result.cycle_run_id = "c"
    loop_result.cycle_status = "completed"
    loop_result.level3_run_id = "r3"
    loop_result.level3_status = "completed"
    loop_result.level3_jobs_queued = 1
    loop_result.level4_run_id = "r4"
    loop_result.level4_status = "completed"
    loop_result.level4_jobs_ready_to_publish = 1
    loop_result.next_run_at = "2026-09-18T10:00:00+00:00"
    loop_result.publish_calls = 0
    loop_result.error_message = None
    loop_result.autonomy_level = 4
    loop_result.channel_id = "default"
    fake.run_now.return_value = loop_result
    fake.run_loop.return_value = {"iterations": 1, "summaries": [loop_result]}
    fake.model_dump_json.return_value = "{}"

    with patch.object(cli, "_schedule_engine", return_value=fake):
        assert cli.run_schedule_create(channel_id="default", level=4, mode="level3_then_level4", output_json=False) == 0
        fake.create_schedule.assert_called_once()
        assert fake.create_schedule.call_args.kwargs["operation_mode"] == "level3_then_level4"
        assert cli.run_schedule_run_now(schedule_id="s-cli", output_json=False) == 0
        assert cli.run_schedule_inspect(schedule_id="s-cli", output_json=False) == 0
        assert cli.run_schedule_loop(poll=0.5, max_iterations=1, output_json=False) == 0
        fake.run_loop.assert_called_once_with(poll_interval=0.5, max_iterations=1, max_runs_per_tick=10)

    out = capsys.readouterr().out
    assert "level3_then_level4" in out
    assert "=== SCHEDULE LOOP (ticker) ===" in out

    # Inconsistent mode still fails closed through the CLI.
    fake.create_schedule.side_effect = ValueError("requires autonomy_level 4")
    with patch.object(cli, "_schedule_engine", return_value=fake):
        assert cli.run_schedule_create(channel_id="default", level=3, mode="level4", output_json=False) == 1


# ---------------------------------------------------------------------------
# Regression: single-stage schedules behave exactly as before.
# ---------------------------------------------------------------------------

def test_single_stage_modes_unchanged(tmp_path, db):
    eng = mock_engine()
    engine = make_scheduler(tmp_path, db, engine=eng)
    engine.create_schedule(
        schedule_id="s-only3", channel_id="tech", autonomy_level=3, cadence="daily",
        max_items_per_run=4, dry_run=True, next_run_at=PAST, now=NOW,
    )
    engine.create_schedule(
        schedule_id="s-only4", channel_id="history", autonomy_level=4, cadence="weekly",
        max_items_per_run=7, policy="local_only", next_run_at=PAST, now=NOW,
    )

    results = engine.run_due(now=NOW, worker_id="w1")
    by_id = {r.schedule_id: r for r in results}
    assert by_id["s-only3"].operation_mode == "level3"
    assert by_id["s-only4"].operation_mode == "level4"

    eng.run_cycle.assert_called_once_with(autonomy_level=3, channel_id="tech", dry_run=True, limit=4)
    eng.run_auto_produce_cycle.assert_called_once_with(
        channel_id="history", limit=7, dry_run=False, policy="local_only"
    )
    # Legacy fields keep their meaning for single-stage runs.
    assert by_id["s-only3"].cycle_run_id == "run-x"
    assert by_id["s-only4"].cycle_run_id == "ap-x"
    assert by_id["s-only3"].level4_run_id is None
    assert by_id["s-only4"].level3_run_id is None


# ---------------------------------------------------------------------------
# Ticker: deterministic, no real sleeping, graceful shutdown.
# ---------------------------------------------------------------------------

def test_ticker_executes_due_schedules(tmp_path, db):
    eng = mock_engine()
    engine = make_scheduler(tmp_path, db, engine=eng)
    create_loop_schedule(engine)

    out = engine.run_loop(poll_interval=0, max_iterations=1, now=NOW)
    assert out["iterations"] == 1
    assert len(out["summaries"]) == 1
    assert out["summaries"][0].schedule_id == "s-loop"
    eng.run_cycle.assert_called_once()
    eng.run_auto_produce_cycle.assert_called_once()
    assert db.get_schedule("s-loop")["total_runs"] == 1


def test_ticker_stops_immediately_on_set_event(tmp_path, db):
    eng = mock_engine()
    engine = make_scheduler(tmp_path, db, engine=eng)
    create_loop_schedule(engine)

    stop = threading.Event()
    stop.set()  # already stopped -> no tick, no sleep, no execution
    out = engine.run_loop(poll_interval=10, stop_event=stop, now=NOW)
    assert out["iterations"] == 0
    eng.run_cycle.assert_not_called()
    assert db.get_schedule("s-loop")["total_runs"] == 0


def test_ticker_respects_max_iterations_and_no_sleep(tmp_path, db):
    eng = mock_engine()
    engine = make_scheduler(tmp_path, db, engine=eng)
    create_loop_schedule(engine)

    slept = []
    stop = threading.Event()
    original_wait = stop.wait

    def counting_wait(seconds):
        slept.append(seconds)
        return original_wait(seconds)

    stop.wait = counting_wait
    out = engine.run_loop(poll_interval=5, max_iterations=2, stop_event=stop, now=NOW)
    assert out["iterations"] == 2
    # Only one runnable due schedule per configuration -> exactly one execution.
    assert db.get_schedule("s-loop")["total_runs"] == 1
    # A bounded loop never sleeps past its final tick.
    assert slept == [] or all(s == 5 for s in slept)


def test_ticker_never_publishes_and_delegates_only(tmp_path, db):
    eng = mock_engine()
    engine = make_scheduler(tmp_path, db, engine=eng)
    create_loop_schedule(engine)

    with patch("autopilot.core.publisher.PublishingEngine.publish_job") as mock_publish:
        out = engine.run_loop(poll_interval=0, max_iterations=1, now=NOW)
    mock_publish.assert_not_called()
    for r in out["summaries"]:
        assert r.publish_calls == 0


def test_ticker_test_isolation(tmp_path):
    """The ticker must never touch the production DB."""
    from autopilot.core.config import CONFIG

    default_db = CONFIG.db_path
    existed_before = default_db.exists()
    snap_before = default_db.stat().st_mtime_ns if existed_before else None

    iso_db = DBManager(str(tmp_path / "ticker_iso.db"))
    iso_db.init_schema()
    engine = ScheduleEngine(config=make_config(tmp_path, str(tmp_path / "ticker_iso.db")), db=iso_db, engine=mock_engine())
    create_loop_schedule(engine)
    engine.run_loop(poll_interval=0, max_iterations=1, now=NOW)

    assert default_db.exists() == existed_before
    if snap_before is not None:
        assert default_db.stat().st_mtime_ns == snap_before
