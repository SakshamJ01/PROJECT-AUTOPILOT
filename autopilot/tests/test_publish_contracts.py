"""Unit tests for Canonical Publishing Contracts — Milestone 6."""
import json
import pytest
from pydantic import ValidationError

from autopilot.core.contracts import (
    PublishPlatform, PublishVisibility, PublishStatus,
    PublishTarget, PublishAttempt, PublicationReceipt,
    PublishRequest, PublishResult, PublishError
)
from autopilot.core.publisher import compute_publish_idempotency_key


def test_publish_enums():
    assert PublishPlatform.YOUTUBE.value == "youtube"
    assert PublishPlatform.TIKTOK.value == "tiktok"
    assert PublishPlatform.INSTAGRAM.value == "instagram"

    assert PublishVisibility.PRIVATE.value == "private"
    assert PublishVisibility.UNLISTED.value == "unlisted"
    assert PublishVisibility.PUBLIC.value == "public"

    assert PublishStatus.PENDING.value == "PENDING"
    assert PublishStatus.DRY_RUN.value == "DRY_RUN"
    assert PublishStatus.SUCCESS.value == "SUCCESS"
    assert PublishStatus.FAILED.value == "FAILED"
    assert PublishStatus.BLOCKED_QA.value == "BLOCKED_QA"
    assert PublishStatus.SKIPPED_DUPLICATE.value == "SKIPPED_DUPLICATE"


def test_publish_request_validation():
    req = PublishRequest(
        publish_request_id="req-001",
        job_id="job-001",
        content_id="content-001",
        platform=PublishPlatform.YOUTUBE,
        target_visibility=PublishVisibility.PRIVATE,
        title="Valid YouTube Title",
        description="A detailed description of the video.",
        tags=["solar", "technology"],
        category_id="28",
        media_path="/path/to/media.mp4",
        media_checksum_sha256="abc123def456",
        idempotency_key="idem-key-001",
    )
    assert req.title == "Valid YouTube Title"
    assert req.target_visibility == PublishVisibility.PRIVATE
    assert req.category_id == "28"
    assert req.made_for_kids is False
    assert req.dry_run is False

    # Title exceeds 100 characters
    with pytest.raises(ValidationError):
        PublishRequest(
            publish_request_id="req-002",
            job_id="job-001",
            content_id="content-001",
            title="A" * 105,
            media_path="/path/to/media.mp4",
            media_checksum_sha256="abc123def456",
            idempotency_key="idem-key-002",
        )

    # Description exceeds 5000 characters
    with pytest.raises(ValidationError):
        PublishRequest(
            publish_request_id="req-003",
            job_id="job-001",
            content_id="content-001",
            title="Valid Title",
            description="A" * 5005,
            media_path="/path/to/media.mp4",
            media_checksum_sha256="abc123def456",
            idempotency_key="idem-key-003",
        )


def test_publication_receipt_round_trip():
    receipt = PublicationReceipt(
        receipt_id="rcpt-001",
        job_id="job-001",
        content_id="content-001",
        render_checksum_sha256="sha256-media-bytes",
        qa_receipt_reference="qa-rcpt-001",
        platform=PublishPlatform.YOUTUBE,
        provider="youtube",
        remote_video_id="dQw4w9WgXcQ",
        remote_url="https://youtu.be/dQw4w9WgXcQ",
        publication_state=PublishStatus.SUCCESS,
        visibility=PublishVisibility.PRIVATE,
        scheduled_time=None,
        published_at="2026-09-11T12:00:00Z",
        metadata_hash="hash-meta-001",
        idempotency_key="idem-key-001",
        extra_metadata={"resolution": "1080p"},
    )
    dumped = receipt.model_dump_json()
    reloaded = PublicationReceipt.model_validate_json(dumped)
    assert reloaded.receipt_id == "rcpt-001"
    assert reloaded.remote_video_id == "dQw4w9WgXcQ"
    assert reloaded.remote_url == "https://youtu.be/dQw4w9WgXcQ"
    assert reloaded.publication_state == PublishStatus.SUCCESS


def test_publish_result_structure():
    res = PublishResult(
        success=True,
        status=PublishStatus.SUCCESS,
        receipt=PublicationReceipt(
            receipt_id="rcpt-002",
            job_id="job-002",
            content_id="content-002",
            render_checksum_sha256="sha256-media-bytes",
            platform=PublishPlatform.YOUTUBE,
            provider="youtube",
            remote_video_id="vid-123",
            remote_url="https://youtu.be/vid-123",
            publication_state=PublishStatus.SUCCESS,
            visibility=PublishVisibility.PRIVATE,
            metadata_hash="hash-002",
            idempotency_key="idem-002",
        ),
        attempts=[
            PublishAttempt(
                attempt_id="att-001",
                publish_request_id="req-001",
                attempt_number=1,
                status=PublishStatus.SUCCESS,
            )
        ],
    )
    assert res.success is True
    assert res.status == PublishStatus.SUCCESS
    assert len(res.attempts) == 1
    assert res.receipt.remote_video_id == "vid-123"


def test_publish_idempotency_key_determinism():
    key1 = compute_publish_idempotency_key(
        content_id="c-001",
        media_checksum="sha-media",
        platform="youtube",
        visibility="private",
        scheduled_time="2026-09-12T10:00:00Z",
        metadata_hash="meta-hash",
    )
    key2 = compute_publish_idempotency_key(
        content_id="c-001",
        media_checksum="sha-media",
        platform="youtube",
        visibility="private",
        scheduled_time="2026-09-12T10:00:00Z",
        metadata_hash="meta-hash",
    )
    assert key1 == key2
    assert len(key1) == 64

    # Different visibility yields different key
    key_pub = compute_publish_idempotency_key(
        content_id="c-001",
        media_checksum="sha-media",
        platform="youtube",
        visibility="public",
        scheduled_time="2026-09-12T10:00:00Z",
        metadata_hash="meta-hash",
    )
    assert key1 != key_pub
