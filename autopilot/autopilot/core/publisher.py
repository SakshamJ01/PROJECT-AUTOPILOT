"""Publishing Engine & Quality Gate Subsystem — Milestone 6.
Provider-neutral publishing orchestrator that enforces:
1. Strict QA Gate verification (publish_allowed invariant).
2. Media artifact SHA-256 checksum validation.
3. Deterministic publication idempotency.
4. Provider dispatch (YouTube / Mock).
5. State machine transitions (APPROVED -> PUBLISHING -> PUBLISHED / FAILED_PUBLISH).
6. SQLite audit logging and artifact exports.
"""
from __future__ import annotations
import json
import time
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from autopilot.core.config import Config, CONFIG
from autopilot.core.logging import StructuredLogger
from autopilot.core.artifacts import job_artifact_dir, publish_path
from autopilot.core.state_machine import WorkflowState
from autopilot.db.manager import DBManager
from autopilot.providers.contracts import REGISTRY, PublisherProvider
from autopilot.core.auto_publish import effective_auto_publish
from autopilot.core.contracts import (
    PublishRequest, PublishResult, PublishStatus,
    PublicationReceipt, PublishAttempt, PublishError,
    PublishVisibility, PublishPlatform, QAStatus,
    PublishReceipt
)
from autopilot.providers.youtube_publisher import YouTubePublisher, redact_secrets


def compute_file_sha256(path: str | Path) -> str:
    """Compute SHA-256 checksum of local file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def compute_publish_idempotency_key(
    content_id: str,
    media_checksum: str,
    platform: str,
    visibility: str,
    scheduled_time: Optional[str],
    metadata_hash: str,
) -> str:
    """Compute deterministic publication identity key."""
    raw = f"{content_id}:{media_checksum}:{platform}:{visibility}:{scheduled_time or ''}:{metadata_hash}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class PublishingEngine:
    def __init__(self, config: Optional[Config] = None, db: Optional[DBManager] = None):
        self.config = config or CONFIG
        self.db = db or DBManager(self.config.db_path)

    @staticmethod
    def compute_idempotency_key(
        job_id: str,
        render_checksum: str,
        platform: str,
        visibility: str,
        scheduled_time: Optional[str] = None,
        metadata_hash: str = "",
    ) -> str:
        """Compute deterministic publication identity key."""
        return compute_publish_idempotency_key(
            content_id=job_id,
            media_checksum=render_checksum,
            platform=platform,
            visibility=visibility,
            scheduled_time=scheduled_time,
            metadata_hash=metadata_hash,
        )

    def _resolve_media_file(self, job_id: str, explicit_path: Optional[str] = None) -> Optional[Path]:
        """Locate rendered media file for job."""
        if explicit_path:
            p = Path(explicit_path)
            if p.exists() and p.is_file():
                return p

        base_dir = self.config.get_artifacts_dir()
        art_dir = job_artifact_dir(job_id, base_dir=base_dir)
        candidates = [
            art_dir / "render" / "final.mp4",
            art_dir / "render" / "output.mp4",
            art_dir / "media" / "final.mp4",
        ]
        for c in candidates:
            if c.exists() and c.is_file() and c.stat().st_size > 0:
                return c

        return None

    def _load_qa_receipt(self, job_id: str, db: DBManager) -> Optional[Dict[str, Any]]:
        """Load QA receipt from filesystem artifact or SQLite database."""
        base_dir = self.config.get_artifacts_dir()
        # 1. Try quality/receipt.json
        receipt_file = job_artifact_dir(job_id, base_dir=base_dir) / "quality" / "receipt.json"
        if receipt_file.exists():
            try:
                return json.loads(receipt_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        # 2. Try DB qa_runs
        try:
            reports = db.get_qa_reports_for_job(job_id)
            if reports:
                latest = reports[0]
                rcpt_str = latest.get("receipt_json")
                if rcpt_str:
                    try:
                        parsed = json.loads(rcpt_str)
                        if parsed and isinstance(parsed, dict) and (parsed.get("status") or parsed.get("publish_allowed") is not None):
                            return parsed
                    except Exception:
                        pass
                return {
                    "status": latest.get("status"),
                    "publish_allowed": bool(latest.get("publish_allowed")),
                    "media_checksum_sha256": None,
                }
        except Exception:
            pass

        return None

    def export_publish_artifacts(self, result: PublishResult, request: PublishRequest, job_id: str) -> Dict[str, str]:
        """Write publication request, receipt, and result JSON artifacts to disk."""
        base_dir = self.config.get_artifacts_dir()
        pub_dir = job_artifact_dir(job_id, base_dir=base_dir) / "publish"
        pub_dir.mkdir(parents=True, exist_ok=True)

        req_path = pub_dir / "request.json"
        req_path.write_text(request.model_dump_json(indent=2), encoding="utf-8")

        res_path = pub_dir / "result.json"
        res_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")

        exported = {
            "request": str(req_path.resolve()),
            "result": str(res_path.resolve()),
        }

        if result.receipt:
            rcpt_path = pub_dir / "receipt.json"
            rcpt_path.write_text(result.receipt.model_dump_json(indent=2), encoding="utf-8")
            exported["receipt"] = str(rcpt_path.resolve())

        return exported

    def _enforce_approval_gate(
        self,
        job_id: str,
        db: DBManager,
        media_checksum: str,
        platform: str,
    ) -> Optional[PublishError]:
        """Enforce the operator approval gate before a real publication.

        The gate requires (a) job readiness (READY_TO_PUBLISH == APPROVED),
        (b) an explicitly approved approval record, (c) approval channel match,
        (d) approval platform match, and (e) that the published artifact is
        byte-identical to the artifact the operator approved. Failures never
        flip the job to FAILED_PUBLISH — the job remains READY_TO_PUBLISH.
        """
        job = db.get_job(job_id)
        if not job:
            return PublishError(
                error_code="JOB_NOT_FOUND",
                message=f"Job '{job_id}' not found.",
                retryable=False,
            )

        job_status = (job.get("status") or "").upper()
        job_channel = (job.get("channel_id") or "default").lower()
        ready_states = {WorkflowState.APPROVED.value.upper(), WorkflowState.READY.value.upper()}
        if job_status not in ready_states:
            return PublishError(
                error_code="JOB_NOT_READY",
                message=(
                    f"Job '{job_id}' is in state '{job.get('status')}'; explicit operator "
                    f"approval requires READY_TO_PUBLISH (APPROVED)."
                ),
                retryable=False,
            )

        approval = db.get_publish_approval(job_id)
        if approval is None:
            return PublishError(
                error_code="APPROVAL_REQUIRED",
                message=f"No approval record for job '{job_id}'. Explicit operator approval is required before publishing.",
                retryable=False,
            )

        if approval.get("status") != "approved":
            err_code = "APPROVAL_REJECTED" if approval.get("status") == "rejected" else "APPROVAL_PENDING"
            return PublishError(
                error_code=err_code,
                message=(
                    f"Publication for job '{job_id}' is {approval.get('status')}. "
                    f"Explicit operator approval is required before publishing."
                ),
                retryable=False,
            )

        approval_channel = (approval.get("channel_id") or "default").lower()
        if approval_channel != job_channel:
            return PublishError(
                error_code="APPROVAL_CHANNEL_MISMATCH",
                message=(
                    f"Approval channel '{approval_channel}' does not match job channel "
                    f"'{job_channel}' for job '{job_id}'."
                ),
                retryable=False,
            )

        approval_platform = (approval.get("platform") or "").lower()
        if approval_platform and approval_platform != str(platform).lower():
            return PublishError(
                error_code="APPROVAL_PLATFORM_MISMATCH",
                message=(
                    f"Approval platform '{approval_platform}' does not match requested "
                    f"platform '{platform}' for job '{job_id}'."
                ),
                retryable=False,
            )

        bound_checksum = approval.get("media_checksum_sha256")
        if bound_checksum and bound_checksum != media_checksum:
            self.db.decide_publish_approval(
                job_id,
                approved=False,
                decided_by="system",
                notes=(
                    f"Approval invalidated: artifact changed after approval "
                    f"({bound_checksum[:12]}... != {media_checksum[:12]}...)."
                ),
            )
            return PublishError(
                error_code="APPROVAL_ARTIFACT_MISMATCH",
                message=(
                    f"Approved artifact for job '{job_id}' no longer matches the current "
                    f"media file (SHA-256 changed post-approval). The approval was "
                    f"invalidated; re-approve the new artifact."
                ),
                retryable=False,
            )
        # Idempotent audit binding: COALESCE fills only unbound values, so this is
        # a no-op when the operator already pinned the checksum/platform.
        db.bind_publish_approval(job_id, media_checksum, str(platform).lower())
        return None

    def evaluate_job_publishability(
        self,
        job_id: str,
        platform: str = "youtube",
    ) -> Dict[str, Any]:
        """Authoritatively evaluate whether a job satisfies all publication gates.

        Reuses the exact publisher gate rules in a read-only manner without
        mutating the database or executing a publication.
        """
        job = self.db.get_job(job_id)
        if not job:
            return {
                "publishable": False,
                "reason": f"Job '{job_id}' not found.",
                "job": None,
                "media_path": None,
                "media_checksum": None,
                "qa_receipt": None,
                "approval": None,
                "published": False,
            }

        job_status = (job.get("status") or "").upper()
        ready_states = {WorkflowState.APPROVED.value.upper(), WorkflowState.READY.value.upper()}
        if job_status not in ready_states:
            return {
                "publishable": False,
                "reason": f"Job '{job_id}' is in state '{job.get('status')}'; explicit operator approval requires READY_TO_PUBLISH (APPROVED).",
                "job": job,
                "media_path": None,
                "media_checksum": None,
                "qa_receipt": None,
                "approval": None,
                "published": False,
            }

        publications = self.db.get_publications_for_job(job_id)
        is_published = any(
            str(p.get("status")).upper() in ("SUCCESS", "PUBLISHED")
            for p in publications
        ) or job_status == WorkflowState.PUBLISHED.value.upper()
        if is_published:
            return {
                "publishable": False,
                "reason": f"Job '{job_id}' has already been published.",
                "job": job,
                "media_path": None,
                "media_checksum": None,
                "qa_receipt": None,
                "approval": None,
                "published": True,
            }

        media = self._resolve_media_file(job_id)
        if not media or not media.exists() or not media.is_file() or media.stat().st_size == 0:
            return {
                "publishable": False,
                "reason": f"No rendered media file found for job '{job_id}'.",
                "job": job,
                "media_path": str(media) if media else None,
                "media_checksum": None,
                "qa_receipt": None,
                "approval": None,
                "published": False,
            }

        media_checksum = compute_file_sha256(media)

        qa_receipt = self._load_qa_receipt(job_id, self.db)
        if not qa_receipt:
            return {
                "publishable": False,
                "reason": f"No QA receipt found for job '{job_id}'. All videos must pass QA before publishing.",
                "job": job,
                "media_path": str(media),
                "media_checksum": media_checksum,
                "qa_receipt": None,
                "approval": None,
                "published": False,
            }

        qa_status = str(qa_receipt.get("status") or "").upper()
        qa_publish_allowed = bool(qa_receipt.get("publish_allowed", False))
        if qa_status in (QAStatus.BLOCK.value.upper(), "FAIL") or not qa_publish_allowed:
            return {
                "publishable": False,
                "reason": f"Publishing blocked by QA Gate. QA Status: {qa_status}, Publish Allowed: {qa_publish_allowed}.",
                "job": job,
                "media_path": str(media),
                "media_checksum": media_checksum,
                "qa_receipt": qa_receipt,
                "approval": None,
                "published": False,
            }

        qa_checksum = qa_receipt.get("media_checksum_sha256")
        if qa_checksum and media_checksum != qa_checksum:
            return {
                "publishable": False,
                "reason": (
                    f"Media SHA-256 on disk ({media_checksum[:12]}...) does not match "
                    f"QA receipt checksum ({qa_checksum[:12]}...). Video may have been modified post-QA."
                ),
                "job": job,
                "media_path": str(media),
                "media_checksum": media_checksum,
                "qa_receipt": qa_receipt,
                "approval": None,
                "published": False,
            }

        approval = self.db.get_publish_approval(job_id)
        if approval is None:
            return {
                "publishable": False,
                "reason": f"No approval record for job '{job_id}'. Explicit operator approval is required before publishing.",
                "job": job,
                "media_path": str(media),
                "media_checksum": media_checksum,
                "qa_receipt": qa_receipt,
                "approval": None,
                "published": False,
            }

        if approval.get("status") != "approved":
            return {
                "publishable": False,
                "reason": (
                    f"Publication for job '{job_id}' is {approval.get('status')}. "
                    f"Explicit operator approval is required before publishing."
                ),
                "job": job,
                "media_path": str(media),
                "media_checksum": media_checksum,
                "qa_receipt": qa_receipt,
                "approval": approval,
                "published": False,
            }

        job_channel = (job.get("channel_id") or "default").lower()
        approval_channel = (approval.get("channel_id") or "default").lower()
        if approval_channel != job_channel:
            return {
                "publishable": False,
                "reason": f"Approval channel '{approval_channel}' does not match job channel '{job_channel}' for job '{job_id}'.",
                "job": job,
                "media_path": str(media),
                "media_checksum": media_checksum,
                "qa_receipt": qa_receipt,
                "approval": approval,
                "published": False,
            }

        approval_platform = (approval.get("platform") or "").lower()
        if approval_platform and approval_platform != str(platform).lower():
            return {
                "publishable": False,
                "reason": f"Approval platform '{approval_platform}' does not match requested platform '{platform}' for job '{job_id}'.",
                "job": job,
                "media_path": str(media),
                "media_checksum": media_checksum,
                "qa_receipt": qa_receipt,
                "approval": approval,
                "published": False,
            }

        bound_checksum = approval.get("media_checksum_sha256")
        if bound_checksum and bound_checksum != media_checksum:
            return {
                "publishable": False,
                "reason": (
                    f"Approved artifact for job '{job_id}' no longer matches current media file "
                    f"(SHA-256 changed post-approval: {bound_checksum[:12]}... != {media_checksum[:12]}...). "
                    f"The approval was invalidated; re-approve the new artifact."
                ),
                "job": job,
                "media_path": str(media),
                "media_checksum": media_checksum,
                "qa_receipt": qa_receipt,
                "approval": approval,
                "published": False,
            }

        return {
            "publishable": True,
            "reason": None,
            "job": job,
            "media_path": str(media),
            "media_checksum": media_checksum,
            "qa_receipt": qa_receipt,
            "approval": approval,
            "published": False,
        }

    def is_job_publishable(self, job_id: str, platform: str = "youtube") -> bool:
        """Return True if the job satisfies all publication gates and can be published."""
        return bool(self.evaluate_job_publishability(job_id, platform=platform)["publishable"])

    def count_publishable_jobs(self, channel_id: Optional[str] = None, platform: str = "youtube") -> int:
        """Count jobs that satisfy all publication gates and are genuinely publishable."""
        candidate_jobs = self.db.list_jobs_by_status("APPROVED", limit=1000)
        if channel_id:
            candidate_jobs = [j for j in candidate_jobs if j.get("channel_id") == channel_id]
        count = 0
        for job in candidate_jobs:
            if self.is_job_publishable(job["job_id"], platform=platform):
                count += 1
        return count


    def publish_job(
        self,
        job_id: str,
        platform: str = "youtube",
        visibility: Optional[str] = None,
        scheduled_time: Optional[str] = None,
        dry_run: Optional[bool] = None,
        force_retry: bool = False,
        media_path: Optional[str] = None,
        provider: Optional[PublisherProvider] = None,
        db_manager: Optional[DBManager] = None,
        require_approval: bool = False,
        autonomous: bool = False,
    ) -> PublishResult:
        """Publish a QA-verified rendered job to the target platform."""
        db = db_manager or self.db
        db.init_schema()

        # Ensure job exists in DB
        if not db.get_job(job_id):
            db.create_job(job_id=job_id, topic=job_id)

        # 1. Locate media file
        target_media = self._resolve_media_file(job_id, explicit_path=media_path)
        if not target_media:
            err = PublishError(
                error_code="MEDIA_NOT_FOUND",
                message=f"No rendered media file found for job '{job_id}' in artifacts/render/",
                retryable=False,
            )
            return PublishResult(
                success=False,
                status=PublishStatus.FAILED,
                error=err,
            )

        # 2. Compute media checksum
        media_checksum = compute_file_sha256(target_media)

        # 3. STRICT QA GATE VERIFICATION
        qa_receipt = self._load_qa_receipt(job_id, db)
        if not qa_receipt:
            err = PublishError(
                error_code="QA_REPORT_MISSING",
                message=f"No QA receipt found for job '{job_id}'. All videos must pass the QA Engine before publishing.",
                retryable=False,
            )
            db.update_job_status(job_id, WorkflowState.FAILED_PUBLISH.value)
            db.log_event(job_id, WorkflowState.APPROVED.value, WorkflowState.FAILED_PUBLISH.value, reason="QA report missing")
            return PublishResult(
                success=False,
                status=PublishStatus.BLOCKED_QA,
                error=err,
            )

        qa_status = qa_receipt.get("status")
        qa_publish_allowed = qa_receipt.get("publish_allowed", False)

        if qa_status == QAStatus.BLOCK.value or not qa_publish_allowed:
            err = PublishError(
                error_code="QA_GATE_BLOCKED",
                message=f"Publishing blocked by QA Gate. QA Status: {qa_status}, Publish Allowed: {qa_publish_allowed}.",
                retryable=False,
                details=qa_receipt,
            )
            db.update_job_status(job_id, WorkflowState.FAILED_PUBLISH.value)
            db.log_event(job_id, WorkflowState.APPROVED.value, WorkflowState.FAILED_PUBLISH.value, reason="QA Gate blocked publishing")
            return PublishResult(
                success=False,
                status=PublishStatus.BLOCKED_QA,
                error=err,
            )

        # Verify media checksum matches QA receipt
        qa_checksum = qa_receipt.get("media_checksum_sha256")
        if qa_checksum is None:
            # P1-01 fix: DB-loaded receipt may lack checksum. Warn instead of silently bypassing.
            _logger = StructuredLogger(job_id=job_id, stage="publish")
            _logger.warning("qa_receipt_missing_checksum", {
                "note": "QA receipt loaded from DB fallback has no checksum; gate is advisory only.",
            })
        if qa_checksum and media_checksum != qa_checksum:
            err = PublishError(
                error_code="CHECKSUM_MISMATCH",
                message=(
                    f"Media SHA-256 on disk ({media_checksum[:12]}...) does not match "
                    f"QA receipt checksum ({qa_checksum[:12]}...). Video may have been modified post-QA."
                ),
                retryable=False,
            )
            db.update_job_status(job_id, WorkflowState.FAILED_PUBLISH.value)
            db.log_event(job_id, WorkflowState.APPROVED.value, WorkflowState.FAILED_PUBLISH.value, reason="Media checksum mismatch")
            return PublishResult(
                success=False,
                status=PublishStatus.BLOCKED_QA,
                error=err,
            )

        # 4. Resolve Publication Metadata
        content_id = qa_receipt.get("content_id") or job_id
        title = f"Autopilot Video — {job_id}"
        description = "Automated video generated by Project Autopilot."
        tags = ["autopilot", "shorts"]

        # Attempt to load rich metadata from ContentPackage and/or ScriptDocument
        base_dir = self.config.get_artifacts_dir()
        pkg_path = job_artifact_dir(job_id, base_dir=base_dir) / "script" / "content_package.json"
        if not pkg_path.exists():
            pkg_path = job_artifact_dir(job_id, base_dir=base_dir) / "media" / "content_package.json"

        script_file_path = job_artifact_dir(job_id, base_dir=base_dir) / "script" / "script.json"

        pkg_data: dict = {}
        pub_meta: dict = {}
        script_meta: dict = {}
        provenance_meta: dict = {}

        if pkg_path.exists():
            try:
                pkg_data = json.loads(pkg_path.read_text(encoding="utf-8"))
                pub_meta = pkg_data.get("publication") or {}
                script_meta = pkg_data.get("script") or {}
                provenance_meta = pkg_data.get("provenance") or {}
            except Exception:
                pass

        if not script_meta and script_file_path.exists():
            try:
                script_meta = json.loads(script_file_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        # Identify provider and whether this is a mock test fixture vs real production
        provider_name = (provenance_meta.get("provider") or "").lower()
        is_mock_provider = provider_name in ("mock", "mock_script", "mock_template")
        is_real_production = bool(provider_name and not is_mock_provider)

        def is_demo_placeholder(text: str) -> bool:
            if not text:
                return False
            lower_text = text.strip().lower()
            return any(phrase in lower_text for phrase in [
                "deterministic demo content",
                "phase 1 demo",
                "phase 3 demo",
                "demo content for topic",
            ])

        gen_meta = script_meta.get("generation_metadata") or {}

        # 4a. Title Resolution
        candidate_title = script_meta.get("working_title") or pub_meta.get("title") or script_meta.get("topic")
        if candidate_title:
            if is_real_production and "phase 1 demo" in candidate_title.lower():
                title = script_meta.get("working_title") or script_meta.get("topic") or title
            else:
                title = candidate_title

        # 4b. Description Resolution
        gen_desc = gen_meta.get("description")
        pub_desc = pub_meta.get("description")
        hook_text = (script_meta.get("hook") or "").strip()
        cta_text = (script_meta.get("cta") or "").strip()
        hook_cta_desc = f"{hook_text}\n\n{cta_text}".strip() if (hook_text and cta_text) else (hook_text or cta_text)

        if gen_desc and gen_desc.strip():
            description = gen_desc.strip()
        elif pub_desc and not is_demo_placeholder(pub_desc):
            description = pub_desc.strip()
        elif hook_cta_desc:
            description = hook_cta_desc
        elif pub_desc and is_mock_provider:
            description = pub_desc.strip()
        elif pub_desc and not is_real_production:
            description = pub_desc.strip()
        elif script_meta.get("topic"):
            description = f"Automated video for: {script_meta.get('topic')}"

        # 4c. Tags Resolution
        gen_tags = gen_meta.get("tags")
        pub_tags = pub_meta.get("hashtags") or pub_meta.get("tags")

        if gen_tags and isinstance(gen_tags, list) and len(gen_tags) > 0:
            resolved_tags = [str(t) for t in gen_tags if t]
        elif pub_tags and isinstance(pub_tags, list) and len(pub_tags) > 0:
            resolved_tags = [str(t) for t in pub_tags if t]
        else:
            resolved_tags = ["autopilot", "shorts"]

        # Ensure no demo tags in real production
        if is_real_production or not is_mock_provider:
            had_demo = any(t.lower() in ("demo", "mock") for t in resolved_tags)
            clean_tags = [t for t in resolved_tags if t.lower() not in ("demo", "mock")]
            if not clean_tags:
                tags = ["autopilot", "shorts"]
            elif had_demo and not any(t.lower() == "shorts" for t in clean_tags):
                clean_tags.append("shorts")
                tags = clean_tags
            else:
                tags = clean_tags
        else:
            tags = resolved_tags

        # 5. Resolve Visibility and Dry-Run
        vis_val = (visibility or self.config.publish_default_visibility).lower()
        if vis_val not in ("private", "unlisted", "public"):
            vis_val = "private"
        target_visibility = PublishVisibility(vis_val)

        is_dry_run = dry_run if dry_run is not None else self.config.publish_dry_run_default

        # 6. Compute Deterministic Idempotency Key
        metadata_str = f"{title}:{description}:{','.join(sorted(tags))}"
        metadata_hash = hashlib.sha256(metadata_str.encode("utf-8")).hexdigest()
        idempotency_key = compute_publish_idempotency_key(
            content_id=content_id,
            media_checksum=media_checksum,
            platform=platform,
            visibility=target_visibility.value,
            scheduled_time=scheduled_time,
            metadata_hash=metadata_hash,
        )

        # 7. Check Existing Idempotent Publication
        if not force_retry:
            existing = db.get_publication_by_idempotency(idempotency_key)
            if existing:
                logger = StructuredLogger(job_id=job_id, stage="publish")
                try:
                    rcpt_obj = PublicationReceipt.model_validate_json(existing["receipt_json"])
                    logger.info("idempotency.hit", {"idempotency_key": idempotency_key})
                    return PublishResult(
                        success=True,
                        status=PublishStatus.SKIPPED_DUPLICATE,
                        receipt=rcpt_obj,
                        attempts=[],
                    )
                except Exception as deserialization_err:
                    # P0-02 fix: Do NOT silently proceed — a matched idempotency key with
                    # unreadable receipt means a prior upload succeeded but receipt was lost.
                    # Treat as a soft duplicate to prevent duplicate uploads.
                    logger.warning("idempotency.hit_but_receipt_corrupt", {
                        "idempotency_key": idempotency_key,
                        "error": str(deserialization_err),
                    })
                    return PublishResult(
                        success=True,
                        status=PublishStatus.SKIPPED_DUPLICATE,
                        error=PublishError(
                            error_code="RECEIPT_DESERIALIZATION",
                            message=f"Prior publication matched but receipt could not be read: {deserialization_err}",
                            retryable=False,
                        ),
                        attempts=[],
                    )

        # 7.5 Protected publishing: enforcement of the operator approval gate.
        # Dry runs and CC-autonomous pre-authorized publishes with an existing
        # approved approval bypass nothing — dry runs only, since they never touch
        # a remote platform.
        if require_approval and not is_dry_run:
            gate_error = self._enforce_approval_gate(job_id, db, media_checksum, platform)
            if gate_error:
                db.record_error(
                    job_id,
                    stage="publish",
                    error_type="APPROVAL_GATE",
                    message=gate_error.message,
                    details={"error_code": gate_error.error_code, "platform": platform},
                )
                return PublishResult(
                    success=False,
                    status=PublishStatus.BLOCKED_APPROVAL,
                    error=gate_error,
                    attempts=[],
                )

        # 7.6 AUTONOMY SWITCH FINAL BOUNDARY RECHECK.
        # This is the last gate before any automated public publication. It is
        # authoritative (reads the persisted switch, never an earlier-captured
        # value) and scoped to autonomous PUBLIC cals only, so human/bridge
        # publishes and dry runs are untouched.
        if autonomous and not is_dry_run and target_visibility == PublishVisibility.PUBLIC:
            switch_on = effective_auto_publish(self.config, db)
            if not switch_on:
                switch_error = PublishError(
                    error_code="AUTONOMY_SWITCH_OFF",
                    message=(
                        "Autonomous public publishing was disabled (kill switch) "
                        "before publication; approval remains intact."
                    ),
                    retryable=False,
                )
                db.record_error(
                    job_id,
                    stage="publish",
                    error_type="AUTONOMY_SWITCH_OFF",
                    message=switch_error.message,
                    details={"error_code": switch_error.error_code, "platform": platform},
                )
                return PublishResult(
                    success=False,
                    status=PublishStatus.BLOCKED_AUTONOMY_SWITCH,
                    error=switch_error,
                    attempts=[],
                )

        # 8. State Machine: Transition to PUBLISHING
        db.update_job_status(job_id, WorkflowState.PUBLISHING.value, idempotency_key=idempotency_key)
        db.log_event(job_id, WorkflowState.APPROVED.value, WorkflowState.PUBLISHING.value, idempotency_key=idempotency_key)

        # 9. Build Canonical PublishRequest
        now_ts = int(time.time())
        try:
            target_platform = PublishPlatform(platform.lower())
        except Exception:
            target_platform = platform.lower()

        request = PublishRequest(
            publish_request_id=f"pubreq-{job_id}-{now_ts}",
            job_id=job_id,
            content_id=content_id,
            platform=target_platform,
            target_visibility=target_visibility,
            title=title[:100],
            description=description[:5000],
            tags=tags,
            category_id="28",
            media_path=str(target_media.resolve()),
            media_checksum_sha256=media_checksum,
            scheduled_publish_time=scheduled_time,
            made_for_kids=False,
            dry_run=is_dry_run,
            idempotency_key=idempotency_key,
        )

        # 10. Dispatch to Provider
        active_provider = provider
        if active_provider is None:
            active_provider = REGISTRY.get(platform)
            if active_provider is None:
                if str(platform).lower().startswith("postiz"):
                    from autopilot.providers.postiz_publisher import PostizPublisher
                    active_provider = PostizPublisher(config=self.config)
                else:
                    active_provider = YouTubePublisher(config=self.config)

        if hasattr(active_provider, "publish"):
            result = active_provider.publish(request)
        else:
            result = active_provider.upload_video(request)

        # 11. Handle Result and State Transition
        if result.success:
            if result.status == PublishStatus.DRY_RUN:
                db.update_job_status(job_id, WorkflowState.APPROVED.value, idempotency_key=idempotency_key)
                db.log_event(job_id, WorkflowState.PUBLISHING.value, WorkflowState.APPROVED.value, reason="Dry run complete")
            elif result.status == PublishStatus.SCHEDULED or (scheduled_time and result.receipt and result.receipt.scheduled_time):
                db.update_job_status(job_id, WorkflowState.APPROVED.value, idempotency_key=idempotency_key)
                db.log_event(job_id, WorkflowState.PUBLISHING.value, WorkflowState.APPROVED.value, reason="Scheduled for future publication")
            else:
                db.update_job_status(job_id, WorkflowState.PUBLISHED.value, idempotency_key=idempotency_key)
                db.log_event(job_id, WorkflowState.PUBLISHING.value, WorkflowState.PUBLISHED.value, idempotency_key=idempotency_key)

            if result.receipt:
                db.record_publication(result.receipt, metadata=request.model_dump())
        else:
            db.update_job_status(job_id, WorkflowState.FAILED_PUBLISH.value, idempotency_key=idempotency_key)
            db.log_event(job_id, WorkflowState.PUBLISHING.value, WorkflowState.FAILED_PUBLISH.value, reason=result.error.message if result.error else "Upload failed")

        # Record all attempts in DB
        for att in result.attempts:
            db.record_publish_attempt(att, job_id=job_id)

        # 12. Export Artifacts
        exported = self.export_publish_artifacts(result, request, job_id)
        if "receipt" in exported:
            db.record_artifact(job_id, exported["receipt"], "publication_receipt", checksum=media_checksum)
        db.record_artifact(job_id, exported["result"], "publication_result")

        return result

    publish = publish_job

    def publish_with_approval(
        self,
        job_id: str,
        platform: str = "youtube",
        visibility: Optional[str] = None,
        scheduled_time: Optional[str] = None,
        dry_run: Optional[bool] = None,
        force_retry: bool = False,
        media_path: Optional[str] = None,
        provider: Optional[PublisherProvider] = None,
        db_manager: Optional[DBManager] = None,
    ) -> PublishResult:
        """Publish via the protected approval loop (explicit operator approval enforced)."""
        return self.publish_job(
            job_id=job_id,
            platform=platform,
            visibility=visibility,
            scheduled_time=scheduled_time,
            dry_run=dry_run,
            force_retry=force_retry,
            media_path=media_path,
            provider=provider,
            db_manager=db_manager,
            require_approval=True,
        )
