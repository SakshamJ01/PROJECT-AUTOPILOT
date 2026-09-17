"""M1+M2 bridge handlers — thin wrappers over EXISTING backend surfaces.

Each handler delegates to the existing DB / engine layer. No business logic
lives here: decisions, gates and state transitions remain in the backend.
"""

from __future__ import annotations

import json as _json
import os
import platform
import sqlite3
import sys
import time
import uuid
from typing import Any, Optional

from autopilot.bridge import BRIDGE_VERSION
from autopilot.bridge.protocol import (
    INVALID_PARAMS,
    INTERNAL_ERROR,
    METHOD_NOT_FOUND,
    ProtocolError,
)
from autopilot.core.config import Config
from autopilot.db.manager import DBManager


# Canonical queue stage ordering for timeline display.
_STAGE_ORDER = [
    "RESEARCH", "SCRIPT", "VOICE", "ASSETS", "RENDER", "QA",
    "PUBLISH", "COMPLETE",
]


class BridgeHandlers:
    METHOD_NAMES = (
        "ping",
        "system.status",
        "health.get",
        "queue.list",
        "job.inspect",
        "events.tail",
        "logs.tail",
        "production.start",
        "production.cancel",
        "production.retry",
        "autonomy.status",
        "autonomy.run",
        "autonomy.proposals",
        "autonomy.inspect",
        "autonomy.proposal.approve",
        "autonomy.proposal.reject",
        "scheduler.status",
        "scheduler.list",
        "scheduler.inspect",
        "scheduler.create",
        "scheduler.update",
        "scheduler.enable",
        "scheduler.disable",
        "scheduler.delete",
        "scheduler.run_now",
        "scheduler.run_due",
        "system.shutdown",
        "system.restart",
    )

    def __init__(self, config: Config, db: DBManager) -> None:
        self.config = config
        self.db = db
        self._started_at = time.time()

    # ------------------------------------------------------------------
    # dispatch
    # ------------------------------------------------------------------
    def dispatch(self, method: str, params: dict | None) -> Any:
        handler = getattr(self, "on_" + method.replace(".", "_"), None)
        if handler is None:
            raise ProtocolError(METHOD_NOT_FOUND, f"Method not found: {method}")
        return handler(params)

    # ------------------------------------------------------------------
    # liveness / control
    # ------------------------------------------------------------------
    def on_ping(self, params: dict | None) -> dict[str, Any]:
        return {
            "pong": True,
            "pid": os.getpid(),
            "bridge_version": BRIDGE_VERSION,
            "python": platform.python_version(),
            "db": str(self.config.db_path),
        }

    def on_system_status(self, params: dict | None) -> dict[str, Any]:
        db_path = self.config.db_path
        return {
            "state": "ready",
            "pid": os.getpid(),
            "bridge_version": BRIDGE_VERSION,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "uptime_seconds": round(time.time() - self._started_at, 1),
            "db": {
                "path": str(db_path),
                "exists": db_path.exists(),
                "schema_version": self._schema_version(),
            },
            "artifacts_dir": str(self.config.get_artifacts_dir()),
            "config_valid": True,
        }

    def on_system_shutdown(self, params: dict | None) -> dict[str, Any]:
        return {"shutdown": True}

    def on_system_restart(self, params: dict | None) -> dict[str, Any]:
        return {"restarting": True}

    # ------------------------------------------------------------------
    # health
    # ------------------------------------------------------------------
    def on_health_get(self, params: dict | None) -> dict[str, Any]:
        from autopilot.cli.main import _learning_health, check_ffmpeg, check_sqlite, get_schedule_status_summary

        ffmpeg = check_ffmpeg()
        sqlite = check_sqlite()
        artifacts_dir = self.config.get_artifacts_dir()
        cache_dir = self.config.get_asset_cache_dir()
        db_path = self.config.db_path
        sqlite_ok = sqlite.get("available", False) and db_path.exists()

        return {
            "status": "healthy" if ffmpeg.get("available", False) and sqlite_ok else "degraded",
            "python_version": sys.version,
            "platform": platform.platform(),
            "ffmpeg": ffmpeg,
            "sqlite": sqlite,
            "artifacts_dir": str(artifacts_dir),
            "asset_cache_dir": str(cache_dir),
            "db": {
                "path": str(db_path),
                "exists": db_path.exists(),
                "schema_version": self._schema_version(),
            },
            "qa_engine": {"available": True, "status": "AVAILABLE"},
            "queue_engine": {
                "status": "AVAILABLE",
                "summary": self.db.get_queue_status_summary(),
            },
            "worker": {"status": "AVAILABLE"},
            "scheduler": get_schedule_status_summary(self.db),
            "analytics_engine": {
                "status": "AVAILABLE",
                "default_provider": self.config.analytics_default_provider,
            },
            "learning_engine": _learning_health(self.db),
            "autonomy_engine": {
                "status": "AVAILABLE",
                "autonomy_level": self.config.autonomy_level,
                "max_ideas_per_cycle": self.config.autonomy_max_ideas_per_cycle,
                "max_daily_jobs": self.config.autonomy_max_daily_jobs,
            },
            "autonomy_auto_publish_enabled": bool(self.config.autonomy_auto_publish),
            "publishing": {
                "status": "AVAILABLE",
                "counts": self.db.get_publish_health_counts(),
            },
            "channels": self._channel_counts(),
            "providers": self._provider_status(),
        }

    def _channel_counts(self) -> dict[str, Any]:
        try:
            from autopilot.core.channel import ChannelManager

            manager = ChannelManager(self.db)
            all_channels = manager.list_channels()
            enabled = [c for c in all_channels if getattr(c, "status", None) and c.status.value == "active"]
            return {
                "status": "AVAILABLE",
                "total": len(all_channels),
                "enabled": len(enabled),
            }
        except Exception as exc:  # noqa: BLE001 — health must be graceful
            return {"status": "DEGRADED", "error": str(exc)}

    def _provider_status(self) -> dict[str, Any]:
        configured = {
            "llm": self.config.provider_default_llm,
            "tts": self.config.provider_default_tts,
            "asr": self.config.provider_default_asr,
            "asset": self.config.provider_default_asset,
            "publisher": self.config.provider_default_publisher,
            "production_engine": self.config.default_production_engine,
        }
        yt = {
            "status": "configured" if self.config.youtube_access_token else "unconfigured",
            "token_present": bool(self.config.youtube_access_token),
        }
        return {"configured": configured, "youtube": yt}

    # ------------------------------------------------------------------
    # queue / jobs
    # ------------------------------------------------------------------
    def on_queue_list(self, params: dict | None) -> dict[str, Any]:
        params = params or {}
        status = params.get("status")
        channel_id = params.get("channel_id")
        search = params.get("search")
        limit = self._int_param(params, "limit", default=50, minimum=1, maximum=500)
        rows = self.db.list_queue_items(status=status, limit=limit, channel_id=channel_id)
        # Enrich rows with parsed payload fields (thin serialisation, not logic).
        enriched = [self._enrich_queue_item(r) for r in rows]
        if search:
            needle = str(search).lower()
            enriched = [
                r for r in enriched
                if needle in str(r.get("topic") or "").lower()
                or needle in str(r.get("job_id") or "").lower()
                or needle in str(r.get("queue_id") or "").lower()
            ]
        return {
            "items": enriched,
            "summary": self.db.get_queue_status_summary(),
        }

    def on_job_inspect(self, params: dict | None) -> dict[str, Any]:
        params = params or {}
        job_id = params.get("job_id")
        if not job_id:
            raise ProtocolError(INVALID_PARAMS, "Missing required param: job_id")
        job = self.db.get_job(job_id)
        if job is None:
            return {"found": False, "job_id": job_id}
        # Fetch QA reports (most recent first, include checks/findings).
        qa_reports = self.db.get_qa_reports_for_job(job_id)
        enriched_qa = []
        for report in qa_reports[:3]:
            report_id = report.get("report_id", "")
            enriched_qa.append({
                **report,
                "checks": self.db.get_qa_checks(report_id),
                "findings": self.db.get_qa_findings(report_id),
            })
        return {
            "found": True,
            "job_id": job_id,
            "job": job,
            "events": self.db.get_events(job_id),
            "artifacts": self.db.get_artifacts_for_job(job_id),
            "errors": self.db.get_errors_for_job(job_id),
            "queue_item": self._enrich_queue_item(
                self.db.get_queue_item_by_job(job_id) or {}
            ),
            "publications": self.db.get_publications_for_job(job_id),
            "qa_reports": enriched_qa,
            "stage_order": _STAGE_ORDER,
        }

    # ------------------------------------------------------------------
    # production control
    # ------------------------------------------------------------------
    def on_production_start(self, params: dict | None) -> dict[str, Any]:
        """Enqueue and synchronously execute a production pipeline job.

        Delegates to LocalWorker.process_claimed_item for the canonical
        pipeline path. Runs on a bridge pool thread so the stdio loop
        stays responsive.
        """
        params = params or {}
        topic = (params.get("topic") or "").strip()
        if not topic:
            raise ProtocolError(INVALID_PARAMS, "Missing required param: topic")

        channel_id = params.get("channel", "default")
        policy = params.get("policy", "local_only")
        profile = params.get("profile", "short_vertical")
        auto_publish = False  # M2 never allows publishing from desktop

        # Provider overrides — only include if explicitly supplied.
        provider_overrides: dict[str, str] = {}
        for key in ("llm", "research", "tts", "asset", "production_engine"):
            val = params.get(f"{key}_provider")
            if val is not None:
                provider_overrides[key] = val
        provider_overrides["policy"] = policy

        job_id = f"prod-{topic.replace(' ', '-')[:30]}-{uuid.uuid4().hex[:8]}"
        queue_id = f"desk-{uuid.uuid4().hex[:12]}"

        payload: dict[str, Any] = {
            "topic": topic,
            "channel_id": channel_id,
            "policy": policy,
            "profile": profile,
            "auto_publish": auto_publish,
        }
        # Persist provider names in payload for audit / inspection.
        for key in ("llm", "research", "tts", "asset", "production_engine"):
            val = params.get(f"{key}_provider")
            if val is not None:
                payload[f"{key}_provider"] = val

        self.db.enqueue_item(
            queue_id=queue_id,
            job_id=job_id,
            priority=3,
            channel_id=channel_id,
            payload=payload,
        )

        # Claim the item we just enqueued (must be in queued state).
        claimed = self.db.claim_queue_item(
            queue_id=queue_id,
            worker_id=f"desktop-{os.getpid()}",
            lease_duration_sec=3600,
        )
        if claimed is None:
            raise ProtocolError(INTERNAL_ERROR, "Failed to claim newly enqueued item")

        # Execute pipeline synchronously via the canonical worker path.
        from autopilot.core.worker import LocalWorker

        worker = LocalWorker(
            worker_id=f"desktop-{os.getpid()}",
            config=self.config,
            db=self.db,
        )
        try:
            result = worker.process_claimed_item(
                claimed,
                provider_overrides=provider_overrides or None,
                force_auto_publish=False,
            )
        except Exception as exc:  # noqa: BLE001 — must not crash the bridge
            self.db.fail_queue_item(queue_id, str(exc), retryable=False)
            result = {
                "queue_id": queue_id,
                "job_id": job_id,
                "status": "failed",
                "error": str(exc),
            }

        return {
            "queue_id": queue_id,
            "job_id": job_id,
            "status": result.get("status", "unknown"),
            "media_path": result.get("media_path"),
            "qa_status": result.get("qa_status"),
            "error": result.get("error"),
        }

    def on_production_cancel(self, params: dict | None) -> dict[str, Any]:
        """Cancel a queue item by job_id or queue_id."""
        params = params or {}
        job_id = params.get("job_id")
        queue_id = params.get("queue_id")
        if not job_id and not queue_id:
            raise ProtocolError(
                INVALID_PARAMS, "Missing required param: job_id or queue_id"
            )
        if not queue_id and job_id:
            item = self.db.get_queue_item_by_job(job_id)
            if item is None:
                raise ProtocolError(INVALID_PARAMS, f"No queue item for job_id={job_id}")
            queue_id = item["queue_id"]
        ok = self.db.cancel_queue_item(queue_id)
        return {"cancelled": ok, "queue_id": queue_id}

    def on_production_retry(self, params: dict | None) -> dict[str, Any]:
        """Retry a failed/blocked/cancelled queue item by job_id or queue_id."""
        params = params or {}
        job_id = params.get("job_id")
        queue_id = params.get("queue_id")
        if not job_id and not queue_id:
            raise ProtocolError(
                INVALID_PARAMS, "Missing required param: job_id or queue_id"
            )
        if not queue_id and job_id:
            item = self.db.get_queue_item_by_job(job_id)
            if item is None:
                raise ProtocolError(INVALID_PARAMS, f"No queue item for job_id={job_id}")
            queue_id = item["queue_id"]
        ok = self.db.retry_queue_item(queue_id)
        return {"retried": ok, "queue_id": queue_id}

    # ------------------------------------------------------------------
    # helpers — queue enrichment
    # ------------------------------------------------------------------
    @staticmethod
    def _enrich_queue_item(row: dict) -> dict:
        """Parse payload_json and surface safe top-level fields."""
        if not row:
            return row
        payload_raw = row.get("payload_json") or "{}"
        try:
            payload = _json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
        except Exception:  # noqa: BLE001
            payload = {}
        row["topic"] = payload.get("topic")
        row["profile"] = payload.get("profile")
        row["policy"] = payload.get("policy")
        row["auto_publish"] = payload.get("auto_publish", False)
        row["publish_visibility"] = payload.get("publish_visibility")
        # Provider names are safe (no secrets).
        providers = {}
        for key in ("llm_provider", "research_provider", "tts_provider", "asset_provider", "production_engine"):
            val = payload.get(key)
            if val is not None:
                providers[key.replace("_provider", "")] = val
        if providers:
            row["providers"] = providers
        return row

    # ------------------------------------------------------------------
    # activity / logs
    # ------------------------------------------------------------------
    def on_events_tail(self, params: dict | None) -> dict[str, Any]:
        params = params or {}
        limit = self._int_param(params, "limit", default=100, minimum=1, maximum=1000)
        since = params.get("since_event_id")
        if since is not None:
            since = self._int_param(params, "since_event_id", default=0, minimum=0)
        events = self.db.list_recent_events(
            limit=limit,
            job_id=params.get("job_id"),
            channel_id=params.get("channel_id"),
            since_event_id=since,
        )
        return {"events": events}

    def on_logs_tail(self, params: dict | None) -> dict[str, Any]:
        params = params or {}
        limit = self._int_param(params, "limit", default=100, minimum=1, maximum=1000)
        severity = params.get("severity")
        channel_id = params.get("channel_id")
        job_id = params.get("job_id")
        search = params.get("search")

        rows: list[dict[str, Any]] = []
        for event in self.db.list_recent_events(limit=limit * 2, job_id=job_id, channel_id=channel_id):
            reason = event.get("reason")
            message = f"transition {event.get('from_state')} -> {event.get('to_state')}"
            if reason:
                message += f" ({reason})"
            rows.append(
                {
                    "timestamp": event.get("occurred_at"),
                    "severity": "info",
                    "source": "event",
                    "stage": event.get("to_state"),
                    "job_id": event.get("job_id"),
                    "channel_id": event.get("channel_id"),
                    "topic": event.get("topic"),
                    "message": message,
                }
            )
        for error in self.db.list_recent_errors(limit=limit * 2, job_id=job_id, channel_id=channel_id):
            rows.append(
                {
                    "timestamp": error.get("occurred_at"),
                    "severity": "error",
                    "source": "error",
                    "stage": error.get("stage"),
                    "job_id": error.get("job_id"),
                    "channel_id": error.get("channel_id"),
                    "topic": error.get("topic"),
                    "message": f"{error.get('error_type')}: {error.get('message')}",
                }
            )

        rows.sort(key=lambda r: str(r.get("timestamp") or ""), reverse=True)

        if severity:
            rows = [r for r in rows if r.get("severity") == severity]
        if search:
            needle = str(search).lower()
            rows = [
                r
                for r in rows
                if needle in (str(r.get("message") or "").lower())
                or needle in str(r.get("job_id") or "").lower()
                or needle in str(r.get("topic") or "").lower()
            ]
        return {"entries": rows[:limit]}

    # ------------------------------------------------------------------
    # M3 — autonomy control surface
    # ------------------------------------------------------------------
    def on_autonomy_status(self, params: dict | None) -> dict[str, Any]:
        """Read-only autonomy state: configured level/mode, policy limits,
        active strategy, learning state, activity counts, scheduler summary."""
        from autopilot.cli.main import _learning_health, get_schedule_status_summary
        from autopilot.core.contracts import AutonomyLevel
        from autopilot.core.feedback import StrategyManager

        level = int(self.config.autonomy_level)
        level_name = AutonomyLevel(level).name
        mode = "manual" if level == 0 else ("assisted" if level <= 2 else "autonomous")

        recent_runs = self.db.list_autonomy_runs(limit=5)
        sched_summary = get_schedule_status_summary(self.db)
        active_executions = sum(
            1 for s in self.db.list_schedules() if s.get("leased_by")
        )
        running = any(r.get("status") == "running" for r in recent_runs) or active_executions > 0

        proposals = self.db.list_idea_proposals(limit=1000)
        proposal_counts = {"proposed": 0, "approved": 0, "rejected": 0, "queued": 0}
        for p in proposals:
            key = str(p.get("status") or "proposed")
            if key in proposal_counts:
                proposal_counts[key] += 1

        pub_counts = self.db.get_publish_health_counts()

        try:
            strategy = StrategyManager(self.db).get_active_strategy()
            active_strategy_version = strategy.version_id if strategy else None
        except Exception:  # noqa: BLE001
            active_strategy_version = None

        return {
            "level": level,
            "level_name": level_name,
            "mode": mode,
            "operational_status": (
                "running" if running else ("degraded" if sched_summary.get("status") == "DEGRADED" else "ready")
            ),
            "policy": {
                "autonomy_level": level,
                "max_ideas_per_cycle": self.config.autonomy_max_ideas_per_cycle,
                "max_auto_queue_per_cycle": self.config.autonomy_max_auto_queue_per_cycle,
                "max_jobs_per_day": self.config.autonomy_max_daily_jobs,
                "max_concurrent_jobs": self.config.queue_max_concurrency,
                "max_queued_jobs": self.config.queue_max_queued_jobs,
                "topic_cooldown_days": self.config.autonomy_topic_cooldown_days,
                "similarity_threshold": self.config.autonomy_similarity_threshold,
                "min_score_threshold": self.config.autonomy_min_score_threshold,
                "trend_provider": self.config.autonomy_trend_provider,
                "strategy_influence_scale": self.config.strategy_influence_scale,
            },
            "active_strategy_version": active_strategy_version,
            "learning": _learning_health(self.db),
            "activity": {
                "recent_runs": recent_runs,
                "proposal_counts": proposal_counts,
                "ready_to_publish": int(pub_counts.get("ready", 0)),
                "published": int(pub_counts.get("published", 0)),
                "produced_today": self.db.count_daily_produced_jobs(),
                "queued_today": self.db.count_autonomous_jobs_queued_today(),
            },
            "scheduler": sched_summary,
            "auto_publish_enabled": bool(self.config.autonomy_auto_publish),
            # M3 hard boundary: Level 4 stops at READY_TO_PUBLISH. No publish
            # action exists on this control surface.
            "publish_boundary": (
                "Level 4 production stops at READY_TO_PUBLISH. Public publishing "
                "is not available on this control surface."
            ),
        }

    def on_autonomy_run(self, params: dict | None) -> dict[str, Any]:
        """Run a Level 3, Level 4, or Level 3 -> Level 4 autonomy cycle.

        Delegates entirely to AutonomyEngine.run_cycle (Level 3) and
        AutonomyEngine.run_auto_produce_cycle (Level 4). The combined mode
        sequences the two existing engines with failure isolation, mirroring
        the scheduler's operation loop: Level 4 runs only when Level 3
        completed. No policy, gating, or production logic lives here.
        """
        params = params or {}
        mode = str(params.get("mode") or "level3").strip().lower()
        if mode not in ("level3", "level4", "level3_then_level4"):
            raise ProtocolError(
                INVALID_PARAMS,
                f"Invalid mode {mode!r}; expected level3, level4 or level3_then_level4",
            )
        channel_id = params.get("channel_id") or "default"
        dry_run = bool(params.get("dry_run", False))
        limit = self._int_param(params, "limit", default=10, minimum=1, maximum=100)
        category = params.get("category")
        policy = params.get("policy") or "local_only"

        from autopilot.core.autonomy import AutonomyEngine

        engine = AutonomyEngine(config=self.config, db=self.db)

        def _level3() -> dict[str, Any]:
            summary = engine.run_cycle(
                autonomy_level=3,
                channel_id=channel_id,
                dry_run=dry_run,
                category=category,
                limit=limit,
            )
            return summary.model_dump(mode="json")

        def _level4() -> dict[str, Any]:
            summary = engine.run_auto_produce_cycle(
                channel_id=channel_id,
                limit=limit,
                dry_run=dry_run,
                policy=policy,
            )
            return summary.model_dump(mode="json")

        try:
            if mode == "level3":
                result: dict[str, Any] = {"operation_mode": "level3", "level3": _level3(), "level4": None}
                result["status"] = result["level3"].get("status")
                result["run_id"] = result["level3"].get("run_id")
                return result
            if mode == "level4":
                result = {"operation_mode": "level4", "level3": None, "level4": _level4()}
                result["status"] = result["level4"].get("status")
                result["run_id"] = result["level4"].get("run_id")
                return result

            level3 = _level3()
            level4 = None
            skip_reason = None
            if level3.get("status") == "completed":
                level4 = _level4()
            else:
                skip_reason = (
                    f"Level 4 skipped: Level 3 finished with status "
                    f"{level3.get('status')!r} (failure isolation)"
                )
            terminal = level4 if level4 is not None else level3
            return {
                "operation_mode": "level3_then_level4",
                "level3": level3,
                "level4": level4,
                "level4_skipped_reason": skip_reason,
                "status": terminal.get("status"),
                "run_id": terminal.get("run_id"),
            }
        except ValueError as exc:
            raise ProtocolError(INVALID_PARAMS, str(exc)) from exc

    def on_autonomy_proposals(self, params: dict | None) -> dict[str, Any]:
        params = params or {}
        status = params.get("status")
        limit = self._int_param(params, "limit", default=50, minimum=1, maximum=500)
        return {"items": self.db.list_idea_proposals(status=status, limit=limit)}

    def on_autonomy_inspect(self, params: dict | None) -> dict[str, Any]:
        params = params or {}
        run_id = params.get("run_id")
        proposal_id = params.get("proposal_id")
        if not run_id and not proposal_id:
            raise ProtocolError(
                INVALID_PARAMS, "Missing required param: run_id or proposal_id"
            )
        if run_id:
            run = self.db.get_autonomy_run(run_id)
            if run is None:
                return {"found": False, "run_id": run_id}
            return {
                "found": True,
                "run_id": run_id,
                "run": run,
                "signals": self.db.get_trend_signals_for_run(run_id),
                "candidates": self.db.get_topic_candidates_for_run(run_id),
            }
        proposal = self.db.get_idea_proposal(proposal_id)
        if proposal is None:
            return {"found": False, "proposal_id": proposal_id}
        return {
            "found": True,
            "proposal_id": proposal_id,
            "proposal": proposal,
            "decision": self.db.get_decision_for_proposal(proposal_id),
        }

    def on_autonomy_proposal_approve(self, params: dict | None) -> dict[str, Any]:
        params = params or {}
        proposal_id = params.get("proposal_id")
        if not proposal_id:
            raise ProtocolError(INVALID_PARAMS, "Missing required param: proposal_id")
        from autopilot.core.autonomy import AutonomyEngine

        engine = AutonomyEngine(config=self.config, db=self.db)
        try:
            return engine.approve_proposal(str(proposal_id))
        except ValueError as exc:
            raise ProtocolError(INVALID_PARAMS, str(exc)) from exc

    def on_autonomy_proposal_reject(self, params: dict | None) -> dict[str, Any]:
        params = params or {}
        proposal_id = params.get("proposal_id")
        if not proposal_id:
            raise ProtocolError(INVALID_PARAMS, "Missing required param: proposal_id")
        from autopilot.core.autonomy import AutonomyEngine

        engine = AutonomyEngine(config=self.config, db=self.db)
        try:
            return engine.reject_proposal(
                str(proposal_id), str(params.get("reason") or "Rejected by operator")
            )
        except ValueError as exc:
            raise ProtocolError(INVALID_PARAMS, str(exc)) from exc

    # ------------------------------------------------------------------
    # M3 — scheduler control surface
    # ------------------------------------------------------------------
    def _schedule_engine(self):
        from autopilot.core.scheduler import ScheduleEngine

        return ScheduleEngine(config=self.config, db=self.db)

    def on_scheduler_status(self, params: dict | None) -> dict[str, Any]:
        from autopilot.cli.main import get_schedule_status_summary

        summary = get_schedule_status_summary(self.db)
        enabled = int(summary.get("enabled_schedules", 0))
        # The daemon loop is a separate process the desktop cannot see; the
        # derived state reflects DB truth: any enabled schedule means the
        # scheduler will fire; degraded is reported by the backend itself.
        if summary.get("status") == "DEGRADED":
            state = "degraded"
        elif enabled > 0:
            state = "running"
        else:
            state = "stopped"
        schedules = self.db.list_schedules()
        return {
            "state": state,
            "summary": summary,
            "active_executions": sum(1 for s in schedules if s.get("leased_by")),
        }

    def on_scheduler_list(self, params: dict | None) -> dict[str, Any]:
        params = params or {}
        channel_id = params.get("channel_id")
        enabled = params.get("enabled")
        if enabled is not None:
            enabled = bool(enabled)
        return {"items": self.db.list_schedules(channel_id=channel_id, enabled=enabled)}

    def on_scheduler_inspect(self, params: dict | None) -> dict[str, Any]:
        params = params or {}
        schedule_id = params.get("schedule_id")
        if not schedule_id:
            raise ProtocolError(INVALID_PARAMS, "Missing required param: schedule_id")
        limit = self._int_param(params, "limit", default=20, minimum=1, maximum=100)
        try:
            return self._schedule_engine().inspect_schedule(str(schedule_id), limit=limit)
        except ValueError as exc:
            raise ProtocolError(INVALID_PARAMS, str(exc)) from exc

    def on_scheduler_create(self, params: dict | None) -> dict[str, Any]:
        params = params or {}
        try:
            schedule = self._schedule_engine().create_schedule(
                channel_id=params.get("channel_id") or "default",
                autonomy_level=int(params.get("autonomy_level", 3)),
                operation_mode=params.get("operation_mode"),
                cadence=params.get("cadence") or "daily",
                timezone=params.get("timezone") or "UTC",
                days_of_week=params.get("days_of_week"),
                max_items_per_run=int(params.get("max_items_per_run", 10)),
                dry_run=bool(params.get("dry_run", False)),
                policy=params.get("policy") or "local_only",
                include_learning=bool(params.get("include_learning", False)),
            )
        except ValueError as exc:
            raise ProtocolError(INVALID_PARAMS, str(exc)) from exc
        return schedule.model_dump(mode="json")

    def on_scheduler_update(self, params: dict | None) -> dict[str, Any]:
        """Update mutable schedule fields with full backend validation.

        Reuses the scheduler's public validators (cadence/timezone/weekdays/
        operation-mode-vs-level) exactly as create does, then delegates to the
        existing DB allowlisted update. Never touches next_run_at directly
        except to recompute it via the backend's own next_run_after.
        """
        params = params or {}
        schedule_id = params.get("schedule_id")
        if not schedule_id:
            raise ProtocolError(INVALID_PARAMS, "Missing required param: schedule_id")

        from autopilot.core.contracts import AutonomyLevel, ScheduleCadence
        from autopilot.core.scheduler import (
            next_run_after,
            validate_days_of_week,
            validate_operation_mode,
            validate_timezone,
        )

        engine = self._schedule_engine()
        try:
            existing = engine.get_schedule(schedule_id)
        except ValueError as exc:
            raise ProtocolError(INVALID_PARAMS, str(exc)) from exc

        # Merge requested changes over the persisted values, then validate the
        # resulting state as a whole (same rules as create).
        fields: dict[str, Any] = {}
        for key in (
            "channel_id", "autonomy_level", "operation_mode", "cadence",
            "days_of_week", "timezone", "max_items_per_run", "dry_run",
            "policy", "include_learning",
        ):
            if key in params and params.get(key) is not None:
                fields[key] = params.get(key)

        merged_level = int(fields.get("autonomy_level", existing.autonomy_level))
        if merged_level not in (AutonomyLevel.LEVEL_3_AUTO_QUEUE, AutonomyLevel.LEVEL_4_AUTO_PRODUCE):
            raise ProtocolError(INVALID_PARAMS, "autonomy_level must be 3 or 4")
        merged_cadence = str(fields.get("cadence", existing.cadence.value)).lower()
        try:
            ScheduleCadence(merged_cadence)
        except ValueError as exc:
            raise ProtocolError(INVALID_PARAMS, f"Invalid cadence {merged_cadence!r}") from exc
        merged_days = fields.get("days_of_week", existing.days_of_week)
        try:
            validate_days_of_week(merged_days)
        except ValueError as exc:
            raise ProtocolError(INVALID_PARAMS, str(exc)) from exc
        merged_tz = str(fields.get("timezone", existing.timezone))
        try:
            validate_timezone(merged_tz)
        except ValueError as exc:
            raise ProtocolError(INVALID_PARAMS, str(exc)) from exc
        merged_mode = fields.get("operation_mode", existing.operation_mode)
        try:
            resolved_mode = validate_operation_mode(merged_mode, merged_level)
        except ValueError as exc:
            raise ProtocolError(INVALID_PARAMS, str(exc)) from exc
        if int(fields.get("max_items_per_run", existing.max_items_per_run)) < 1:
            raise ProtocolError(INVALID_PARAMS, "max_items_per_run must be at least 1")

        fields["autonomy_level"] = merged_level
        fields["cadence"] = merged_cadence
        fields["operation_mode"] = resolved_mode

        # Enabled transitions use the engine's own (lease-aware) methods.
        if params.get("enabled") is not None:
            if bool(params.get("enabled")):
                engine.enable_schedule(schedule_id)
            else:
                engine.disable_schedule(schedule_id)

        if fields:
            self.db.update_schedule(schedule_id, **fields)

            # Recompute next_run_at when timing-relevant fields changed so the
            # schedule reflects its new cadence/timezone/weekdays.
            if {"cadence", "days_of_week", "timezone"} & set(fields):
                updated = engine.get_schedule(schedule_id)
                if updated.enabled:
                    from datetime import datetime, timezone as _tz

                    nxt = next_run_after(updated, datetime.now(_tz.utc)).isoformat()
                    self.db.update_schedule(schedule_id, next_run_at=nxt)

        return engine.get_schedule(schedule_id).model_dump(mode="json")

    def on_scheduler_enable(self, params: dict | None) -> dict[str, Any]:
        params = params or {}
        schedule_id = params.get("schedule_id")
        if not schedule_id:
            raise ProtocolError(INVALID_PARAMS, "Missing required param: schedule_id")
        try:
            schedule = self._schedule_engine().enable_schedule(str(schedule_id))
        except ValueError as exc:
            raise ProtocolError(INVALID_PARAMS, str(exc)) from exc
        return schedule.model_dump(mode="json")

    def on_scheduler_disable(self, params: dict | None) -> dict[str, Any]:
        params = params or {}
        schedule_id = params.get("schedule_id")
        if not schedule_id:
            raise ProtocolError(INVALID_PARAMS, "Missing required param: schedule_id")
        try:
            schedule = self._schedule_engine().disable_schedule(str(schedule_id))
        except ValueError as exc:
            raise ProtocolError(INVALID_PARAMS, str(exc)) from exc
        return schedule.model_dump(mode="json")

    def on_scheduler_delete(self, params: dict | None) -> dict[str, Any]:
        params = params or {}
        schedule_id = params.get("schedule_id")
        if not schedule_id:
            raise ProtocolError(INVALID_PARAMS, "Missing required param: schedule_id")
        try:
            removed = self._schedule_engine().delete_schedule(str(schedule_id))
        except ValueError as exc:
            raise ProtocolError(INVALID_PARAMS, str(exc)) from exc
        return {"deleted": removed, "schedule_id": schedule_id}

    def on_scheduler_run_now(self, params: dict | None) -> dict[str, Any]:
        """Execute a single schedule immediately via the existing scheduler."""
        params = params or {}
        schedule_id = params.get("schedule_id")
        if not schedule_id:
            raise ProtocolError(INVALID_PARAMS, "Missing required param: schedule_id")
        try:
            result = self._schedule_engine().run_now(str(schedule_id))
        except ValueError as exc:
            raise ProtocolError(INVALID_PARAMS, str(exc)) from exc
        return result.model_dump(mode="json")

    def on_scheduler_run_due(self, params: dict | None) -> dict[str, Any]:
        """Execute every due schedule once (bounded catch-up)."""
        params = params or {}
        limit = self._int_param(params, "limit", default=10, minimum=1, maximum=50)
        summaries = self._schedule_engine().run_due(max_runs=limit)
        return {
            "executed": len(summaries),
            "summaries": [s.model_dump(mode="json") for s in summaries],
        }

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _schema_version(self) -> Optional[int]:
        try:
            with sqlite3.connect(self.config.db_path) as conn:
                row = conn.execute("SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1").fetchone()
                return int(row[0]) if row else None
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _int_param(params: dict, name: str, default: int, minimum: int, maximum: int) -> int:
        raw = params.get(name, default)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))