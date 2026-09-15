"""Phase 3 Tests — Postiz Multi-Platform Sidecar Adapter Contract and AGPL-3.0 Clean Boundary."""
import json
import pytest
from unittest.mock import patch, MagicMock

from autopilot.core.config import Config
from autopilot.core.contracts import (
    PublishRequest,
    PublishResult,
    PublishStatus,
    PublishVisibility,
    PublishPlatform,
)
from autopilot.providers.postiz_publisher import PostizPublisher, redact_secrets


def test_postiz_health_check_unavailable_when_offline():
    """When Postiz HTTP sidecar is unreachable, reports POSTIZ_UNAVAILABLE without crashing."""
    cfg = Config(postiz_endpoint="http://127.0.0.1:9999")  # Unused port
    publisher = PostizPublisher(config=cfg)

    health = publisher.health_check()
    assert health.healthy is False
    assert "POSTIZ_UNAVAILABLE" in health.error
    assert health.details["status"] == "offline"


def test_postiz_redacts_secrets():
    """Ensures tokens, bearer strings, and keys are redacted in outputs."""
    raw = "Failed request with Bearer secret_token_12345 and client_secret='shhh_dont_leak'"
    cleaned = redact_secrets(raw)
    assert "secret_token_12345" not in cleaned
    assert "shhh_dont_leak" not in cleaned
    assert "Bearer [REDACTED]" in cleaned


def test_postiz_dry_run_preview():
    """Dry-run returns structured preview without executing HTTP calls."""
    publisher = PostizPublisher(config=Config(postiz_endpoint="http://127.0.0.1:3000"))
    req = PublishRequest(
        publish_request_id="req-postiz-1",
        job_id="job-postiz-dry",
        content_id="content-postiz-1",
        media_path="/tmp/fake_video.mp4",
        media_checksum_sha256="abc1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        idempotency_key="idemp-postiz-key-1",
        title="Postiz Sidecar Test",
        description="Testing out-of-process boundary",
        tags=["autopilot", "postiz"],
        target_visibility=PublishVisibility.UNLISTED,
        platform="postiz/instagram",
        dry_run=True,
    )
    res = publisher.publish(req)
    assert res.success is True
    assert res.status == PublishStatus.DRY_RUN
    assert res.dry_run_preview["provider"] == "postiz"
    assert res.dry_run_preview["target_platform"] == "instagram"


def test_postiz_handles_offline_on_publish(tmp_path):
    """Real publish attempt against offline sidecar returns failed result with POSTIZ_UNAVAILABLE."""
    dummy_vid = tmp_path / "offline_video.mp4"
    dummy_vid.write_bytes(b"\x00" * 1024)

    publisher = PostizPublisher(config=Config(postiz_endpoint="http://127.0.0.1:9999"))
    req = PublishRequest(
        publish_request_id="req-postiz-2",
        job_id="job-postiz-offline",
        content_id="content-postiz-2",
        media_path=str(dummy_vid),
        media_checksum_sha256="abc1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        idempotency_key="idemp-postiz-key-2",
        title="Offline Test",
        target_visibility=PublishVisibility.PRIVATE,
        platform="postiz/tiktok",
        dry_run=False,
    )
    res = publisher.publish(req)
    assert res.success is False
    assert res.status == PublishStatus.FAILED
    assert "POSTIZ_UNAVAILABLE" in res.error.error_code or "POSTIZ_UNAVAILABLE" in res.error.message
