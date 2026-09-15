"""Deterministic Mock Publisher — Milestone 6.
Offline test provider simulating video publication without external API calls.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from autopilot.providers.contracts import (
    PublisherProvider, ProviderHealth, CapabilityMetadata,
    CostUsageMetadata, ProviderErrorType, REGISTRY
)
from autopilot.core.contracts import (
    PublishRequest, PublishResult, PublishStatus,
    PublicationReceipt, PublishAttempt, PublishError,
    PublishVisibility, PublishPlatform
)


class MockPublisher(PublisherProvider):
    provider_name: str = "mock_publisher"
    capability: CapabilityMetadata = CapabilityMetadata(
        max_resolution="1080p",
        supports_9_16=True,
        local_only=True,
        license_note="Deterministic test mock publisher; offline only",
    )
    error_type: ProviderErrorType = ProviderErrorType.UNCONFIGURED
    cost_meta: CostUsageMetadata = CostUsageMetadata(
        estimated_usd=0.0,
        provider_type="local",
    )

    def __init__(self, should_fail: bool = False, fail_message: str = "Simulated mock failure"):
        self.should_fail = should_fail
        self.fail_message = fail_message

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            healthy=True,
            provider_name=self.provider_name,
            error="",
            details={"mode": "deterministic_mock", "status": "AVAILABLE"},
        )

    def upload_video(self, request: PublishRequest) -> PublishResult:
        now_iso = datetime.now(timezone.utc).isoformat()
        att_id = f"att-{request.publish_request_id}-1"

        if self.should_fail:
            err = PublishError(
                error_code="MOCK_PUBLISH_FAILURE",
                message=self.fail_message,
                retryable=False,
            )
            attempt = PublishAttempt(
                attempt_id=att_id,
                publish_request_id=request.publish_request_id,
                attempt_number=1,
                status=PublishStatus.FAILED,
                error_type=err.error_code,
                error_message=err.message,
            )
            return PublishResult(
                success=False,
                status=PublishStatus.FAILED,
                attempts=[attempt],
                error=err,
            )

        status = PublishStatus.DRY_RUN if request.dry_run else PublishStatus.SUCCESS
        video_id = f"mock-yt-{request.job_id}"
        receipt = PublicationReceipt(
            receipt_id=f"rcpt-mock-{request.job_id}",
            job_id=request.job_id,
            content_id=request.content_id,
            render_checksum_sha256=request.media_checksum_sha256,
            qa_receipt_reference=None,
            platform=request.platform,
            provider=self.provider_name,
            remote_video_id=video_id,
            remote_url=f"https://youtu.be/{video_id}",
            publication_state=status,
            visibility=request.target_visibility,
            scheduled_time=request.scheduled_publish_time,
            published_at=now_iso,
            metadata_hash=request.idempotency_key,
            idempotency_key=request.idempotency_key,
            extra_metadata={"mock": True},
        )
        attempt = PublishAttempt(
            attempt_id=att_id,
            publish_request_id=request.publish_request_id,
            attempt_number=1,
            status=status,
            details={"video_id": video_id},
        )
        return PublishResult(
            success=True,
            status=status,
            receipt=receipt,
            attempts=[attempt],
            dry_run_preview={"mock_dry_run": True} if request.dry_run else None,
        )

    def schedule(self, job_id: str, schedule_time: str, **kwargs) -> dict:
        return {
            "status": "scheduled",
            "job_id": job_id,
            "scheduled_time": schedule_time,
            "provider": self.provider_name,
        }


try:
    REGISTRY.register(MockPublisher())
except Exception:
    pass
