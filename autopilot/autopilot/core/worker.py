"""LOCAL WORKER ENGINE — Milestone 7.
Bounded local worker process polling the SQLite queue, claiming jobs atomically,
executing pipeline stages, recovering stale leases, and handling retries with exponential backoff.
"""
from __future__ import annotations
import os
import time
import uuid
import json
from typing import Optional, Dict, Any

from autopilot.core.config import CONFIG, Config
from autopilot.core.contracts import (
    QueueItem, QueueItemStatus, QueuePriority, QueueStage,
)
from autopilot.core.logging import StructuredLogger
from autopilot.db.manager import DBManager
from autopilot.core.pipeline import PipelineOrchestrator, PipelineError

def run_pipeline(**kwargs) -> Dict[str, Any]:
    """Module-level pipeline execution wrapper for worker execution and testing."""
    orch = kwargs.pop("orchestrator", None) or PipelineOrchestrator()
    return orch.run_pipeline(**kwargs)


class LocalWorker:
    """Bounded local worker process executing queue items."""

    def __init__(
        self,
        worker_id: str | None = None,
        config: Config | None = None,
        db: DBManager | None = None,
        orchestrator: PipelineOrchestrator | None = None,
    ):
        self.config = config or CONFIG
        self.db = db or DBManager(self.config.db_path)
        self.db.init_schema()
        self.worker_id = worker_id or f"worker-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.orchestrator = orchestrator or PipelineOrchestrator(config=self.config, db=self.db)
        self.logger = StructuredLogger(job_id=self.worker_id, stage="worker")

    def recover_stale_leases(self, grace_lease_sec: float | None = None) -> list[str]:
        """Recovers any jobs whose worker lease has expired.

        By default passes ``grace_lease_sec`` equal to the configured lease
        duration so that recovered items are not immediately claimable —
        preventing duplicate execution if the original worker is alive but in
        a long stage.  Pass ``grace_lease_sec=0`` to restore the original
        immediate-claim behaviour (useful in tests).
        """
        if grace_lease_sec is None:
            grace_lease_sec = self.config.queue_lease_duration_seconds
        recovered = self.db.recover_stale_leases(grace_lease_sec=grace_lease_sec)
        if recovered:
            self.logger.info("stale_leases_recovered", details={"count": len(recovered), "queue_ids": recovered})
        return recovered

    def process_next_job(self) -> Optional[Dict[str, Any]]:
        """Claims and processes a single job from the queue.
        
        Returns result summary or None if no eligible job was available.
        """
        # 1. Stale lease recovery
        self.recover_stale_leases()

        # 2. Claim next available job
        item_dict = self.db.claim_next_queue_item(
            worker_id=self.worker_id,
            lease_duration_sec=self.config.queue_lease_duration_seconds,
        )
        if not item_dict:
            return None

        return self.process_claimed_item(item_dict)

    def process_claimed_item(
        self,
        item_dict: Dict[str, Any],
        provider_overrides: Optional[Dict[str, str]] = None,
        force_auto_publish: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Processes an already-claimed queue item through the pipeline.

        ``process_next_job`` delegates here after its atomic claim.  The Level 4
        guarded auto-produce cycle also calls this method directly after an
        atomic ``claim_queue_item`` so pre-validated autonomous jobs reuse the
        exact same production execution path.

        ``provider_overrides`` lets an orchestrator pin deterministic/test
        providers without mutating the persisted payload (test isolation).
        ``force_auto_publish`` overrides publish intent (Level 4 always passes
        ``False`` to preserve the public-publishing boundary).
        """
        profile = item_dict.get("profile")
        job_id = item_dict["job_id"]
        payload_raw = item_dict.get("payload_json") or "{}"
        try:
            payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
        except Exception:
            payload = {}

        topic = payload.get("topic") or item_dict.get("topic") or f"Topic for {job_id}"
        profile = profile or payload.get("profile", "short_vertical")
        channel_id = item_dict.get("channel_id") or payload.get("channel_id", "default")
        auto_publish = payload.get("auto_publish", False) or payload.get("publish", False)
        publish_visibility = payload.get("publish_visibility", "private")
        publish_platform = payload.get("publish_platform", "youtube")
        tts_provider = payload.get("tts_provider", "mock")
        asset_provider = payload.get("asset_provider", "local")
        llm_provider = payload.get("llm_provider", "mock")
        research_provider = payload.get("research_provider", "mock_search")
        production_engine = payload.get("production_engine") or self.config.default_production_engine

        if provider_overrides:
            if provider_overrides.get("policy") is not None:
                payload["policy"] = provider_overrides["policy"]
            for key, val in (provider_overrides or {}).items():
                if key == "llm":
                    llm_provider = val
                elif key == "research":
                    research_provider = val
                elif key == "tts":
                    tts_provider = val
                elif key == "asset":
                    asset_provider = val
                elif key == "production_engine":
                    production_engine = val
        if force_auto_publish is not None:
            auto_publish = force_auto_publish

        # Apply policy-based provider resolution to prevent silent mock usage.
        # The CLI path runs resolve_providers_for_policy; the queue/worker path must too.
        policy = payload.get("policy", "local_only")
        if policy not in ("mock",):
            from autopilot.cli.main import resolve_providers_for_policy
            try:
                llm_provider, research_provider, tts_provider, production_engine = resolve_providers_for_policy(
                    policy=policy,
                    llm_provider=llm_provider,
                    research_provider=research_provider,
                    tts_provider=tts_provider,
                    production_engine=production_engine,
                )
            except ValueError:
                # Policy forbids mock but no explicit real provider was given;
                # fall through and let the pipeline fail loudly.
                pass

        # Check channel status & config if channel profile exists
        from autopilot.core.channel import ChannelManager
        from autopilot.core.contracts import ChannelStatus
        cm = ChannelManager(self.db)
        channel = cm.get_channel(channel_id)
        if channel:
            if channel.status == ChannelStatus.DISABLED:
                self.db.block_queue_item(item_dict["queue_id"], f"Channel '{channel_id}' is disabled.")
                return {"queue_id": item_dict["queue_id"], "job_id": job_id, "status": "blocked", "error": f"Channel '{channel_id}' is disabled."}
            if channel.voice:
                v_prov = getattr(channel.voice, "provider", None) or (channel.voice.get("provider") if isinstance(channel.voice, dict) else None)
                if v_prov and v_prov != "mock":
                    tts_provider = v_prov
            if channel.target_platforms:
                publish_platform = channel.target_platforms[0]
            if publish_platform.lower() not in ("youtube",):
                err_msg = f"Unsupported publishing platform target '{publish_platform}'."
                self.db.fail_queue_item(queue_id=item_dict["queue_id"], error_message=err_msg, retryable=False)
                return {"queue_id": item_dict["queue_id"], "job_id": job_id, "status": "failed", "error": err_msg}

        queue_id = item_dict["queue_id"]
        self.logger.info("job_claimed", details={
            "queue_id": queue_id,
            "job_id": job_id,
            "channel_id": channel_id,
            "topic": topic,
            "attempt": item_dict.get("attempt_count", 1),
        })

        # 3. Stage progress callback with lease renewal
        def on_stage_progress(stage: str):
            self.db.update_queue_stage(queue_id=queue_id, stage=stage, status="running")
            self.db.renew_lease(
                queue_id=queue_id,
                worker_id=self.worker_id,
                lease_duration_sec=self.config.queue_lease_duration_seconds,
            )

        # 4. Execute Pipeline
        try:
            res = run_pipeline(
                orchestrator=self.orchestrator,
                job_id=job_id,
                topic=topic,
                profile=profile,
                priority=item_dict.get("priority", 2),
                auto_publish=auto_publish,
                publish_visibility=publish_visibility,
                publish_platform=publish_platform,
                tts_provider=tts_provider,
                asset_provider=asset_provider,
                on_stage_progress=on_stage_progress,
                channel_id=channel_id,
                llm_provider=llm_provider,
                research_provider=research_provider,
                production_engine=production_engine,
            )
            # Mark Succeeded
            self.db.complete_queue_item(queue_id)
            self.logger.info("job_completed", details={"queue_id": queue_id, "job_id": job_id})
            return {
                "queue_id": queue_id,
                "job_id": job_id,
                "status": "succeeded",
                "media_path": res.get("media_path"),
                "qa_status": res.get("qa_status"),
                "published": res.get("published"),
            }

        except PipelineError as p_err:
            self.logger.error("pipeline_error", error=p_err.message, details={
                "queue_id": queue_id,
                "job_id": job_id,
                "category": p_err.category,
                "stage": p_err.stage,
            })
            if p_err.category == "BLOCKED":
                self.db.block_queue_item(queue_id, p_err.message)
                return {"queue_id": queue_id, "job_id": job_id, "status": "blocked", "error": p_err.message}
            elif p_err.category == "RETRYABLE":
                outcome = self.db.fail_queue_item(
                    queue_id=queue_id,
                    error_message=p_err.message,
                    retryable=True,
                    backoff_base_sec=self.config.queue_retry_backoff_base_seconds,
                )
                return {"queue_id": queue_id, "job_id": job_id, "status": outcome, "error": p_err.message}
            else:
                outcome = self.db.fail_queue_item(
                    queue_id=queue_id,
                    error_message=p_err.message,
                    retryable=False,
                )
                return {"queue_id": queue_id, "job_id": job_id, "status": outcome, "error": p_err.message}

        except Exception as exc:
            self.logger.error("unexpected_worker_error", error=str(exc), details={"queue_id": queue_id, "job_id": job_id})
            outcome = self.db.fail_queue_item(
                queue_id=queue_id,
                error_message=str(exc),
                retryable=True,
                backoff_base_sec=self.config.queue_retry_backoff_base_seconds,
            )
            return {"queue_id": queue_id, "job_id": job_id, "status": outcome, "error": str(exc)}

    def run(
        self,
        max_jobs: Optional[int] = None,
        poll_interval: Optional[float] = None,
        once: bool = False,
    ) -> int:
        """Runs the worker loop.
        
        If `once` is True, processes all currently eligible jobs and then exits.
        Returns total number of processed jobs.
        """
        poll_sec = poll_interval if poll_interval is not None else self.config.queue_poll_interval_seconds
        processed_count = 0

        self.logger.info("worker_started", details={
            "worker_id": self.worker_id,
            "once": once,
            "max_jobs": max_jobs,
        })

        while True:
            res = self.process_next_job()
            if res is not None:
                processed_count += 1
                if max_jobs and processed_count >= max_jobs:
                    break
            else:
                if once:
                    # In once mode, when no claimable work is currently ready, exit cleanly
                    break
                time.sleep(poll_sec)

        self.logger.info("worker_stopped", details={
            "worker_id": self.worker_id,
            "jobs_processed": processed_count,
        })
        return processed_count

    def run_once(self) -> int:
        """Processes a single job if available. Returns 1 if processed, 0 if queue empty."""
        res = self.process_next_job()
        return 1 if res is not None else 0

    def run_all(self, max_jobs: Optional[int] = None) -> int:
        """Processes all ready jobs in the queue until empty, up to max_jobs."""
        return self.run(max_jobs=max_jobs, once=True)


# Alias for backward compatibility
Worker = LocalWorker
