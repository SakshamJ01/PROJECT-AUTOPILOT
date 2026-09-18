"""Phase 3 — Scheduling Tests.

Covers recurring, per-channel schedule orchestration on top of the existing
Level 3 (run_cycle) and Level 4 (run_auto_produce_cycle) engines: create /
validation / list, due detection, disabled handling, level dispatch, atomic
overlap protection, cross-channel isolation, manual run-now, dry-run, restart
safety, bounded missed-run catch-up, failure tracking, next-run calculation,
timezones, cadences, enable/disable/delete, publish boundary, existing
guardrail preservation, run-record idempotency, CLI dispatch, and test
isolation.

Isolation rule: every deterministic run pins mock providers (policy "mock"
payloads + ffmpeg production engine) and uses only temporary DBs/artifact dirs.
The per-module structured loggers are stubbed to in-memory recorders so no
production log files are written during this suite.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autopilot.core.autonomy import AutonomyEngine
from autopilot.core.channel import ChannelManager
from autopilot.core.config import Config, CONFIG
from autopilot.core.contracts import (
    AutonomyCycleSummary,
    AutonomyLevel,
    AutonomyPolicy,
    AutonomySchedule,
    AutoProduceSummary,
    ScheduleCadence,
)
from autopilot.core.scheduler import (
    ScheduleEngine,
    next_run_after,
    validate_days_of_week,
    validate_timezone,
)
from autopilot.db.manager import DBManager

NOW = datetime(2026, 9, 16, 10, 0, 0, tzinfo=timezone.utc)  # Wednesday


class _FrozenDatetime(datetime):
    """Fix `datetime.now` so enable/disable re-advance logic is deterministic.

    ScheduleEngine.enable_schedule re-advances a backdated next_run_at against
    ``datetime.now()``; the tests pin a fixed NOW, so the wall clock must be
    frozen for the duration of the operation or the suite is time-dependent.
    """

    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _Recorder:
    """In-memory structured-logger stub used to keep tests free of file writes."""

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

    monkeypatch.setattr(scheduler_mod, "StructuredLogger", _Recorder)
    monkeypatch.setattr(autonomy_mod, "StructuredLogger", _Recorder)


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test_scheduler_phase3.db"
    d = DBManager(str(db_path))
    d.init_schema()
    return d


def make_config(tmp_path, db_path):
    return Config(artifacts_dir=str(tmp_path / "artifacts"), db_path=str(db_path))


def make_scheduler(tmp_path, db, engine=None, **kwargs):
    cfg = make_config(tmp_path, db.db_path)
    return ScheduleEngine(config=cfg, db=db, engine=engine, **kwargs)


def success_cycle(run_id="run-x", channel_id="default", level=3, status="completed"):
    return AutonomyCycleSummary(run_id=run_id, channel_id=channel_id, autonomy_level=level, status=status)


def mock_engine(**overrides):
    engine = MagicMock()
    engine.run_cycle.return_value = success_cycle(**{**{"run_id": "run-x", "channel_id": "default", "level": 3}, **overrides})
    engine.run_auto_produce_cycle.return_value = AutoProduceSummary(
        run_id="ap-run-x", channel_id=overrides.get("channel_id", "default"), status="completed"
    )
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


def parse_iso(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# TEST 1: Create a schedule with valid defaults and persistence.
# ---------------------------------------------------------------------------
def test_schedule_create(tmp_path, db):
    engine = make_scheduler(tmp_path, db, engine=mock_engine())
    schedule = engine.create_schedule(
        channel_id="default", autonomy_level=3, cadence="daily", now=NOW, schedule_id="s-1"
    )
    assert schedule.schedule_id == "s-1"
    assert schedule.channel_id == "default"
    assert schedule.autonomy_level == 3
    assert schedule.enabled is True
    assert schedule.cadence == ScheduleCadence.DAILY
    assert schedule.dry_run is False
    assert schedule.total_runs == 0
    assert schedule.consecutive_failures == 0

    row = db.get_schedule("s-1")
    assert row is not None
    assert row["channel_id"] == "default"
    assert row["cadence"] == "daily"
    assert row["timezone"] == "UTC"

    due = parse_iso(row["next_run_at"])
    assert due == datetime(2026, 9, 17, 10, 0, 0, tzinfo=timezone.utc)  # daily -> +1 day

    events = [e["event"] for e in engine.logger.events]
    assert "schedule_created" in events
    created = next(e for e in engine.logger.events if e["event"] == "schedule_created")
    assert created["details"]["schedule_id"] == "s-1"
    assert created["details"]["autonomy_level"] == 3


# ---------------------------------------------------------------------------
# TEST 2: Fail-closed validation rejects invalid schedules.
# ---------------------------------------------------------------------------
def test_schedule_create_invalid(tmp_path, db):
    engine = make_scheduler(tmp_path, db, engine=mock_engine())

    with pytest.raises(ValueError, match="channel_id"):
        engine.create_schedule(channel_id="", cadence="daily")
    with pytest.raises(ValueError, match="autonomy_level"):
        engine.create_schedule(channel_id="default", autonomy_level=2, cadence="daily")
    with pytest.raises(ValueError, match="autonomy_level"):
        engine.create_schedule(channel_id="default", autonomy_level=5, cadence="daily")
    with pytest.raises(ValueError, match="cadence"):
        engine.create_schedule(channel_id="default", cadence="fortnightly")
    with pytest.raises(ValueError, match="timezone"):
        engine.create_schedule(channel_id="default", cadence="daily", timezone="Mars/Olympus")
    with pytest.raises(ValueError, match="timezone"):
        engine.create_schedule(channel_id="default", cadence="daily", timezone=123)
    with pytest.raises(ValueError, match="max_items_per_run"):
        engine.create_schedule(channel_id="default", cadence="daily", max_items_per_run=0)
    with pytest.raises(ValueError, match="weekday"):
        engine.create_schedule(channel_id="default", cadence="weekly", days_of_week=["onday"])
    assert db.list_schedules() == []


# ---------------------------------------------------------------------------
# TEST 3: List schedules with channel / enabled filters.
# ---------------------------------------------------------------------------
def test_schedule_list(tmp_path, db):
    engine = make_scheduler(tmp_path, db, engine=mock_engine())
    engine.create_schedule(channel_id="channel-a", autonomy_level=3, cadence="daily", now=NOW, schedule_id="s-a")
    engine.create_schedule(channel_id="channel-b", autonomy_level=4, cadence="weekly", now=NOW, schedule_id="s-b")

    all_sched = engine.list_schedules()
    assert {s.schedule_id for s in all_sched} == {"s-a", "s-b"}

    only_a = engine.list_schedules(channel_id="channel-a")
    assert [s.schedule_id for s in only_a] == ["s-a"]

    engine.disable_schedule("s-b")
    enabled = engine.list_schedules(enabled=True)
    disabled = engine.list_schedules(enabled=False)
    assert [s.schedule_id for s in enabled] == ["s-a"]
    assert [s.schedule_id for s in disabled] == ["s-b"]


# ---------------------------------------------------------------------------
# TEST 4: Due detection claims and executes exactly the due schedule.
# ---------------------------------------------------------------------------
def test_schedule_due_detection(tmp_path, db):
    eng = mock_engine()
    engine = make_scheduler(tmp_path, db, engine=eng)
    engine.create_schedule(schedule_id="s-due", cadence="daily", next_run_at="2026-09-16T08:00:00+00:00", now=NOW)

    assert db.count_due_schedules(NOW.isoformat()) == 1

    results = engine.run_due(now=NOW, worker_id="w1")
    assert len(results) == 1
    assert results[0].status == "completed"
    assert results[0].cycle_run_id == "run-x"
    eng.run_cycle.assert_called_once()
    assert db.count_due_schedules(NOW.isoformat()) == 0
    assert db.get_schedule("s-due")["total_runs"] == 1


# ---------------------------------------------------------------------------
# TEST 5: Non-due schedules are not executed.
# ---------------------------------------------------------------------------
def test_schedule_not_due(tmp_path, db):
    eng = mock_engine()
    engine = make_scheduler(tmp_path, db, engine=eng)
    engine.create_schedule(schedule_id="s-future", cadence="daily", next_run_at="2026-09-17T10:00:00+00:00", now=NOW)

    assert db.count_due_schedules(NOW.isoformat()) == 0
    results = engine.run_due(now=NOW, worker_id="w1")
    assert results == []
    eng.run_cycle.assert_not_called()
    assert db.get_schedule("s-future")["total_runs"] == 0


# ---------------------------------------------------------------------------
# TEST 6: Disabled schedules never execute (run-due skips, run-now blocked).
# ---------------------------------------------------------------------------
def test_schedule_disabled(tmp_path, db):
    eng = mock_engine()
    engine = make_scheduler(tmp_path, db, engine=eng)
    engine.create_schedule(schedule_id="s-off", cadence="daily", next_run_at="2026-09-16T08:00:00+00:00", now=NOW)
    engine.disable_schedule("s-off")

    assert db.count_due_schedules(NOW.isoformat()) == 0
    assert engine.run_due(now=NOW, worker_id="w1") == []

    blocked = engine.run_now("s-off", now=NOW, worker_id="w1")
    assert blocked.status == "blocked"
    assert "disabled" in (blocked.error_message or "")
    eng.run_cycle.assert_not_called()
    eng.run_auto_produce_cycle.assert_not_called()


# ---------------------------------------------------------------------------
# TEST 7: Level 3 schedules dispatch to run_cycle (never run_auto_produce_cycle).
# ---------------------------------------------------------------------------
def test_schedule_level3_dispatch(tmp_path, db):
    eng = mock_engine()
    engine = make_scheduler(tmp_path, db, engine=eng)
    engine.create_schedule(
        schedule_id="s-l3", channel_id="tech", autonomy_level=3, cadence="daily",
        max_items_per_run=4, dry_run=True, next_run_at="2026-09-16T08:00:00+00:00", now=NOW,
    )
    results = engine.run_due(now=NOW, worker_id="w1")
    assert len(results) == 1
    assert results[0].autonomy_level == 3
    eng.run_cycle.assert_called_once_with(
        autonomy_level=3, channel_id="tech", dry_run=True, limit=4
    )
    eng.run_auto_produce_cycle.assert_not_called()

    events = [e["event"] for e in engine.logger.events]
    for expected in ("schedule_claimed", "schedule_run_started", "schedule_run_completed", "schedule_released"):
        assert expected in events


# ---------------------------------------------------------------------------
# TEST 8: Level 4 schedules dispatch to run_auto_produce_cycle (never run_cycle).
# ---------------------------------------------------------------------------
def test_schedule_level4_dispatch(tmp_path, db):
    eng = mock_engine()
    engine = make_scheduler(tmp_path, db, engine=eng)
    engine.create_schedule(
        schedule_id="s-l4", channel_id="history", autonomy_level=4, cadence="weekly",
        max_items_per_run=7, policy="local_only", next_run_at="2026-09-16T08:00:00+00:00", now=NOW,
    )
    results = engine.run_due(now=NOW, worker_id="w1")
    assert len(results) == 1
    assert results[0].autonomy_level == 4
    eng.run_auto_produce_cycle.assert_called_once_with(
        channel_id="history", limit=7, dry_run=False, policy="local_only"
    )
    eng.run_cycle.assert_not_called()


# ---------------------------------------------------------------------------
# TEST 9: Overlap protection — a claimed schedule cannot be claimed twice.
# ---------------------------------------------------------------------------
def test_schedule_overlap_race(tmp_path, db):
    engine = make_scheduler(tmp_path, db, engine=mock_engine())
    engine.create_schedule(schedule_id="s-race", cadence="daily", next_run_at="2026-09-16T08:00:00+00:00", now=NOW)

    first = db.claim_due_schedule(worker_id="worker-a", now_iso=NOW.isoformat())
    assert first is not None
    assert first["schedule_id"] == "s-race"

    second = db.claim_due_schedule(worker_id="worker-b", now_iso=NOW.isoformat())
    assert second is None

    claimed = db.claim_schedule_by_id(schedule_id="s-race", worker_id="worker-b", now_iso=NOW.isoformat())
    assert claimed is None

    eng2 = mock_engine()
    engine2 = make_scheduler(tmp_path, db, engine=eng2)
    skipped = engine2.run_now("s-race", now=NOW, worker_id="worker-b")
    assert skipped.status == "skipped"
    eng2.run_cycle.assert_not_called()
    eng2.run_auto_produce_cycle.assert_not_called()


# ---------------------------------------------------------------------------
# TEST 10: Cross-channel isolation — each channel only triggers its own cycles.
# ---------------------------------------------------------------------------
def test_schedule_cross_channel_isolation(tmp_path, db):
    eng = mock_engine()
    engine = make_scheduler(tmp_path, db, engine=eng)
    engine.create_schedule(schedule_id="s-a", channel_id="channel-a", cadence="daily",
                           next_run_at="2026-09-16T08:00:00+00:00", now=NOW)
    engine.create_schedule(schedule_id="s-b", channel_id="channel-b", cadence="daily",
                           next_run_at="2026-09-16T09:00:00+00:00", now=NOW)

    results = engine.run_due(now=NOW, worker_id="w1", max_runs=10)
    assert [r.schedule_id for r in results] == ["s-a", "s-b"]

    call_args = [call.kwargs["channel_id"] for call in eng.run_cycle.call_args_list]
    assert call_args == ["channel-a", "channel-b"]

    assert db.get_schedule("s-a")["total_runs"] == 1
    assert db.get_schedule("s-b")["total_runs"] == 1


# ---------------------------------------------------------------------------
# TEST 11: Manual run-now executes regardless of due-ness.
# ---------------------------------------------------------------------------
def test_schedule_run_now_manual(tmp_path, db):
    eng = mock_engine(run_id="run-manual")
    engine = make_scheduler(tmp_path, db, engine=eng)
    engine.create_schedule(schedule_id="s-manual", cadence="daily", next_run_at="2026-09-17T10:00:00+00:00", now=NOW)

    result = engine.run_now("s-manual", now=NOW, worker_id="w1")
    assert result.status == "completed"
    assert result.cycle_run_id == "run-manual"
    eng.run_cycle.assert_called_once()

    row = db.get_schedule("s-manual")
    assert row["total_runs"] == 1
    assert parse_iso(row["next_run_at"]) == datetime(2026, 9, 17, 10, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# TEST 12: Dry-run schedules simulate cycles without any DB mutation.
# ---------------------------------------------------------------------------
def test_schedule_dry_run(tmp_path, db):
    engine = make_scheduler(tmp_path, db, engine=None)
    engine.create_schedule(
        schedule_id="s-dry", autonomy_level=3, cadence="daily", dry_run=True,
        next_run_at="2026-09-16T08:00:00+00:00", now=NOW,
    )

    result = engine.run_now("s-dry", now=NOW, worker_id="w1")
    assert result.status == "completed"
    assert result.cycle_status == "completed"

    with db._connect() as conn:
        run_rows = conn.execute("SELECT count(*) AS cnt FROM autonomy_runs").fetchone()["cnt"]
        queue_rows = conn.execute("SELECT count(*) AS cnt FROM queue_items").fetchone()["cnt"]
    assert run_rows == 0
    assert queue_rows == 0
    assert db.get_schedule("s-dry")["total_runs"] == 1


# ---------------------------------------------------------------------------
# TEST 13: Restart safety — stale leases expire, completed runs never double.
# ---------------------------------------------------------------------------
def test_schedule_restart_safety(tmp_path, db):
    engine = make_scheduler(tmp_path, db, engine=mock_engine())
    engine.create_schedule(schedule_id="s-rs", cadence="daily", next_run_at="2026-09-16T08:00:00+00:00", now=NOW)

    # Runner A claims (lease until NOW+300s).
    assert db.claim_due_schedule(worker_id="wA", now_iso=NOW.isoformat()) is not None
    # Runner B immediately: lease still held -> no claim.
    assert db.claim_due_schedule(worker_id="wB", now_iso=NOW.isoformat()) is None

    # Crash: A never completes. After lease expiry a new runner can reclaim.
    after_crash = NOW + timedelta(seconds=400)
    reclaimed = db.claim_due_schedule(worker_id="wB", now_iso=after_crash.isoformat())
    assert reclaimed is not None
    assert reclaimed["schedule_id"] == "s-rs"
    assert db.get_schedule("s-rs")["leased_by"] == "wB"


# ---------------------------------------------------------------------------
# TEST 14: Missed-run bounded catch-up — one run, then advance from now.
# ---------------------------------------------------------------------------
def test_schedule_missed_run_bounded_catchup(tmp_path, db):
    eng = mock_engine()
    engine = make_scheduler(tmp_path, db, engine=eng)
    # Due 5 days ago with a daily cadence.
    engine.create_schedule(schedule_id="s-catchup", cadence="daily", next_run_at="2026-09-11T10:00:00+00:00", now=NOW)

    results = engine.run_due(now=NOW, worker_id="w1", max_runs=10)
    assert len(results) == 1
    assert results[0].status == "completed"
    eng.run_cycle.assert_called_once()

    row = db.get_schedule("s-catchup")
    assert row["total_runs"] == 1  # exactly one, not five
    assert parse_iso(row["next_run_at"]) == datetime(2026, 9, 17, 10, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# TEST 15: Cycle failures are recorded, counted, and the schedule keeps going.
# ---------------------------------------------------------------------------
def test_schedule_failure_recorded(tmp_path, db):
    eng = mock_engine()
    eng.run_cycle.return_value = success_cycle(run_id="run-fail", status="failed")
    engine = make_scheduler(tmp_path, db, engine=eng)
    engine.create_schedule(schedule_id="s-fail", cadence="daily", next_run_at="2026-09-16T08:00:00+00:00", now=NOW)

    result = engine.run_now("s-fail", now=NOW, worker_id="w1")
    assert result.status == "failed"
    assert result.cycle_status == "failed"
    assert result.error_message is not None

    row = db.get_schedule("s-fail")
    assert row["total_runs"] == 1
    assert row["consecutive_failures"] == 1
    assert row["last_run_status"] == "failed"
    assert row["enabled"] == 1  # not permanently disabled
    assert parse_iso(row["next_run_at"]) == datetime(2026, 9, 17, 10, 0, 0, tzinfo=timezone.utc)

    runs = db.list_schedule_runs("s-fail")
    assert runs[0]["status"] == "failed"
    assert "run-fail" in (runs[0]["cycle_run_id"] or "")


# ---------------------------------------------------------------------------
# TEST 16: Next-run calculation for every cadence at reference times.
# ---------------------------------------------------------------------------
def test_schedule_next_run_calculation():
    hourly = AutonomySchedule(schedule_id="h", cadence=ScheduleCadence.HOURLY)
    assert next_run_after(hourly, NOW) == datetime(2026, 9, 16, 11, 0, 0, tzinfo=timezone.utc)

    daily = AutonomySchedule(schedule_id="d", cadence=ScheduleCadence.DAILY)
    assert next_run_after(daily, NOW) == datetime(2026, 9, 17, 10, 0, 0, tzinfo=timezone.utc)

    weekdays = AutonomySchedule(schedule_id="w", cadence=ScheduleCadence.WEEKDAYS)
    assert next_run_after(weekdays, NOW) == datetime(2026, 9, 17, 10, 0, 0, tzinfo=timezone.utc)  # Wed -> Thu

    weekend = datetime(2026, 9, 19, 10, 0, 0, tzinfo=timezone.utc)  # Saturday
    assert next_run_after(weekdays, weekend) == datetime(2026, 9, 21, 10, 0, 0, tzinfo=timezone.utc)  # -> Monday

    weekly_plain = AutonomySchedule(schedule_id="w7", cadence=ScheduleCadence.WEEKLY)
    assert next_run_after(weekly_plain, NOW) == datetime(2026, 9, 23, 10, 0, 0, tzinfo=timezone.utc)  # Wed -> Wed+7

    weekly_days = AutonomySchedule(schedule_id="wd", cadence=ScheduleCadence.WEEKLY, days_of_week=["mon", "wed", "fri"])
    # Wednesday -> next allowed weekday is Friday.
    assert next_run_after(weekly_days, NOW) == datetime(2026, 9, 18, 10, 0, 0, tzinfo=timezone.utc)
    # Monday -> next is Wednesday.
    monday = datetime(2026, 9, 14, 10, 0, 0, tzinfo=timezone.utc)
    assert next_run_after(weekly_days, monday) == datetime(2026, 9, 16, 10, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# TEST 17: IANA timezone handling.
# ---------------------------------------------------------------------------
def test_schedule_timezone(tmp_path, db):
    engine = make_scheduler(tmp_path, db, engine=mock_engine())
    # New York is UTC-4 on 2026-09-16 (EDT). 10:00Z == 06:00 EDT; +1h == 11:00Z.
    ny = engine.create_schedule(
        schedule_id="s-ny", cadence="hourly", timezone="America/New_York", now=NOW,
    )
    assert ny.timezone == "America/New_York"
    assert parse_iso(ny.next_run_at) == datetime(2026, 9, 16, 11, 0, 0, tzinfo=timezone.utc)

    # tok timezone accepted; full name + zoneinfo resolution works.
    la = engine.create_schedule(
        schedule_id="s-la", cadence="daily", timezone="America/Los_Angeles", now=NOW,
    )
    assert la.timezone == "America/Los_Angeles"

    # Invalid/unknown zones are rejected fail-closed.
    with pytest.raises(ValueError, match="timezone"):
        engine.create_schedule(channel_id="default", cadence="daily", timezone="Not/AZone")
    with pytest.raises(ValueError):
        validate_timezone("UTC-5:00")  # offsets are not IANA names
    assert validate_timezone("UTC") is not None


# ---------------------------------------------------------------------------
# TEST 18: Daily cadence advances exactly +1 day after every execution.
# ---------------------------------------------------------------------------
def test_schedule_daily_cadence(tmp_path, db):
    eng = mock_engine()
    engine = make_scheduler(tmp_path, db, engine=eng)
    engine.create_schedule(schedule_id="s-daily", cadence="daily", next_run_at="2026-09-16T08:00:00+00:00", now=NOW)

    first = engine.run_now("s-daily", now=NOW, worker_id="w1")
    assert parse_iso(first.next_run_at) == datetime(2026, 9, 17, 10, 0, 0, tzinfo=timezone.utc)

    later = NOW + timedelta(days=1)
    db.update_schedule("s-daily", next_run_at="2026-09-17T08:00:00+00:00")
    second = engine.run_now("s-daily", now=later, worker_id="w1")
    assert parse_iso(second.next_run_at) == datetime(2026, 9, 18, 10, 0, 0, tzinfo=timezone.utc)

    assert db.get_schedule("s-daily")["total_runs"] == 2


# ---------------------------------------------------------------------------
# TEST 19: Weekly cadence with selected weekdays.
# ---------------------------------------------------------------------------
def test_schedule_weekly_cadence(tmp_path, db):
    engine = make_scheduler(tmp_path, db, engine=mock_engine())
    # Created Wednesday with mon/wed/fri permissions -> first run Friday.
    created = engine.create_schedule(
        schedule_id="s-wk", cadence="weekly", days_of_week=["mon", "wed", "fri"], now=NOW,
    )
    assert parse_iso(created.next_run_at) == datetime(2026, 9, 18, 10, 0, 0, tzinfo=timezone.utc)

    res = engine.run_now("s-wk", now=NOW, worker_id="w1")
    assert res.status == "completed"
    assert parse_iso(res.next_run_at) == datetime(2026, 9, 18, 10, 0, 0, tzinfo=timezone.utc)  # Wed -> Fri
    assert validate_days_of_week(["Monday", "monday", "MON"]) == ["mon", "mon", "mon"]


# ---------------------------------------------------------------------------
# TEST 20: Enable / disable toggles execution.
# ---------------------------------------------------------------------------
def test_schedule_enable_disable(tmp_path, db):
    eng = mock_engine()
    engine = make_scheduler(tmp_path, db, engine=eng)
    engine.create_schedule(schedule_id="s-tog", cadence="daily", next_run_at="2026-09-16T08:00:00+00:00", now=NOW)

    disabled = engine.disable_schedule("s-tog")
    assert disabled.enabled is False
    assert db.get_schedule("s-tog")["enabled"] == 0
    assert engine.run_due(now=NOW, worker_id="w1") == []
    eng.run_cycle.assert_not_called()

    with patch("autopilot.core.scheduler.datetime", _FrozenDatetime):
        enabled = engine.enable_schedule("s-tog")
    assert enabled.enabled is True
    assert db.get_schedule("s-tog")["enabled"] == 1
    assert db.count_due_schedules(NOW.isoformat()) == 0  # backdated next_run_at re-advanced on enable

    results = engine.run_due(now=NOW + timedelta(days=3), worker_id="w1")
    assert len(results) == 1
    assert results[0].status == "completed"


# ---------------------------------------------------------------------------
# TEST 21: Delete removes the schedule and its run history.
# ---------------------------------------------------------------------------
def test_schedule_delete(tmp_path, db):
    eng = mock_engine()
    engine = make_scheduler(tmp_path, db, engine=eng)
    engine.create_schedule(schedule_id="s-del", cadence="daily", next_run_at="2026-09-16T08:00:00+00:00", now=NOW)
    engine.run_now("s-del", now=NOW, worker_id="w1")

    assert len(db.list_schedule_runs("s-del")) == 1
    assert engine.delete_schedule("s-del") is True
    assert db.get_schedule("s-del") is None
    assert db.list_schedule_runs("s-del") == []
    assert engine.list_schedules() == []

    with pytest.raises(ValueError, match="not found"):
        engine.get_schedule("s-del")
    with pytest.raises(ValueError, match="not found"):
        engine.run_now("s-del", now=NOW, worker_id="w1")


# ---------------------------------------------------------------------------
# TEST 22: Publishing boundary — scheduled Level 4 runs never publish.
# ---------------------------------------------------------------------------
def test_schedule_publish_boundary(tmp_path, db):
    enqueue_auto(db, "q-pub", "job-pub", "The Discovery of Penicillin")
    cfg = Config(artifacts_dir=str(tmp_path), db_path=str(db.db_path), default_production_engine="ffmpeg")
    real_engine = AutonomyEngine(config=cfg, db=db)
    engine = make_scheduler(tmp_path, db, engine=real_engine)
    engine.create_schedule(schedule_id="s-pub", autonomy_level=4, cadence="daily", policy="mock",
                           next_run_at="2026-09-16T08:00:00+00:00", now=NOW)

    with patch("autopilot.core.publisher.PublishingEngine.publish_job") as mock_publish_job:
        result = engine.run_now("s-pub", now=NOW, worker_id="w1")

    assert result.status == "completed"
    assert result.cycle_status == "completed"
    assert result.publish_calls == 0
    mock_publish_job.assert_not_called()

    item = db.get_queue_item("q-pub")
    assert item["status"] == "succeeded"
    runs = db.list_schedule_runs("s-pub")
    assert runs[0]["publish_calls"] == 0


# ---------------------------------------------------------------------------
# TEST 23: Existing autonomy guardrails/limits are not bypassed by scheduling.
# ---------------------------------------------------------------------------
def test_schedule_existing_limits_preserved(tmp_path, db):
    enqueue_auto(db, "q-d1", "job-d1", "Daily One")
    enqueue_auto(db, "q-d2", "job-d2", "Daily Two")
    cfg = Config(artifacts_dir=str(tmp_path), db_path=str(db.db_path), default_production_engine="ffmpeg")
    real_engine = AutonomyEngine(config=cfg, db=db, policy=AutonomyPolicy(max_jobs_per_day=1))
    engine = make_scheduler(tmp_path, db, engine=real_engine)
    engine.create_schedule(schedule_id="s-lim", autonomy_level=4, cadence="daily", policy="mock",
                           next_run_at="2026-09-16T08:00:00+00:00", now=NOW)

    result = engine.run_now("s-lim", now=NOW, worker_id="w1")
    assert result.status == "completed"
    assert result.cycle_status == "completed"

    # Neither item may be produced today: the daily quota (1) was already consumed by the queue count.
    for qid in ("q-d1", "q-d2"):
        assert db.get_queue_item(qid)["status"] == "queued"
    assert db.get_schedule("s-lim")["total_runs"] == 1


# ---------------------------------------------------------------------------
# TEST 24: Schedule run records are idempotent — no double accounting.
# ---------------------------------------------------------------------------
def test_schedule_run_record_idempotency(tmp_path, db):
    engine = make_scheduler(tmp_path, db, engine=mock_engine())
    engine.create_schedule(schedule_id="s-idem", cadence="daily", next_run_at="2026-09-16T08:00:00+00:00", now=NOW)

    claimed = db.claim_due_schedule(worker_id="w1", now_iso=NOW.isoformat())
    assert claimed is not None

    ok = db.complete_schedule_run(
        schedule_id="s-idem", worker_id="w1", run_id="run-dup", channel_id="default", autonomy_level=3,
        cycle_run_id="cycle-1", run_status="completed", error_message=None, cycle_summary_json="{}",
        publish_calls=0, next_run_at="2026-09-17T10:00:00+00:00",
        started_at=NOW.isoformat(), completed_at=NOW.isoformat(), consecutive_failures=0,
    )
    assert ok is True
    # Duplicate delivery after a crash-replay: same run_id, lease already released.
    ok_redo = db.complete_schedule_run(
        schedule_id="s-idem", worker_id="w1", run_id="run-dup", channel_id="default", autonomy_level=3,
        cycle_run_id="cycle-1", run_status="completed", error_message=None, cycle_summary_json="{}",
        publish_calls=0, next_run_at="2026-09-17T10:00:00+00:00",
        started_at=NOW.isoformat(), completed_at=NOW.isoformat(), consecutive_failures=0,
    )
    assert ok_redo is False

    row = db.get_schedule("s-idem")
    assert row["total_runs"] == 1
    assert row["last_run_id"] == "run-dup"
    runs = db.list_schedule_runs("s-idem")
    assert len(runs) == 1
    assert runs[0]["run_id"] == "run-dup"


# ---------------------------------------------------------------------------
# TEST 25: CLI dispatch wiring for every schedule subcommand.
# ---------------------------------------------------------------------------
def test_schedule_cli_dispatch(tmp_path, capsys):
    import autopilot.cli.main as cli

    fake_engine = MagicMock()
    created = AutonomySchedule(schedule_id="s-cli", channel_id="default", autonomy_level=4,
                               cadence=ScheduleCadence.DAILY, timezone="UTC", max_items_per_run=5,
                               policy="local_only", next_run_at="2026-09-17T10:00:00+00:00")
    fake_engine.create_schedule.return_value = created
    fake_engine.list_schedules.return_value = [created]
    fake_engine.get_schedule.return_value = created
    fake_engine.enable_schedule.return_value = created
    fake_engine.disable_schedule.return_value = created.model_copy(update={"enabled": False})
    fake_engine.inspect_schedule.return_value = {"schedule": created.model_dump(), "runs": []}
    fake_engine.run_due = MagicMock(return_value=[])

    with patch.object(cli, "_schedule_engine", return_value=fake_engine):
        assert cli.run_schedule_list(output_json=False) == 0
        assert cli.run_schedule_create(channel_id="default", level=4, cadence="daily", limit=5, output_json=False) == 0
        assert cli.run_schedule_enable("s-cli", output_json=False) == 0
        assert cli.run_schedule_disable("s-cli", output_json=False) == 0
        assert cli.run_schedule_delete("s-cli", output_json=False) == 0
        assert cli.run_schedule_inspect("s-cli", output_json=False) == 0
        assert cli.run_schedule_run_due(limit=5, output_json=False) == 0

    fake_engine.create_schedule.side_effect = ValueError("boom")
    with patch.object(cli, "_schedule_engine", return_value=fake_engine):
        assert cli.run_schedule_create(channel_id="default", level=4, cadence="daily", output_json=False) == 1

    captured = capsys.readouterr().out
    assert "=== SCHEDULES ===" in captured
    assert "=== SCHEDULE CREATED" in captured


# ---------------------------------------------------------------------------
# TEST 26: Test isolation — scheduling never touches the production DB.
# ---------------------------------------------------------------------------
def test_schedule_test_isolation(tmp_path):
    default_db = Path(CONFIG.db_path)
    existed_before = default_db.exists()
    snap_before = default_db.stat().st_mtime_ns if existed_before else None

    db_path = tmp_path / "iso.db"
    iso_db = DBManager(str(db_path))
    iso_db.init_schema()

    eng = mock_engine()
    engine = make_scheduler(tmp_path, iso_db, engine=eng)
    engine.create_schedule(schedule_id="s-iso", cadence="daily", now=NOW)
    engine.run_now("s-iso", now=NOW, worker_id="w1")

    assert default_db.exists() == existed_before
    if snap_before is not None:
        assert default_db.stat().st_mtime_ns == snap_before
    assert iso_db.get_schedule("s-iso")["total_runs"] == 1


# ---------------------------------------------------------------------------
# TEST 27: Health command surfaces schedule status (total, enabled, due-now,
# runs, failures, last run). Uses a temp DB so the dev DB is untouched.
# ---------------------------------------------------------------------------
def test_health_surfaces_schedule_status(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "health.db"
    db = DBManager(str(db_path))
    db.init_schema()

    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    db.create_schedule(schedule_id="h-1", cadence="hourly", enabled=True, next_run_at=past)
    db.create_schedule(schedule_id="h-2", cadence="daily", enabled=False, next_run_at=past)

    monkeypatch.setattr(CONFIG, "db_path", db_path)

    import autopilot.cli.main as cli
    code = cli.run_health()
    assert code == 0

    out = capsys.readouterr().out
    report = json.loads(out.split("\n--- SUMMARY ---")[0])
    sched = report["scheduler"]
    assert sched["status"] == "AVAILABLE"
    assert sched["total_schedules"] == 2
    assert sched["enabled_schedules"] == 1
    assert sched["due_now"] == 1
    assert sched["total_runs"] == 0
    assert sched["schedules_failing"] == 0
    assert "Scheduler: AVAILABLE (2 schedules, 1 enabled, 1 due now, 0 runs)" in out