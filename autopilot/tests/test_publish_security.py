"""Security and Credential Sanitization Tests — Milestone 6."""
import json
import pytest
from pathlib import Path

from autopilot.core.contracts import (
    PublishRequest, PublishPlatform, PublishVisibility
)
from autopilot.providers.youtube_publisher import YouTubePublisher, redact_secrets


def test_special_characters_in_metadata_safe(tmp_path):
    media = tmp_path / "safe.mp4"
    media.write_bytes(b"VIDEO")

    # Titles with quotes, shell injection tokens, XML tags
    dangerous_title = "Video; rm -rf /; $(whoami); `calc.exe` & <script>alert(1)</script>"
    req = PublishRequest(
        publish_request_id="req-sec-001",
        job_id="sec-job-001",
        content_id="sec-content-001",
        platform=PublishPlatform.YOUTUBE,
        target_visibility=PublishVisibility.PRIVATE,
        title=dangerous_title[:100],
        description="Normal description with 'quotes' and \"double quotes\"",
        media_path=str(media),
        media_checksum_sha256="abc123456",
        dry_run=True,
        idempotency_key="sec-key-001",
    )

    publisher = YouTubePublisher()
    res = publisher.upload_video(req)
    assert res.success is True
    # Verify metadata preserves string safely without command execution
    assert res.dry_run_preview["metadata"]["snippet"]["title"] == dangerous_title[:100]


def test_redact_secrets_in_error_messages():
    raw_error = "Error from https://oauth2.googleapis.com/token: client_secret=super_secret_key_999 Bearer ya29.secret_token"
    cleaned = redact_secrets(raw_error)
    assert "super_secret_key_999" not in cleaned
    assert "ya29.secret_token" not in cleaned
    assert "[REDACTED]" in cleaned


def test_path_traversal_refused():
    req = PublishRequest(
        publish_request_id="req-traversal-001",
        job_id="job-traversal",
        content_id="content-traversal",
        platform=PublishPlatform.YOUTUBE,
        target_visibility=PublishVisibility.PRIVATE,
        title="Title",
        media_path="/nonexistent/../../etc/shadow/secret.mp4",
        media_checksum_sha256="fake-hash",
        dry_run=False,
        idempotency_key="key-trav",
    )
    publisher = YouTubePublisher()
    res = publisher.upload_video(req)
    assert res.success is False
    assert res.error.error_code == "MEDIA_NOT_FOUND"
