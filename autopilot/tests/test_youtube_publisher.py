"""Unit tests for YouTube Data API v3 publisher adapter — Milestone 6."""
import json
import pytest
from pathlib import Path

from autopilot.core.config import Config
from autopilot.core.contracts import (
    PublishRequest, PublishStatus, PublishVisibility, PublishPlatform
)
from autopilot.providers.youtube_publisher import YouTubePublisher, redact_secrets


def create_mock_request(tmp_path, dry_run: bool = False, visibility: str = "private") -> PublishRequest:
    media = tmp_path / "test_video.mp4"
    media.write_bytes(b"TEST_VIDEO_BYTES_YOUTUBE_ADAPTER")
    import hashlib
    chk = hashlib.sha256(media.read_bytes()).hexdigest()

    return PublishRequest(
        publish_request_id="pubreq-yt-001",
        job_id="yt-job-001",
        content_id="content-yt-001",
        platform=PublishPlatform.YOUTUBE,
        target_visibility=PublishVisibility(visibility),
        title="Testing YouTube Upload",
        description="A great video about renewable energy.",
        tags=["solar", "energy"],
        category_id="28",
        media_path=str(media),
        media_checksum_sha256=chk,
        dry_run=dry_run,
        idempotency_key="idem-yt-001",
    )


def test_youtube_publisher_dry_run(tmp_path):
    req = create_mock_request(tmp_path, dry_run=True)
    publisher = YouTubePublisher()
    res = publisher.upload_video(req)

    assert res.success is True
    assert res.status == PublishStatus.DRY_RUN
    assert res.receipt is not None
    assert res.receipt.remote_video_id == "DRY_RUN_VIDEO_ID"
    assert res.dry_run_preview is not None
    assert "metadata" in res.dry_run_preview
    assert res.dry_run_preview["metadata"]["snippet"]["title"] == "Testing YouTube Upload"


def test_youtube_publisher_missing_credentials(tmp_path):
    req = create_mock_request(tmp_path, dry_run=False)
    # Isolate from real credentials/youtube_token.json on disk
    cfg = Config(
        project_root=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
    )
    cfg.youtube_access_token = None
    cfg.youtube_token_path = None
    publisher = YouTubePublisher(config=cfg)

    res = publisher.upload_video(req)
    assert res.success is False
    assert res.status == PublishStatus.FAILED
    assert res.error.error_code == "AUTH_CREDENTIALS_MISSING"


def test_youtube_publisher_mocked_upload_success(tmp_path):
    req = create_mock_request(tmp_path, dry_run=False)
    cfg = Config()
    cfg.youtube_access_token = "mock-secret-access-token-123"

    call_count = 0

    def mock_transport(http_req):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Resumable session initiation
            return 200, {"Location": "https://upload.youtube.com/session/abc123xyz"}, b""
        else:
            # Chunk upload completion
            resp_body = json.dumps({"id": "real-yt-vid-999", "snippet": {"title": req.title}}).encode("utf-8")
            return 200, {}, resp_body

    publisher = YouTubePublisher(config=cfg, transport=mock_transport)
    res = publisher.upload_video(req)

    assert res.success is True
    assert res.status == PublishStatus.SUCCESS
    assert res.receipt.remote_video_id == "real-yt-vid-999"
    assert res.receipt.remote_url == "https://youtu.be/real-yt-vid-999"
    assert call_count == 2


def test_youtube_publisher_transient_failure_retry(tmp_path, monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)

    req = create_mock_request(tmp_path, dry_run=False)
    cfg = Config()
    cfg.youtube_access_token = "mock-secret-access-token-123"
    cfg.publish_max_retries = 3

    attempt = 0

    def failing_transport(http_req):
        nonlocal attempt
        attempt += 1
        if attempt < 3:
            # Simulate 503 Service Unavailable
            return 503, {}, b"Service Unavailable"
        else:
            # Succeeded on 3rd attempt
            resp_body = json.dumps({"id": "recovered-vid-555"}).encode("utf-8")
            return 200, {"Location": "https://upload.youtube.com/session/recovered"}, resp_body

    publisher = YouTubePublisher(config=cfg, transport=failing_transport)
    # The first attempt fails session creation, retries, fails session, retries session and succeeds
    # Let's test with a transport that succeeds on session creation then completes
    step = 0
    def retry_transport(http_req):
        nonlocal step
        step += 1
        if step == 1:
            return 500, {}, b"Internal Server Error"
        elif step == 2:
            return 200, {"Location": "https://upload.youtube.com/session/123"}, b""
        else:
            return 200, {}, json.dumps({"id": "recovered-vid-777"}).encode("utf-8")

    publisher = YouTubePublisher(config=cfg, transport=retry_transport)
    res = publisher.upload_video(req)
    assert res.success is True
    assert res.receipt.remote_video_id == "recovered-vid-777"


def test_redact_secrets():
    text = "Authorization: Bearer ya29.a0AfH6SMB_secret_token and access_token=my_secret_token_val"
    redacted = redact_secrets(text)
    assert "ya29" not in redacted
    assert "Bearer [REDACTED]" in redacted
    assert "access_token=[REDACTED]" in redacted
