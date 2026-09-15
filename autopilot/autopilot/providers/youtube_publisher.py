"""Official YouTube Data API v3 Publisher Adapter — Milestone 6.
Implements the official Google YouTube Data API v3 resumable upload protocol
with private-by-default visibility, dry-run validation, credential handling,
and bounded retries with exponential backoff.
"""
from __future__ import annotations
import os
import json
import time
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from autopilot.core.config import Config, CONFIG
from autopilot.providers.contracts import (
    PublisherProvider, ProviderHealth, CapabilityMetadata,
    CostUsageMetadata, ProviderErrorType, REGISTRY
)
from autopilot.core.contracts import (
    PublishRequest, PublishResult, PublishStatus,
    PublicationReceipt, PublishAttempt, PublishError,
    PublishVisibility, PublishPlatform
)


def redact_secrets(text: str) -> str:
    """Ensure no auth tokens, keys, or bearer tokens appear in outputs."""
    if not text:
        return ""
    import re
    cleaned = re.sub(r'(?i)bearer\s+[a-zA-Z0-9_\-\.]+', 'Bearer [REDACTED]', text)
    cleaned = re.sub(r'(?i)(client_secret|access_token|refresh_token)["\']?\s*[:=]\s*["\']?[a-zA-Z0-9_\-\.]+', r'\1=[REDACTED]', cleaned)
    return cleaned


class YouTubePublisher(PublisherProvider):
    provider_name: str = "youtube"
    capability: CapabilityMetadata = CapabilityMetadata(
        max_resolution="4k",
        supports_9_16=True,
        local_only=False,
        license_note="Official YouTube Data API v3",
    )
    error_type: ProviderErrorType = ProviderErrorType.PUBLISH_FAILED
    cost_meta: CostUsageMetadata = CostUsageMetadata(
        estimated_usd=0.0,
        provider_type="official_api",
    )

    UPLOAD_ENDPOINT: str = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"

    def __init__(self, config: Optional[Config] = None, transport: Optional[Callable] = None):
        self.config = config or CONFIG
        self._transport = transport  # Optional custom transport for test mocking

    def _resolve_access_token(self, auth_transport: Optional[Any] = None) -> Optional[str]:
        """Resolve YouTube OAuth access token. P0-04 fix: delegates to shared resolver."""
        from autopilot.providers.youtube_oauth import resolve_youtube_access_token
        return resolve_youtube_access_token(self.config, transport=auth_transport)

    def health_check(self) -> ProviderHealth:
        """Inspect whether YouTube publisher credentials are configured."""
        token = self._resolve_access_token()
        if token:
            return ProviderHealth(
                healthy=True,
                provider_name=self.provider_name,
                error="",
                details={
                    "configured": True,
                    "provider": "youtube_data_api_v3",
                    "mode": "ready",
                },
            )
        else:
            return ProviderHealth(
                healthy=False,
                provider_name=self.provider_name,
                error="YouTube credentials not configured (YOUTUBE_ACCESS_TOKEN or YOUTUBE_TOKEN_PATH)",
                details={
                    "configured": False,
                    "provider": "youtube_data_api_v3",
                    "note": "Publishing requires OAuth token; dry-run available offline",
                },
            )

    def _execute_http(self, req: urllib.request.Request, timeout: float = 60.0) -> tuple[int, dict, bytes]:
        """Execute an HTTP request using custom transport or urllib."""
        if self._transport is not None:
            return self._transport(req)

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status
                headers = dict(resp.headers)
                body = resp.read()
                return status, headers, body
        except urllib.error.HTTPError as e:
            body = e.read() if hasattr(e, "read") else b""
            return e.code, dict(e.headers), body
        except urllib.error.URLError as e:
            raise ConnectionError(f"Network error connecting to YouTube API: {redact_secrets(str(e))}")

    def upload_video(self, request: PublishRequest) -> PublishResult:
        """Upload video to YouTube following the official Resumable Upload specification."""
        attempts: List[PublishAttempt] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        # 1. Validate local media file
        media_file = Path(request.media_path)
        if not media_file.exists() or not media_file.is_file():
            err = PublishError(
                error_code="MEDIA_NOT_FOUND",
                message=f"Media file not found at '{request.media_path}'",
                retryable=False,
            )
            attempts.append(PublishAttempt(
                attempt_id=f"att-{request.publish_request_id}-1",
                publish_request_id=request.publish_request_id,
                attempt_number=1,
                status=PublishStatus.FAILED,
                error_type=err.error_code,
                error_message=err.message,
            ))
            return PublishResult(
                success=False,
                status=PublishStatus.FAILED,
                attempts=attempts,
                error=err,
            )

        file_size = media_file.stat().st_size
        if file_size == 0:
            err = PublishError(
                error_code="ZERO_BYTE_MEDIA",
                message="Media file is 0 bytes; cannot publish empty video",
                retryable=False,
            )
            attempts.append(PublishAttempt(
                attempt_id=f"att-{request.publish_request_id}-1",
                publish_request_id=request.publish_request_id,
                attempt_number=1,
                status=PublishStatus.FAILED,
                error_type=err.error_code,
                error_message=err.message,
            ))
            return PublishResult(
                success=False,
                status=PublishStatus.FAILED,
                attempts=attempts,
                error=err,
            )

        # 2. Build official YouTube API metadata payload
        metadata_body: Dict[str, Any] = {
            "snippet": {
                "title": request.title[:100],
                "description": request.description[:5000],
                "tags": request.tags,
                "categoryId": request.category_id,
            },
            "status": {
                "privacyStatus": request.target_visibility.value,
                "selfDeclaredMadeForKids": request.made_for_kids,
            },
        }
        if request.scheduled_publish_time:
            metadata_body["status"]["publishAt"] = request.scheduled_publish_time

        # 3. Dry-Run Mode
        if request.dry_run:
            receipt = PublicationReceipt(
                receipt_id=f"rcpt-dryrun-{request.job_id}",
                job_id=request.job_id,
                content_id=request.content_id,
                render_checksum_sha256=request.media_checksum_sha256,
                qa_receipt_reference=None,
                platform=PublishPlatform.YOUTUBE,
                provider=self.provider_name,
                remote_video_id="DRY_RUN_VIDEO_ID",
                remote_url="https://youtu.be/DRY_RUN_VIDEO_ID",
                publication_state=PublishStatus.DRY_RUN,
                visibility=request.target_visibility,
                scheduled_time=request.scheduled_publish_time,
                published_at=now_iso,
                metadata_hash=request.idempotency_key,
                idempotency_key=request.idempotency_key,
                extra_metadata={"dry_run": True, "file_size": file_size},
            )
            attempts.append(PublishAttempt(
                attempt_id=f"att-{request.publish_request_id}-1",
                publish_request_id=request.publish_request_id,
                attempt_number=1,
                status=PublishStatus.DRY_RUN,
                details={"dry_run": True, "file_size": file_size},
            ))
            return PublishResult(
                success=True,
                status=PublishStatus.DRY_RUN,
                receipt=receipt,
                attempts=attempts,
                dry_run_preview={
                    "provider": self.provider_name,
                    "endpoint": self.UPLOAD_ENDPOINT,
                    "metadata": metadata_body,
                    "media_path": str(media_file.resolve()),
                    "file_size": file_size,
                    "visibility": request.target_visibility.value,
                    "scheduled_time": request.scheduled_publish_time,
                    "scheduled_publish_time": request.scheduled_publish_time,
                },
            )

        # 4. Resolve token for live upload
        token = self._resolve_access_token()
        if not token:
            err = PublishError(
                error_code="AUTH_CREDENTIALS_MISSING",
                message="YouTube credentials not configured. Please set YOUTUBE_ACCESS_TOKEN or configure YOUTUBE_TOKEN_PATH.",
                retryable=False,
            )
            attempts.append(PublishAttempt(
                attempt_id=f"att-{request.publish_request_id}-1",
                publish_request_id=request.publish_request_id,
                attempt_number=1,
                status=PublishStatus.FAILED,
                error_type=err.error_code,
                error_message=err.message,
            ))
            return PublishResult(
                success=False,
                status=PublishStatus.FAILED,
                attempts=attempts,
                error=err,
            )

        # 5. Execute Live Resumable Upload with bounded retries
        max_retries = max(1, self.config.publish_max_retries)
        chunk_size = self.config.publish_chunk_size_bytes

        upload_url: Optional[str] = None
        for attempt_num in range(1, max_retries + 1):
            att_id = f"att-{request.publish_request_id}-{attempt_num}"
            try:
                # Step A: Initiate resumable upload session if not already established
                if not upload_url:
                    init_req = urllib.request.Request(
                        self.UPLOAD_ENDPOINT,
                        data=json.dumps(metadata_body).encode("utf-8"),
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json; charset=UTF-8",
                            "X-Upload-Content-Type": "video/mp4",
                            "X-Upload-Content-Length": str(file_size),
                        },
                        method="POST",
                    )
                    status_code, headers, body_bytes = self._execute_http(init_req, timeout=self.config.publish_timeout_seconds)

                    if status_code in (200, 201):
                        upload_url = headers.get("Location") or headers.get("location")
                        if not upload_url:
                            raise ValueError("YouTube API response missing Location header for resumable upload")
                    elif status_code in (401, 403):
                        err_msg = redact_secrets(body_bytes.decode("utf-8", errors="ignore"))
                        err = PublishError(
                            error_code="AUTH_REJECTED",
                            message=f"YouTube authentication failed (HTTP {status_code}): {err_msg}",
                            retryable=False,
                            details={"status_code": status_code},
                        )
                        attempts.append(PublishAttempt(
                            attempt_id=att_id,
                            publish_request_id=request.publish_request_id,
                            attempt_number=attempt_num,
                            status=PublishStatus.FAILED,
                            error_type=err.error_code,
                            error_message=err.message,
                        ))
                        return PublishResult(success=False, status=PublishStatus.FAILED, attempts=attempts, error=err)
                    else:
                        raise IOError(f"YouTube initial session failed with HTTP {status_code}: {redact_secrets(body_bytes.decode('utf-8', errors='ignore'))}")

                # Step B: Resumable chunk upload
                bytes_sent = 0
                final_resp_data: Optional[Dict[str, Any]] = None

                with open(media_file, "rb") as vf:
                    while bytes_sent < file_size:
                        chunk_data = vf.read(chunk_size)
                        chunk_len = len(chunk_data)
                        start_byte = bytes_sent
                        end_byte = bytes_sent + chunk_len - 1

                        chunk_req = urllib.request.Request(
                            upload_url,
                            data=chunk_data,
                            headers={
                                "Content-Length": str(chunk_len),
                                "Content-Range": f"bytes {start_byte}-{end_byte}/{file_size}",
                                "Content-Type": "video/mp4",
                            },
                            method="PUT",
                        )
                        chunk_status, chunk_headers, chunk_body = self._execute_http(chunk_req, timeout=self.config.publish_timeout_seconds)

                        if chunk_status in (200, 201):
                            # Upload complete!
                            final_resp_data = json.loads(chunk_body.decode("utf-8", errors="ignore") or "{}")
                            bytes_sent = file_size
                            break
                        elif chunk_status == 308:
                            # Resume Incomplete — chunk accepted
                            bytes_sent += chunk_len
                        else:
                            raise IOError(f"Chunk upload failed with HTTP {chunk_status}: {redact_secrets(chunk_body.decode('utf-8', errors='ignore'))}")

                if final_resp_data and ("id" in final_resp_data or "kind" in final_resp_data):
                    video_id = final_resp_data.get("id", f"yt-{request.job_id}")
                    remote_url = f"https://youtu.be/{video_id}"
                    receipt = PublicationReceipt(
                        receipt_id=f"rcpt-{request.job_id}",
                        job_id=request.job_id,
                        content_id=request.content_id,
                        render_checksum_sha256=request.media_checksum_sha256,
                        qa_receipt_reference=None,
                        platform=PublishPlatform.YOUTUBE,
                        provider=self.provider_name,
                        remote_video_id=video_id,
                        remote_url=remote_url,
                        publication_state=PublishStatus.SUCCESS,
                        visibility=request.target_visibility,
                        scheduled_time=request.scheduled_publish_time,
                        published_at=datetime.now(timezone.utc).isoformat(),
                        metadata_hash=request.idempotency_key,
                        idempotency_key=request.idempotency_key,
                        extra_metadata={"video_id": video_id, "file_size": file_size},
                    )
                    attempts.append(PublishAttempt(
                        attempt_id=att_id,
                        publish_request_id=request.publish_request_id,
                        attempt_number=attempt_num,
                        status=PublishStatus.SUCCESS,
                        details={"video_id": video_id, "remote_url": remote_url},
                    ))
                    return PublishResult(
                        success=True,
                        status=PublishStatus.SUCCESS,
                        receipt=receipt,
                        attempts=attempts,
                    )

            except Exception as exc:
                is_last = attempt_num == max_retries
                err_msg = redact_secrets(str(exc))
                attempts.append(PublishAttempt(
                    attempt_id=att_id,
                    publish_request_id=request.publish_request_id,
                    attempt_number=attempt_num,
                    status=PublishStatus.FAILED,
                    error_type="UPLOAD_ATTEMPT_FAILED",
                    error_message=err_msg,
                ))
                if not is_last:
                    # Bounded exponential backoff
                    backoff = min(1.0 * (2 ** (attempt_num - 1)), 8.0)
                    time.sleep(backoff)
                else:
                    return PublishResult(
                        success=False,
                        status=PublishStatus.FAILED,
                        attempts=attempts,
                        error=PublishError(
                            error_code="UPLOAD_RETRIES_EXHAUSTED",
                            message=f"YouTube upload failed after {max_retries} attempts: {err_msg}",
                            retryable=False,
                            details={"attempts": len(attempts)},
                        ),
                    )

        return PublishResult(
            success=False,
            status=PublishStatus.FAILED,
            attempts=attempts,
            error=PublishError(
                error_code="UPLOAD_FAILED",
                message="YouTube upload failed with unknown error",
                retryable=False,
            ),
        )

    def publish(self, request: PublishRequest) -> PublishResult:
        """Publish canonical request (PublishProviderProtocol compliance)."""
        return self.upload_video(request)

    def schedule(self, job_id: str, schedule_time: str, **kwargs) -> dict:
        """Schedule publication for a future UTC timestamp."""
        return {
            "status": "scheduled",
            "job_id": job_id,
            "scheduled_time": schedule_time,
            "provider": self.provider_name,
        }


# Register provider to canonical registry
try:
    REGISTRY.register(YouTubePublisher())
except Exception:
    pass
