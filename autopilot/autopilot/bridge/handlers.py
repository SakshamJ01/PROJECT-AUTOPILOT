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