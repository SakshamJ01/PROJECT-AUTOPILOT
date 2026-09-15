"""BATCH MANIFEST PROCESSOR — Milestone 7.
Parses, validates, and enqueues batch production manifests with deterministic deduplication.
Supports JSON and YAML manifest definitions without requiring external cloud services.
"""
from __future__ import annotations
import json
import uuid
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List

from autopilot.core.config import CONFIG, Config
from autopilot.core.contracts import (
    BatchManifest, BatchItem, BatchSubmitResult, QueuePriority,
)
from autopilot.db.manager import DBManager


class BatchProcessor:
    """Processes batch manifests and enqueues production jobs atomically."""

    def __init__(self, config: Config | None = None, db: DBManager | None = None):
        if isinstance(config, DBManager):
            db = config
            config = None
        self.config = config or CONFIG
        self.db = db or DBManager(self.config.db_path)
        self.db.init_schema()

    def parse_manifest_file(self, file_path: str | Path, channel_id: Optional[str] = None) -> BatchManifest:
        """Parses a plain-text list of topics (.txt), JSON, or YAML manifest file."""
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(f"Batch manifest file not found: {p}")

        raw_text = p.read_text(encoding="utf-8").strip()

        # Check for plain text list (.txt or non-json/yaml)
        if p.suffix.lower() == ".txt" or ("\n" in raw_text and not raw_text.startswith("{") and not raw_text.startswith("[") and ":" not in raw_text.splitlines()[0]):
            topics = [line.strip() for line in raw_text.splitlines() if line.strip() and not line.strip().startswith("#")]
            if not topics:
                raise ValueError(f"No valid topics found in text file: {p}")
            items = [BatchItem(topic=t, channel_id=channel_id or "default") for t in topics]
            return BatchManifest(
                manifest_id=f"mf-txt-{uuid.uuid4().hex[:8]}",
                name=p.stem,
                channel_id=channel_id or "default",
                items=items,
            )

        data = None

        # 1. Try standard JSON
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            # 2. Try simple YAML parsing
            try:
                import yaml
                data = yaml.safe_load(raw_text)
            except ImportError:
                raise ValueError(f"Could not parse manifest as JSON: {p}. (Install pyyaml for advanced YAML syntax)")

        if not isinstance(data, dict):
            raise ValueError(f"Invalid manifest format: top-level object must be a dictionary in {p}")

        if channel_id and "channel_id" not in data:
            data["channel_id"] = channel_id

        try:
            return BatchManifest.model_validate(data)
        except Exception as exc:
            raise ValueError(f"Invalid batch manifest schema: {exc}")

    def execute_batch(
        self,
        manifest: BatchManifest,
        orchestrator: Optional[Any] = None,
        production_engine: Optional[str] = None,
        policy: str = "local_only",
        max_regeneration_attempts: int = 3,
        **pipeline_kwargs,
    ) -> Dict[str, Any]:
        """Executes a batch manifest with complete job isolation.
        
        Failure of any individual job is captured, categorized, and does not stop
        the processing of subsequent jobs in the batch.
        """
        from autopilot.core.pipeline import PipelineOrchestrator, PipelineError
        orch = orchestrator or PipelineOrchestrator(config=self.config, db=self.db)
        
        manifest_id = manifest.manifest_id or f"mf-exec-{uuid.uuid4().hex[:8]}"
        total = len(manifest.items)
        succeeded = 0
        failed = 0
        needs_review = 0
        job_results: List[Dict[str, Any]] = []

        for idx, item in enumerate(manifest.items):
            topic = item.topic.strip()
            item_channel = item.channel_id or manifest.channel_id or "default"
            item_profile = item.profile or manifest.profile or "short_vertical"
            idemp_raw = f"{item_channel}:{topic}:{item_profile}"
            idemp_hash = hashlib.sha256(idemp_raw.encode("utf-8")).hexdigest()[:12]
            job_id = f"batch-job-{idemp_hash}"

            job_summary: Dict[str, Any] = {
                "job_id": job_id,
                "topic": topic,
                "channel_id": item_channel,
                "status": "pending",
                "attempt_count": 1,
                "error": None,
                "error_category": None,
                "artifacts": {},
            }

            try:
                call_kwargs = dict(pipeline_kwargs)
                call_kwargs.pop("channel_id", None)
                call_kwargs.pop("production_engine", None)
                call_kwargs.pop("policy", None)
                call_kwargs.pop("profile", None)
                res = orch.run_pipeline(
                    job_id=job_id,
                    topic=topic,
                    profile=item_profile,
                    channel_id=item_channel,
                    production_engine=production_engine,
                    auto_publish=manifest.auto_publish,
                    publish_visibility=manifest.publish_visibility,
                    max_regeneration_attempts=max_regeneration_attempts,
                    policy=policy,
                    **call_kwargs,
                )
                job_summary["status"] = "succeeded"
                job_summary["media_path"] = res.get("media_path")
                job_summary["media_checksum"] = res.get("media_checksum")
                job_summary["qa_status"] = res.get("qa_status")
                succeeded += 1
            except PipelineError as p_err:
                job_summary["error"] = p_err.message
                job_summary["error_category"] = p_err.category
                if "NEEDS_REVIEW" in p_err.message or p_err.category == "BLOCKED":
                    job_summary["status"] = "needs_review"
                    needs_review += 1
                else:
                    job_summary["status"] = "failed"
                    failed += 1
            except Exception as exc:
                job_summary["status"] = "failed"
                job_summary["error"] = str(exc)
                job_summary["error_category"] = "NON_RETRYABLE"
                failed += 1

            job_results.append(job_summary)

        return {
            "manifest_id": manifest_id,
            "total_jobs": total,
            "succeeded_jobs": succeeded,
            "failed_jobs": failed,
            "needs_review_jobs": needs_review,
            "job_results": job_results,
        }

    def submit_manifest(
        self,
        manifest: BatchManifest,
        dry_run: bool = False,
        force: bool = False,
    ) -> BatchSubmitResult:
        """Validates and enqueues jobs for all items in the manifest.
        
        Guarantees deterministic duplicate prevention unless force=True.
        """
        manifest_id = manifest.manifest_id or f"mf-{uuid.uuid4().hex[:8]}"
        total_items = len(manifest.items)
        submitted_count = 0
        skipped_count = 0
        queued_ids: List[str] = []
        errors: List[str] = []

        priority_map = {
            "low": QueuePriority.LOW.value,
            "normal": QueuePriority.NORMAL.value,
            "high": QueuePriority.HIGH.value,
        }
        manifest_default_prio = priority_map.get(str(manifest.priority).lower(), QueuePriority.NORMAL.value)

        # Pre-validate all items before database modification
        for idx, item in enumerate(manifest.items):
            if not item.topic or not item.topic.strip():
                errors.append(f"Item #{idx + 1} has an empty topic")
            if item.scheduled_at:
                try:
                    # Validate ISO timestamp
                    datetime.fromisoformat(item.scheduled_at.replace("Z", "+00:00"))
                except Exception:
                    errors.append(f"Item #{idx + 1} ('{item.topic}'): invalid ISO scheduled_at timestamp: '{item.scheduled_at}'")

        if errors:
            return BatchSubmitResult(
                manifest_id=manifest_id,
                total_items=total_items,
                submitted_count=0,
                skipped_duplicate_count=0,
                queued_ids=[],
                errors=errors,
            )

        if dry_run:
            # Return validation preview without writing to database
            return BatchSubmitResult(
                manifest_id=manifest_id,
                total_items=total_items,
                submitted_count=total_items,
                skipped_duplicate_count=0,
                queued_ids=[f"dry-run-q-{idx}" for idx in range(total_items)],
                errors=[],
            )

        # Record manifest record in DB
        self.db.record_batch_manifest(
            manifest_id=manifest_id,
            name=manifest.name or "batch_production",
            profile=manifest.profile,
            total_items=total_items,
            status="submitted",
            raw_json=manifest.model_dump_json(),
        )

        for idx, item in enumerate(manifest.items):
            topic = item.topic.strip()
            item_profile = item.profile or manifest.profile
            prio = priority_map.get(str(item.priority).lower(), manifest_default_prio)
            channel_id = item.channel_id or getattr(manifest, "channel_id", "default") or "default"

            # Deterministic idempotency identity per channel
            idemp_raw = f"{channel_id}:{topic}:{item_profile}"
            idemp_hash = hashlib.sha256(idemp_raw.encode("utf-8")).hexdigest()[:16]
            job_id = f"job-{idemp_hash}"
            queue_id = f"q-{idemp_hash}-{uuid.uuid4().hex[:6]}"

            # Check if existing job/queue item exists
            existing_queue_item = self.db.get_queue_item_by_job(job_id)
            if existing_queue_item and not force:
                cur_status = existing_queue_item.get("status")
                if cur_status in ("queued", "running", "succeeded", "retry_wait"):
                    skipped_count += 1
                    continue

            # Item payload passed into pipeline orchestrator
            payload = {
                "topic": topic,
                "channel_id": channel_id,
                "profile": item_profile,
                "auto_publish": manifest.auto_publish,
                "publish_visibility": manifest.publish_visibility,
                "manifest_id": manifest_id,
            }
            if item.payload:
                payload.update(item.payload)

            self.db.enqueue_item(
                queue_id=queue_id,
                job_id=job_id,
                content_id=job_id,
                channel_id=channel_id,
                priority=prio,
                stage="RESEARCH",
                scheduled_at=item.scheduled_at,
                max_attempts=self.config.queue_max_attempts,
                manifest_id=manifest_id,
                payload=payload,
            )
            queued_ids.append(queue_id)
            submitted_count += 1

        return BatchSubmitResult(
            manifest_id=manifest_id,
            total_items=total_items,
            submitted_count=submitted_count,
            skipped_duplicate_count=skipped_count,
            queued_ids=queued_ids,
            errors=[],
        )
