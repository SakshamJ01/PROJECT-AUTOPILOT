"""Phase 3: Recurring schedule orchestration.

The ScheduleEngine is a thin orchestrator on top of the existing autonomy
engine. It decides *when* a cycle runs, claims the due schedule atomically via
the database (overlap protection), invokes the existing Level 3
(AutonomyEngine.run_cycle) or Level 4 (AutonomyEngine.run_auto_produce_cycle)
path, persists the schedule run, and advances the next occurrence. It never
re-implements cycle logic and never invokes a public publisher.
"""
from __future__ import annotations
import json
import socket
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Optional

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from autopilot.core.channel import ChannelManager
from autopilot.core.config import Config, CONFIG
from autopilot.core.contracts import (
    AutonomyLevel,
    AutonomySchedule,
    OperationMode,
    ScheduleCadence,
    ScheduleRunSummary,
    WEEKDAY_NAMES,
    derive_operation_mode,
    validate_operation_mode,
)
from autopilot.core.logging import StructuredLogger
from autopilot.db.manager import DBManager

WEEKDAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
CYCLE_SUCCESS_STATUSES = {"completed", "completed_manual_mode", "blocked"}

# Default poll interval (seconds) for the portable in-process ticker.
DEFAULT_LOOP_POLL_INTERVAL = 60.0


def validate_timezone(name: str) -> ZoneInfo:
    """Validate an IANA timezone name; raises ValueError on any invalid input."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("timezone must be a non-empty IANA timezone name (e.g. 'UTC', 'America/New_York')")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Invalid IANA timezone: {name!r}") from exc


def validate_days_of_week(days: Optional[list]) -> list:
    """Validate/normalize weekday names (accept 'mon', 'Monday', 'monday' -> 'mon')."""
    normalized: list = []
    for raw in days or []:
        token = str(raw).strip().lower()[:3]
        if token not in WEEKDAY_INDEX:
            raise ValueError(f"Invalid weekday {token!r}; expected one of {WEEKDAY_NAMES}")
        normalized.append(token)
    return normalized


def next_run_after(schedule: AutonomySchedule, now_utc: datetime) -> datetime:
    """Compute the next run occurrence in the schedule's timezone.

    Cadence semantics (bounded catch-up is handled by the engine: exactly one
    run executes, then next_run_at advances from ``now``):
      - hourly  : now + 1 hour
      - daily   : now + 1 day
      - weekly  : advance to the next allowed weekday in ``days_of_week``, or
                  the same weekday + 7 days when ``days_of_week`` is empty
      - weekdays: advance to the next Mon..Fri weekday
    """
    tz = validate_timezone(schedule.timezone)
    local = now_utc.astimezone(tz)
    if schedule.cadence == ScheduleCadence.HOURLY:
        return (local + timedelta(hours=1)).astimezone(dt_timezone.utc)
    if schedule.cadence == ScheduleCadence.DAILY:
        return (local + timedelta(days=1)).astimezone(dt_timezone.utc)
    if schedule.cadence == ScheduleCadence.WEEKDAYS:
        return _next_weekday_occurrence(local, {0, 1, 2, 3, 4})
    allowed = {WEEKDAY_INDEX[d] for d in validate_days_of_week(schedule.days_of_week)}
    if not allowed:
        allowed = {local.weekday()}
    return _next_weekday_occurrence(local, allowed)


def _next_weekday_occurrence(local: datetime, allowed: set) -> datetime:
    candidate = local + timedelta(days=1)
    for _ in range(8):
        if candidate.weekday() in allowed:
            return candidate.astimezone(dt_timezone.utc)
        candidate += timedelta(days=1)
    return (local + timedelta(days=1)).astimezone(dt_timezone.utc)


class ScheduleEngine:
    """Thin orchestration layer for recurring autonomy cycles."""

    RUNNER = "scheduler"

    def __init__(
        self,
        config: Optional[Config] = None,
        db: Optional[DBManager] = None,
        engine: Optional[object] = None,
        channel_manager: Optional[ChannelManager] = None,
    ):
        self.config = config or CONFIG
        self.db = db or DBManager(self.config.db_path)
        self.db.init_schema()
        self.engine = engine
        self.channel_manager = channel_manager or ChannelManager(db=self.db)
        self.logger = StructuredLogger(stage="scheduler")

    # ------------------------------------------------------------------
    # Construction / helpers
    # ------------------------------------------------------------------

    def _autonomy_engine(self):
        if self.engine is None:
            from autopilot.core.autonomy import AutonomyEngine

            self.engine = AutonomyEngine(config=self.config, db=self.db)
        return self.engine

    @staticmethod
    def _worker_id() -> str:
        return f"{ScheduleEngine.RUNNER}-{socket.gethostname()}-{uuid.uuid4().hex[:6]}"

    @staticmethod
    def _schedule_from_row(row: dict) -> AutonomySchedule:
        return AutonomySchedule(
            schedule_id=row["schedule_id"],
            channel_id=row["channel_id"],
            autonomy_level=int(row["autonomy_level"]),
            # Legacy (pre-v9) rows have no stored mode; derive it from the level.
            operation_mode=(row["operation_mode"] if "operation_mode" in row.keys() else None)
            or derive_operation_mode(int(row["autonomy_level"])),
            enabled=bool(row["enabled"]),
            cadence=ScheduleCadence(row["cadence"]),
            days_of_week=json.loads(row["days_of_week"] or "[]"),
            timezone=row["timezone"],
            max_items_per_run=int(row["max_items_per_run"]),
            dry_run=bool(row["dry_run"]),
            policy=row["policy"],
            next_run_at=row["next_run_at"],
            last_run_at=row["last_run_at"],
            last_run_id=row["last_run_id"],
            last_run_status=row["last_run_status"],
            total_runs=int(row["total_runs"]),
            consecutive_failures=int(row["consecutive_failures"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _validate_inputs(
        self,
        channel_id: str,
        autonomy_level: int,
        cadence: str,
        timezone: str,
        days_of_week: Optional[list],
        max_items_per_run: int,
        dry_run: bool,
        operation_mode: Optional[str] = None,
    ) -> str:
        """Validates schedule inputs; returns the resolved operation_mode."""
        if not channel_id or not str(channel_id).strip():
            raise ValueError("channel_id must be a non-empty channel identifier")
        if int(autonomy_level) not in (AutonomyLevel.LEVEL_3_AUTO_QUEUE, AutonomyLevel.LEVEL_4_AUTO_PRODUCE):
            raise ValueError("autonomy_level must be 3 (guarded auto-queue) or 4 (guarded auto-produce)")
        try:
            ScheduleCadence(str(cadence).lower())
        except ValueError as exc:
            raise ValueError(f"Invalid cadence {cadence!r}; expected one of {[c.value for c in ScheduleCadence]}") from exc
        validate_timezone(timezone)
        validate_days_of_week(days_of_week or [])
        if int(max_items_per_run) < 1:
            raise ValueError("max_items_per_run must be at least 1")
        return validate_operation_mode(operation_mode, int(autonomy_level))

    # ------------------------------------------------------------------
    # Schedule CRUD
    # ------------------------------------------------------------------

    def create_schedule(
        self,
        channel_id: str = "default",
        autonomy_level: int = 3,
        operation_mode: Optional[str] = None,
        cadence: str = "daily",
        timezone: str = "UTC",
        days_of_week: Optional[list] = None,
        max_items_per_run: int = 10,
        dry_run: bool = False,
        policy: str = "local_only",
        schedule_id: Optional[str] = None,
        next_run_at: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> AutonomySchedule:
        """Create a schedule with validation and fail-closed contract checks."""
        resolved_mode = self._validate_inputs(
            channel_id, autonomy_level, cadence, timezone, days_of_week, max_items_per_run, dry_run, operation_mode
        )
        cadence_enum = ScheduleCadence(str(cadence).lower())
        days = validate_days_of_week(days_of_week or [])
        now = now or datetime.now(dt_timezone.utc)
        sid = schedule_id or f"sch-{uuid.uuid4().hex[:8]}"
        model = AutonomySchedule(
            schedule_id=sid,
            channel_id=str(channel_id).strip(),
            autonomy_level=int(autonomy_level),
            operation_mode=resolved_mode,
            cadence=cadence_enum,
            days_of_week=days,
            timezone=timezone,
            max_items_per_run=int(max_items_per_run),
            dry_run=bool(dry_run),
            policy=policy,
            next_run_at=next_run_at or next_run_after(
                AutonomySchedule(schedule_id=sid, channel_id=str(channel_id).strip(), autonomy_level=int(autonomy_level), cadence=cadence_enum, days_of_week=days, timezone=timezone),
                now,
            ).isoformat(),
        )
        if not self.db.create_schedule(
            schedule_id=sid,
            channel_id=model.channel_id,
            autonomy_level=model.autonomy_level,
            operation_mode=model.operation_mode,
            cadence=model.cadence.value,
            timezone_name=model.timezone,
            days_of_week=model.days_of_week,
            max_items_per_run=model.max_items_per_run,
            dry_run=model.dry_run,
            policy=model.policy,
            enabled=True,
            next_run_at=model.next_run_at,
        ):
            raise ValueError(f"Schedule already exists: {sid}")
        self.logger.info(
            "schedule_created",
            details={
                "schedule_id": sid,
                "channel_id": model.channel_id,
                "autonomy_level": model.autonomy_level,
                "operation_mode": model.operation_mode,
                "cadence": model.cadence.value,
                "timezone": model.timezone,
                "dry_run": model.dry_run,
                "next_run_at": model.next_run_at,
            },
        )
        return self.get_schedule(sid)

    def get_schedule(self, schedule_id: str) -> AutonomySchedule:
        row = self.db.get_schedule(schedule_id)
        if row is None:
            raise ValueError(f"Schedule not found: {schedule_id}")
        return self._schedule_from_row(row)

    def list_schedules(self, channel_id: Optional[str] = None, enabled: Optional[bool] = None) -> list:
        return [self._schedule_from_row(r) for r in self.db.list_schedules(channel_id=channel_id, enabled=enabled)]

    def enable_schedule(self, schedule_id: str) -> AutonomySchedule:
        schedule = self.get_schedule(schedule_id)
        now = datetime.now(dt_timezone.utc)
        next_run = schedule.next_run_at
        if next_run and self.db._coerce_dt(next_run) < now:
            next_run = next_run_after(schedule, now).isoformat()
        self.db.update_schedule(schedule_id, enabled=True, next_run_at=next_run)
        self.logger.info(
            "schedule_enabled",
            details={"schedule_id": schedule_id, "channel_id": schedule.channel_id, "autonomy_level": schedule.autonomy_level},
        )
        return self.get_schedule(schedule_id)

    def disable_schedule(self, schedule_id: str) -> AutonomySchedule:
        self.get_schedule(schedule_id)
        self.db.update_schedule(schedule_id, enabled=False)
        self.logger.info(
            "schedule_disabled",
            details={"schedule_id": schedule_id, "channel_id": self.get_schedule(schedule_id).channel_id, "autonomy_level": self.get_schedule(schedule_id).autonomy_level},
        )
        return self.get_schedule(schedule_id)

    def delete_schedule(self, schedule_id: str) -> bool:
        if self.db.get_schedule(schedule_id) is None:
            raise ValueError(f"Schedule not found: {schedule_id}")
        removed = self.db.delete_schedule(schedule_id)
        self.logger.info("schedule_deleted", details={"schedule_id": schedule_id})
        return removed

    def inspect_schedule(self, schedule_id: str, limit: int = 20) -> dict:
        schedule = self.get_schedule(schedule_id)
        return {"schedule": schedule.model_dump(), "runs": self.db.list_schedule_runs(schedule_id, limit=limit)}

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run_due(self, now: Optional[datetime] = None, max_runs: int = 10, worker_id: Optional[str] = None) -> list:
        """Execute every due schedule exactly once (bounded catch-up), oldest first.

        Returns one ScheduleRunSummary per executed schedule.
        """
        now = now or datetime.now(dt_timezone.utc)
        worker_id = worker_id or self._worker_id()
        summaries = []
        for _ in range(max(1, int(max_runs))):
            row = self.db.claim_due_schedule(worker_id=worker_id, now_iso=now.isoformat())
            if row is None:
                break
            schedule = self._schedule_from_row(row)
            self.logger.info(
                "schedule_claimed",
                details={"schedule_id": schedule.schedule_id, "channel_id": schedule.channel_id, "autonomy_level": schedule.autonomy_level},
            )
            summaries.append(self._execute(schedule, row, worker_id, now))
        return summaries

    def run_now(self, schedule_id: str, now: Optional[datetime] = None, worker_id: Optional[str] = None) -> ScheduleRunSummary:
        """Execute a single schedule immediately (manual control), regardless of due-ness."""
        now = now or datetime.now(dt_timezone.utc)
        row = self.db.get_schedule(schedule_id)
        if row is None:
            raise ValueError(f"Schedule not found: {schedule_id}")
        schedule = self._schedule_from_row(row)
        if not schedule.enabled:
            return ScheduleRunSummary(
                run_id=_fresh_run_id(now),
                schedule_id=schedule_id,
                channel_id=schedule.channel_id,
                autonomy_level=schedule.autonomy_level,
                status="blocked",
                error_message="Schedule is disabled.",
            )
        worker_id = worker_id or self._worker_id()
        claimed = self.db.claim_schedule_by_id(schedule_id=schedule_id, worker_id=worker_id, now_iso=now.isoformat())
        if claimed is None:
            return ScheduleRunSummary(
                run_id=_fresh_run_id(now),
                schedule_id=schedule_id,
                channel_id=schedule.channel_id,
                autonomy_level=schedule.autonomy_level,
                status="skipped",
                error_message="Schedule is already claimed by another worker.",
            )
        self.logger.info(
            "schedule_claimed",
            details={"schedule_id": schedule.schedule_id, "channel_id": schedule.channel_id, "autonomy_level": schedule.autonomy_level},
        )
        return self._execute(schedule, claimed, worker_id, now)

    # ------------------------------------------------------------------
    # Portable in-process ticker (thin wrapper around run_due)
    # ------------------------------------------------------------------

    def run_loop(
        self,
        poll_interval: Optional[float] = None,
        max_iterations: Optional[int] = None,
        now: Optional[datetime] = None,
        stop_event: Optional["object"] = None,
        worker_id: Optional[str] = None,
        max_runs_per_tick: int = 10,
    ) -> dict:
        """Portable in-process ticker: repeatedly executes due schedules.

        This is a *thin timing wrapper* only.  It contains no production, queue,
        provider, or publish logic: every tick delegates entirely to the existing
        :meth:`run_due`, which atomically claims schedules (overlap protection),
        dispatches to the existing Level 3 / Level 4 engines, and records each
        run.  All leases, guardrails, budgets, and the READY_TO_PUBLISH boundary
        are therefore preserved unchanged.

        Shutdown is graceful: SIGINT (and SIGTERM where available) set an event
        flag; the current tick finishes and the loop exits without leaving a
        schedule claimed.  ``signal.signal`` is only attempted in the main
        thread (skipped silently otherwise) so the ticker stays cross-platform.

        Deterministic testing: pass ``max_iterations`` to bound the loop and/or
        a ``threading.Event`` as ``stop_event``; a zero/None ``poll_interval``
        disables sleeping entirely.
        """
        import signal
        import threading

        interval = DEFAULT_LOOP_POLL_INTERVAL if poll_interval is None else float(poll_interval)
        stop = stop_event if stop_event is not None else threading.Event()
        worker_id = worker_id or self._worker_id()

        installed_handlers: list = []
        if threading.current_thread() is threading.main_thread():
            for sig_name in ("SIGINT", "SIGTERM"):
                sig = getattr(signal, sig_name, None)
                if sig is None:
                    continue
                try:
                    signal.signal(sig, lambda signum, frame: stop.set())
                    installed_handlers.append(sig)
                except Exception:  # pragma: no cover - platform restrictions
                    pass

        self.logger.info(
            "schedule_loop_started",
            details={
                "worker_id": worker_id,
                "poll_interval": interval,
                "max_iterations": max_iterations,
                "max_runs_per_tick": max_runs_per_tick,
            },
        )

        iterations = 0
        all_summaries: list = []
        try:
            while not stop.is_set():
                tick_summaries = self.run_due(now=now, worker_id=worker_id, max_runs=max_runs_per_tick)
                all_summaries.extend(tick_summaries)
                iterations += 1
                self.logger.info(
                    "schedule_loop_tick",
                    details={"iteration": iterations, "executed": len(tick_summaries)},
                )
                if max_iterations is not None and iterations >= int(max_iterations):
                    break
                if stop.is_set() or not interval or interval <= 0:
                    break
                # Interruptible wait so a signal can wake us immediately.
                stop.wait(interval)
        finally:
            for sig in installed_handlers:
                try:
                    signal.signal(sig, signal.SIG_DFL)
                except Exception:  # pragma: no cover
                    pass

        self.logger.info(
            "schedule_loop_stopped",
            details={"iterations": iterations, "total_executions": len(all_summaries)},
        )
        return {"iterations": iterations, "summaries": all_summaries}

    # ------------------------------------------------------------------
    # Stage execution (Level 3 / Level 4 / combined operation loop)
    # ------------------------------------------------------------------

    def _run_level3_stage(self, schedule: AutonomySchedule, engine, now: datetime) -> dict:
        """Run the existing Level 3 engine for this schedule. Never raises.

        Returns a stage result dict (run_id / status / summary_json / jobs_queued
        / error_message).  A Level 3 failure is *isolated*: it never manufactures
        queue records or a fake success.
        """
        try:
            summary = engine.run_cycle(
                autonomy_level=AutonomyLevel.LEVEL_3_AUTO_QUEUE,
                channel_id=schedule.channel_id,
                dry_run=schedule.dry_run,
                limit=schedule.max_items_per_run,
            )
            return {
                "run_id": summary.run_id,
                "status": summary.status,
                "summary_json": summary.model_dump_json(),
                "jobs_queued": summary.jobs_queued,
                "error_message": str(summary.error_message)[:500]
                if summary.status == "failed" and summary.error_message
                else None,
            }
        except Exception as exc:  # pragma: no cover - defensive; engine returns failed summaries
            return {"run_id": None, "status": "failed", "summary_json": "{}", "jobs_queued": 0, "error_message": str(exc)[:500]}

    def _run_level4_stage(self, schedule: AutonomySchedule, engine, now: datetime) -> dict:
        """Run the existing Level 4 engine for this schedule. Never raises.

        Level 4 stops at READY_TO_PUBLISH and never invokes a public publisher.
        """
        try:
            summary = engine.run_auto_produce_cycle(
                channel_id=schedule.channel_id,
                limit=schedule.max_items_per_run,
                dry_run=schedule.dry_run,
                policy=schedule.policy,
            )
            return {
                "run_id": summary.run_id,
                "status": summary.status,
                "summary_json": summary.model_dump_json(),
                "jobs_ready_to_publish": summary.jobs_ready_to_publish,
                "error_message": str(summary.error_message)[:500]
                if summary.status == "failed" and summary.error_message
                else None,
            }
        except Exception as exc:  # pragma: no cover - defensive; engine returns failed summaries
            return {
                "run_id": None,
                "status": "failed",
                "summary_json": "{}",
                "jobs_ready_to_publish": 0,
                "error_message": str(exc)[:500],
            }

    def _run_operation_loop(self, schedule: AutonomySchedule, engine, now: datetime) -> dict:
        """Combined level3_then_level4 operation: Level 3 -> queue -> Level 4 -> STOP.

        Sequences the two existing engines without duplicating any of their
        logic.  Failure isolation: Level 4 runs only when Level 3 completed;
        otherwise it is skipped and the failure is recorded as the terminal
        stage.  The terminal success state is READY_TO_PUBLISH, never PUBLISHED.
        """
        level3 = self._run_level3_stage(schedule, engine, now)
        self.logger.info(
            "operation_loop_level3_done",
            details={
                "schedule_id": schedule.schedule_id,
                "channel_id": schedule.channel_id,
                "level3_run_id": level3["run_id"],
                "level3_status": level3["status"],
                "level3_jobs_queued": level3["jobs_queued"],
            },
        )

        level4 = None
        skip_reason = None
        if level3["status"] == "completed":
            level4 = self._run_level4_stage(schedule, engine, now)
            self.logger.info(
                "operation_loop_level4_done",
                details={
                    "schedule_id": schedule.schedule_id,
                    "channel_id": schedule.channel_id,
                    "level4_run_id": level4["run_id"],
                    "level4_status": level4["status"],
                    "level4_jobs_ready_to_publish": level4["jobs_ready_to_publish"],
                },
            )
        else:
            skip_reason = (
                f"Level 4 skipped: Level 3 finished with status {level3['status']!r} "
                "(failure isolation - no production without a completed discovery stage)"
            )
            self.logger.warning(
                "operation_loop_level4_skipped",
                details={"schedule_id": schedule.schedule_id, "level3_status": level3["status"]},
            )

        terminal = level4 if level4 is not None else level3
        combined_json = json.dumps(
            {
                "operation_mode": OperationMode.LEVEL_3_THEN_4.value,
                "level3": json.loads(level3["summary_json"] or "{}"),
                "level4": json.loads(level4["summary_json"] or "{}") if level4 else None,
                "level4_skipped_reason": skip_reason,
            }
        )
        error_message = level4.get("error_message") if level4 is not None else level3.get("error_message")
        return {
            "level3_run_id": level3["run_id"],
            "level3_status": level3["status"],
            "level3_jobs_queued": level3["jobs_queued"],
            "level4_run_id": level4["run_id"] if level4 else None,
            "level4_status": level4["status"] if level4 else None,
            "level4_jobs_ready_to_publish": level4["jobs_ready_to_publish"] if level4 else 0,
            "cycle_run_id": terminal["run_id"],
            "cycle_status": terminal["status"],
            "cycle_summary_json": combined_json,
            "error_message": error_message,
            "level4_skipped_reason": skip_reason,
        }

    def _execute(self, schedule: AutonomySchedule, row: dict, worker_id: str, now: datetime) -> ScheduleRunSummary:
        run_id = _fresh_run_id(now)
        started_at = now.isoformat()
        cycle_run_id: Optional[str] = None
        cycle_status: Optional[str] = None
        cycle_summary_json = "{}"
        error_message: Optional[str] = None
        run_status = "failed"
        mode = schedule.operation_mode or derive_operation_mode(schedule.autonomy_level)

        # Stage-level detail for the operation-loop run record.
        level3_run_id = level3_status = level4_run_id = level4_status = None
        level3_jobs_queued = 0
        level4_jobs_ready = 0

        self.logger.info(
            "schedule_run_started",
            details={
                "schedule_id": schedule.schedule_id,
                "channel_id": schedule.channel_id,
                "autonomy_level": schedule.autonomy_level,
                "operation_mode": mode,
                "run_id": run_id,
                "dry_run": schedule.dry_run,
            },
        )
        try:
            engine = self._autonomy_engine()
            if mode == OperationMode.LEVEL_3_THEN_4.value:
                loop = self._run_operation_loop(schedule, engine, now)
                cycle_run_id = loop["cycle_run_id"]
                cycle_status = loop["cycle_status"]
                cycle_summary_json = loop["cycle_summary_json"]
                error_message = loop["error_message"]
                level3_run_id, level3_status, level3_jobs_queued = loop["level3_run_id"], loop["level3_status"], loop["level3_jobs_queued"]
                level4_run_id, level4_status, level4_jobs_ready = loop["level4_run_id"], loop["level4_status"], loop["level4_jobs_ready_to_publish"]
                if error_message is None and loop.get("level4_skipped_reason"):
                    error_message = loop["level4_skipped_reason"]
            elif mode == OperationMode.LEVEL_4_ONLY.value:
                stage = self._run_level4_stage(schedule, engine, now)
                cycle_run_id, cycle_status = stage["run_id"], stage["status"]
                cycle_summary_json = stage["summary_json"]
                error_message = stage["error_message"]
                level4_run_id, level4_status, level4_jobs_ready = stage["run_id"], stage["status"], stage["jobs_ready_to_publish"]
            else:
                stage = self._run_level3_stage(schedule, engine, now)
                cycle_run_id, cycle_status = stage["run_id"], stage["status"]
                cycle_summary_json = stage["summary_json"]
                error_message = stage["error_message"]
                level3_run_id, level3_status, level3_jobs_queued = stage["run_id"], stage["status"], stage["jobs_queued"]
        except Exception as exc:  # pragma: no cover - defensive; stages never raise
            error_message = str(exc)[:500]
            cycle_status = "failed"

        run_status = "completed" if (cycle_status or "") in CYCLE_SUCCESS_STATUSES else "failed"
        if error_message is None and run_status == "failed":
            error_message = f"Cycle finished with status {cycle_status!r}"
        consecutive_failures = schedule.consecutive_failures + (0 if run_status == "completed" else 1)
        next_run = next_run_after(schedule, now)
        completed_at = datetime.now(dt_timezone.utc).isoformat()

        self.db.complete_schedule_run(
            schedule_id=schedule.schedule_id,
            worker_id=worker_id,
            run_id=run_id,
            channel_id=schedule.channel_id,
            autonomy_level=schedule.autonomy_level,
            cycle_run_id=cycle_run_id,
            run_status=run_status,
            error_message=error_message,
            cycle_summary_json=cycle_summary_json,
            publish_calls=0,
            next_run_at=next_run.isoformat(),
            started_at=started_at,
            completed_at=completed_at,
            consecutive_failures=consecutive_failures,
        )

        event_details = {
            "schedule_id": schedule.schedule_id,
            "channel_id": schedule.channel_id,
            "autonomy_level": schedule.autonomy_level,
            "operation_mode": mode,
            "run_id": run_id,
            "cycle_run_id": cycle_run_id,
            "cycle_status": cycle_status,
            "level3_run_id": level3_run_id,
            "level3_status": level3_status,
            "level3_jobs_queued": level3_jobs_queued,
            "level4_run_id": level4_run_id,
            "level4_status": level4_status,
            "level4_jobs_ready_to_publish": level4_jobs_ready,
            "next_run_at": next_run.isoformat(),
        }
        if run_status == "completed":
            self.logger.info("schedule_run_completed", details=event_details)
        else:
            self.logger.error("schedule_run_failed", error=error_message or "schedule run failed", details=event_details)
        self.logger.info(
            "schedule_released",
            details={"schedule_id": schedule.schedule_id, "run_id": run_id, "next_run_at": next_run.isoformat()},
        )
        return ScheduleRunSummary(
            run_id=run_id,
            schedule_id=schedule.schedule_id,
            channel_id=schedule.channel_id,
            autonomy_level=schedule.autonomy_level,
            operation_mode=mode,
            status=run_status,
            cycle_run_id=cycle_run_id,
            cycle_status=cycle_status,
            level3_run_id=level3_run_id,
            level3_status=level3_status,
            level3_jobs_queued=level3_jobs_queued,
            level4_run_id=level4_run_id,
            level4_status=level4_status,
            level4_jobs_ready_to_publish=level4_jobs_ready,
            next_run_at=next_run.isoformat(),
            publish_calls=0,
            error_message=error_message,
        )


def _fresh_run_id(now: datetime) -> str:
    return f"schrun-{now.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"