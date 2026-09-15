"""Postiz Multi-Platform Publishing Sidecar Adapter — Phase 3.
External out-of-process HTTP REST client integration for Postiz (AGPL-3.0).
NO Postiz source code or internal modules are imported or included in Autopilot.
Communication occurs strictly over HTTP REST endpoints.
"""
from __future__ import annotations
import os
import json
import re
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from autopilot.core.config import Config, CONFIG
from autopilot.core.contracts import (
    PublishRequest,
    PublishResult,
    PublishStatus,
    PublicationReceipt,
    PublishAttempt,
    PublishError,
    PublishVisibility,
    PublishProviderProtocol,
)
from autopilot.providers.contracts import (
    PublisherProvider,
    ProviderHealth,
    CapabilityMetadata,
    CostUsageMetadata,
    ProviderErrorType,
)


def redact_secrets(text: str) -> str:
    """Ensure no auth tokens or API keys leak into logs or errors."""
    if not text:
        return ""
    cleaned = re.sub(r'(?i)bearer\s+[a-zA-Z0-9_\-\.]+', 'Bearer [REDACTED]', text)
    cleaned = re.sub(r'(?i)(api_key|token|secret)["\']?\s*[:=]\s*["\']?[a-zA-Z0-9_\-\.]+', r'\1=[REDACTED]', cleaned)
    return cleaned


class PostizPublisher(PublisherProvider):
    """Out-of-process HTTP client adapter for Postiz social publishing sidecar."""
    provider_name: str = "postiz"
    capability: CapabilityMetadata = CapabilityMetadata(
        max_resolution="4k",
        supports_9_16=True,
        local_only=False,
        license_note="Postiz Out-of-Process HTTP Sidecar (AGPL-3.0 clean boundary)",
    )
    error_type: ProviderErrorType = ProviderErrorType.PUBLISH_FAILED
    cost_meta: CostUsageMetadata = CostUsageMetadata(
        estimated_usd=0.0,
        provider_type="self_hosted_sidecar",
    )

    def __init__(self, config: Optional[Config] = None, transport: Optional[Callable] = None):
        self.config = config or CONFIG
        self.endpoint = (os.environ.get("POSTIZ_ENDPOINT") or getattr(self.config, "postiz_endpoint", None) or "http://127.0.0.1:3000").rstrip("/")
        self.api_key = os.environ.get("POSTIZ_API_KEY") or getattr(self.config, "postiz_api_key", None) or ""
        self._transport = transport

    def health_check(self) -> ProviderHealth:
        """Checks whether the out-of-process Postiz service is reachable."""
        url = f"{self.endpoint}/api/v1/health"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Project-Autopilot/1.0"})
            if self.api_key:
                req.add_header("Authorization", f"Bearer {self.api_key}")
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status in (200, 204):
                    return ProviderHealth(
                        healthy=True,
                        provider_name=self.provider_name,
                        error="",
                        details={"endpoint": self.endpoint, "status": "online"},
                    )
        except Exception as exc:
            pass

        return ProviderHealth(
            healthy=False,
            provider_name=self.provider_name,
            error="POSTIZ_UNAVAILABLE: Postiz HTTP sidecar service is unreachable or offline.",
            details={"endpoint": self.endpoint, "status": "offline"},
        )

    def publish(self, request: PublishRequest) -> PublishResult:
        """Dispatches upload or scheduled publication to Postiz via HTTP API."""
        now_iso = datetime.now(timezone.utc).isoformat()
        platform_str = str(getattr(request.platform, "value", request.platform)).lower()
        if "/" in platform_str:
            target_social = platform_str.split("/")[-1]
        else:
            target_social = platform_str

        # 1. Dry run preview handling
        if request.dry_run:
            preview = {
                "dry_run": True,
                "provider": self.provider_name,
                "target_platform": target_social,
                "title": request.title,
                "description_preview": (request.description or "")[:120],
                "visibility": str(request.target_visibility.value),
                "scheduled_time": request.scheduled_publish_time,
                "media_path": request.media_path,
                "idempotency_key": request.idempotency_key,
                "postiz_endpoint": self.endpoint,
            }
            return PublishResult(
                success=True,
                status=PublishStatus.DRY_RUN,
                receipt=PublicationReceipt(
                    receipt_id=f"dry-postiz-{request.job_id}",
                    job_id=request.job_id,
                    content_id=request.content_id,
                    render_checksum_sha256=request.media_checksum_sha256,
                    platform=request.platform,
                    provider=self.provider_name,
                    remote_video_id="dry-run-preview-id",
                    remote_url=None,
                    publication_state=PublishStatus.DRY_RUN,
                    visibility=request.target_visibility,
                    scheduled_time=request.scheduled_publish_time,
                    metadata_hash=request.media_checksum_sha256[:16],
                    idempotency_key=request.idempotency_key,
                    extra_metadata={"dry_run": True},
                ),
                dry_run_preview=preview,
            )

        # 2. Verify media file exists
        media_path = Path(request.media_path)
        if not media_path.exists() or media_path.stat().st_size == 0:
            return PublishResult(
                success=False,
                status=PublishStatus.FAILED,
                error=PublishError(
                    error_code="MISSING_MEDIA",
                    message=f"Media file not found or empty at {request.media_path}",
                    retryable=False,
                ),
            )

        # 3. Construct HTTP payload
        payload = {
            "title": request.title,
            "content": request.description or "",
            "tags": request.tags,
            "platform": target_social,
            "scheduledAt": request.scheduled_publish_time,
            "mediaUrl": str(media_path),
            "idempotencyKey": request.idempotency_key,
        }

        # 4. Dispatch to Postiz REST API
        post_url = f"{self.endpoint}/api/v1/posts"
        data_bytes = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Project-Autopilot/1.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        if self._transport:
            try:
                resp_data = self._transport(post_url, data_bytes, headers)
                status_code = resp_data.get("status_code", 200)
                body = resp_data.get("body", {})
            except Exception as exc:
                return PublishResult(
                    success=False,
                    status=PublishStatus.FAILED,
                    error=PublishError(
                        error_code="POSTIZ_UNAVAILABLE",
                        message=f"Postiz HTTP request failed: {redact_secrets(str(exc))}",
                        retryable=True,
                    ),
                )
        else:
            try:
                req = urllib.request.Request(post_url, data=data_bytes, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    status_code = resp.status
                    raw_body = resp.read().decode("utf-8")
                    body = json.loads(raw_body) if raw_body else {}
            except urllib.error.HTTPError as h_err:
                err_msg = redact_secrets(h_err.read().decode("utf-8", errors="replace"))
                return PublishResult(
                    success=False,
                    status=PublishStatus.FAILED,
                    error=PublishError(
                        error_code=f"HTTP_{h_err.code}",
                        message=f"Postiz HTTP error {h_err.code}: {err_msg}",
                        retryable=h_err.code in (429, 500, 502, 503, 504),
                    ),
                )
            except Exception as conn_err:
                return PublishResult(
                    success=False,
                    status=PublishStatus.FAILED,
                    error=PublishError(
                        error_code="POSTIZ_UNAVAILABLE",
                        message=f"Postiz sidecar is unavailable at {self.endpoint}: {redact_secrets(str(conn_err))}",
                        retryable=True,
                    ),
                )

        remote_id = body.get("id") or body.get("postId") or f"postiz-{request.job_id}"
        remote_url = body.get("url") or body.get("postUrl") or f"{self.endpoint}/posts/{remote_id}"
        is_scheduled = bool(request.scheduled_publish_time)
        pub_state = PublishStatus.SCHEDULED if is_scheduled else PublishStatus.PUBLISHED

        receipt = PublicationReceipt(
            receipt_id=f"rec-postiz-{request.job_id}",
            job_id=request.job_id,
            content_id=request.content_id,
            render_checksum_sha256=request.media_checksum_sha256,
            platform=request.platform,
            provider=self.provider_name,
            remote_video_id=str(remote_id),
            remote_url=str(remote_url),
            publication_state=pub_state,
            visibility=request.target_visibility,
            scheduled_time=request.scheduled_publish_time,
            metadata_hash=request.media_checksum_sha256[:16],
            idempotency_key=request.idempotency_key,
            extra_metadata={"platform": target_social, "postiz_id": remote_id},
        )

        return PublishResult(
            success=True,
            status=pub_state,
            receipt=receipt,
            attempts=[
                PublishAttempt(
                    attempt_id=f"att-postiz-{request.job_id}-1",
                    publish_request_id=request.publish_request_id,
                    attempt_number=1,
                    status=pub_state,
                    timestamp=now_iso,
                )
            ],
        )

    def upload_video(self, video_path: str, metadata: dict, **kwargs) -> dict:
        req = PublishRequest(
            publish_request_id=f"req-{metadata.get('job_id', 'job')}",
            job_id=metadata.get("job_id", "job"),
            content_id=metadata.get("content_id", "job"),
            platform=metadata.get("platform", "postiz"),
            title=metadata.get("title", "Untitled"),
            description=metadata.get("description", ""),
            tags=metadata.get("tags", []),
            media_path=video_path,
            media_checksum_sha256=metadata.get("media_checksum", "0" * 64),
            idempotency_key=metadata.get("idempotency_key", f"idemp-{metadata.get('job_id', 'job')}"),
        )
        res = self.publish(req)
        return res.model_dump(mode="json")

    def schedule(self, job_id: str, schedule_time: str, **kwargs) -> dict:
        metadata = kwargs.get("metadata", {})
        metadata["job_id"] = job_id
        metadata["scheduled_time"] = schedule_time
        video_path = kwargs.get("video_path", metadata.get("media_path", ""))
        req = PublishRequest(
            publish_request_id=f"req-{job_id}",
            job_id=job_id,
            content_id=job_id,
            platform=metadata.get("platform", "postiz"),
            title=metadata.get("title", "Untitled"),
            description=metadata.get("description", ""),
            tags=metadata.get("tags", []),
            media_path=video_path,
            media_checksum_sha256=metadata.get("media_checksum", "0" * 64),
            scheduled_publish_time=schedule_time,
            idempotency_key=f"idemp-{job_id}-sched",
        )
        res = self.publish(req)
        return res.model_dump(mode="json")
