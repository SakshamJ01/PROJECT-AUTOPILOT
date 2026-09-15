"""Focused Integration Tests for the YouTube Publishing Pipeline & Verification.

Verifies:
1. Approval/publication gate enforcement (QA pass, QA block, missing QA, checksum mismatch).
2. Private/unlisted visibility (explicit private and unlisted targets, payload validation).
3. Missing OAuth credentials handling (fail closed with AUTH_CREDENTIALS_MISSING).
4. Idempotent publish behavior (duplicate protection, force retry).
5. Successful publisher result persistence (SQLite state, DB publication record, artifact export).
"""
import json
import hashlib
import pytest
from pathlib import Path
from typing import Dict, Any, Optional

from autopilot.core.config import Config, CONFIG
from autopilot.core.contracts import (
    PublishRequest,
    PublishResult,
    PublishStatus,
    PublishVisibility,
    PublishPlatform,
    QAStatus,
    QAReport,
)
from autopilot.core.state_machine import WorkflowState
from autopilot.core.publisher import PublishingEngine
from autopilot.providers.youtube_publisher import YouTubePublisher
from autopilot.db.manager import DBManager


def create_sample_job_environment(
    tmp_path: Path,
    job_id: str,
    media_content: bytes = b"SAMPLE_VIDEO_STREAM_BYTES_FOR_YOUTUBE_VERIFICATION",
    qa_status: str = "PASS",
    publish_allowed: bool = True,
    checksum_tamper: bool = False,
    omit_qa: bool = False,
    package_data: Optional[Dict[str, Any]] = None,
    script_data: Optional[Dict[str, Any]] = None,
):
    """Set up realistic job artifacts, media files, QA receipts, and DB."""
    db_path = tmp_path / "artifacts" / "autopilot.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = DBManager(str(db_path))
    db.init_schema()
    db.create_job(job_id=job_id, topic=f"Topic for {job_id}")

    # Rendered video
    render_dir = tmp_path / "artifacts" / "jobs" / job_id / "render"
    render_dir.mkdir(parents=True, exist_ok=True)
    media_file = render_dir / "final.mp4"
    media_file.write_bytes(media_content)
    real_checksum = hashlib.sha256(media_content).hexdigest()

    # QA receipt
    if not omit_qa:
        qa_dir = tmp_path / "artifacts" / "jobs" / job_id / "quality"
        qa_dir.mkdir(parents=True, exist_ok=True)
        qa_chk = "tampered_checksum_value_12345" if checksum_tamper else real_checksum
        qa_receipt = {
            "receipt_id": f"rcpt-qa-{job_id}",
            "job_id": job_id,
            "content_id": job_id,
            "status": qa_status,
            "publish_allowed": publish_allowed,
            "media_path": str(media_file),
            "media_checksum_sha256": qa_chk,
        }
        (qa_dir / "receipt.json").write_text(json.dumps(qa_receipt), encoding="utf-8")

        qa_report = QAReport(
            report_id=f"qa-rep-{job_id}",
            job_id=job_id,
            content_id=job_id,
            status=QAStatus(qa_status),
            publish_allowed=publish_allowed,
            media_checksum_sha256=qa_chk,
        )
        db.record_qa_report(qa_report)

    # Script / metadata artifacts
    script_dir = tmp_path / "artifacts" / "jobs" / job_id / "script"
    script_dir.mkdir(parents=True, exist_ok=True)

    if script_data:
        (script_dir / "script.json").write_text(json.dumps(script_data), encoding="utf-8")
    else:
        default_script = {
            "working_title": f"3 Mind-Blowing Facts About Space ({job_id})",
            "topic": "Space Facts",
            "hook": "Did you know space is completely silent?",
            "cta": "Subscribe for daily space shorts!",
            "generation_metadata": {
                "description": "Discover 3 amazing facts about our vast universe.",
                "tags": ["space", "facts", "astronomy", "shorts"],
            },
        }
        (script_dir / "script.json").write_text(json.dumps(default_script), encoding="utf-8")

    if package_data:
        (script_dir / "content_package.json").write_text(json.dumps(package_data), encoding="utf-8")

    return db, media_file, real_checksum


# =========================================================================
# 1. APPROVAL / PUBLICATION GATE ENFORCEMENT TESTS
# =========================================================================

def test_youtube_gate_blocks_when_qa_missing(tmp_path, monkeypatch):
    """Publishing to YouTube must fail-closed if QA receipt is missing."""
    job_id = "job-yt-no-qa"
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    db, media_file, chk = create_sample_job_environment(tmp_path, job_id, omit_qa=True)

    cfg = Config(artifacts_dir=tmp_path / "artifacts", db_path=tmp_path / "artifacts" / "autopilot.db")
    cfg.youtube_access_token = "valid-token-123"
    engine = PublishingEngine(config=cfg, db=db)

    result = engine.publish_job(job_id=job_id, platform="youtube", visibility="private")
    assert result.success is False
    assert result.status == PublishStatus.BLOCKED_QA
    assert result.error.error_code == "QA_REPORT_MISSING"

    job_state = db.get_job(job_id)
    assert job_state["status"] == WorkflowState.FAILED_PUBLISH.value


def test_youtube_gate_blocks_when_qa_blocks(tmp_path, monkeypatch):
    """Publishing to YouTube must fail-closed if QA status is BLOCK."""
    job_id = "job-yt-blocked-qa"
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    db, media_file, chk = create_sample_job_environment(
        tmp_path, job_id, qa_status="BLOCK", publish_allowed=False
    )

    cfg = Config(artifacts_dir=tmp_path / "artifacts", db_path=tmp_path / "artifacts" / "autopilot.db")
    cfg.youtube_access_token = "valid-token-123"
    engine = PublishingEngine(config=cfg, db=db)

    result = engine.publish_job(job_id=job_id, platform="youtube", visibility="private")
    assert result.success is False
    assert result.status == PublishStatus.BLOCKED_QA
    assert result.error.error_code == "QA_GATE_BLOCKED"

    job_state = db.get_job(job_id)
    assert job_state["status"] == WorkflowState.FAILED_PUBLISH.value


def test_youtube_gate_blocks_when_checksum_mismatch(tmp_path, monkeypatch):
    """Publishing to YouTube must fail-closed if video was modified post-QA."""
    job_id = "job-yt-tampered"
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    db, media_file, chk = create_sample_job_environment(
        tmp_path, job_id, qa_status="PASS", publish_allowed=True, checksum_tamper=True
    )

    cfg = Config(artifacts_dir=tmp_path / "artifacts", db_path=tmp_path / "artifacts" / "autopilot.db")
    cfg.youtube_access_token = "valid-token-123"
    engine = PublishingEngine(config=cfg, db=db)

    result = engine.publish_job(job_id=job_id, platform="youtube", visibility="private")
    assert result.success is False
    assert result.status == PublishStatus.BLOCKED_QA
    assert result.error.error_code == "CHECKSUM_MISMATCH"


# =========================================================================
# 2. PRIVATE / UNLISTED VISIBILITY TESTS
# =========================================================================

def test_youtube_publish_private_visibility_payload(tmp_path, monkeypatch):
    """Verifies that private visibility is strictly encoded in the YouTube API payload."""
    job_id = "job-yt-private-vis"
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    db, media_file, chk = create_sample_job_environment(tmp_path, job_id)

    cfg = Config(artifacts_dir=tmp_path / "artifacts", db_path=tmp_path / "artifacts" / "autopilot.db")
    cfg.youtube_access_token = "valid-access-token"

    captured_metadata = None

    def mock_transport(http_req):
        nonlocal captured_metadata
        if http_req.method == "POST":
            # Session initiation payload
            body = json.loads(http_req.data.decode("utf-8"))
            captured_metadata = body
            return 200, {"Location": "https://upload.youtube.com/session/sess-priv-1"}, b""
        else:
            # Chunk upload
            resp = {"id": "yt-vid-priv-123", "status": {"privacyStatus": "private"}}
            return 200, {}, json.dumps(resp).encode("utf-8")

    yt_publisher = YouTubePublisher(config=cfg, transport=mock_transport)
    engine = PublishingEngine(config=cfg, db=db)

    result = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        visibility="private",
        provider=yt_publisher,
        db_manager=db,
    )

    assert result.success is True
    assert result.status == PublishStatus.SUCCESS
    assert result.receipt.visibility == PublishVisibility.PRIVATE
    assert captured_metadata is not None
    assert captured_metadata["status"]["privacyStatus"] == "private"


def test_youtube_publish_unlisted_visibility_payload(tmp_path, monkeypatch):
    """Verifies that unlisted visibility is strictly encoded in the YouTube API payload."""
    job_id = "job-yt-unlisted-vis"
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    db, media_file, chk = create_sample_job_environment(tmp_path, job_id)

    cfg = Config(artifacts_dir=tmp_path / "artifacts", db_path=tmp_path / "artifacts" / "autopilot.db")
    cfg.youtube_access_token = "valid-access-token"

    captured_metadata = None

    def mock_transport(http_req):
        nonlocal captured_metadata
        if http_req.method == "POST":
            body = json.loads(http_req.data.decode("utf-8"))
            captured_metadata = body
            return 200, {"Location": "https://upload.youtube.com/session/sess-unlist-1"}, b""
        else:
            resp = {"id": "yt-vid-unlist-456", "status": {"privacyStatus": "unlisted"}}
            return 200, {}, json.dumps(resp).encode("utf-8")

    yt_publisher = YouTubePublisher(config=cfg, transport=mock_transport)
    engine = PublishingEngine(config=cfg, db=db)

    result = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        visibility="unlisted",
        provider=yt_publisher,
        db_manager=db,
    )

    assert result.success is True
    assert result.status == PublishStatus.SUCCESS
    assert result.receipt.visibility == PublishVisibility.UNLISTED
    assert captured_metadata is not None
    assert captured_metadata["status"]["privacyStatus"] == "unlisted"


# =========================================================================
# 3. MISSING OAUTH CREDENTIALS FAIL-CLOSED TESTS
# =========================================================================

def test_youtube_publish_fails_closed_without_credentials(tmp_path, monkeypatch):
    """Without YOUTUBE_ACCESS_TOKEN or YOUTUBE_TOKEN_PATH, real upload must fail closed."""
    job_id = "job-yt-no-auth"
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    db, media_file, chk = create_sample_job_environment(tmp_path, job_id)

    cfg = Config(
        project_root=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        db_path=tmp_path / "artifacts" / "autopilot.db",
    )
    cfg.youtube_access_token = None
    cfg.youtube_token_path = None

    yt_publisher = YouTubePublisher(config=cfg)
    # Check health check
    health = yt_publisher.health_check()
    assert health.healthy is False
    assert "YouTube credentials not configured" in health.error

    engine = PublishingEngine(config=cfg, db=db)
    result = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        visibility="private",
        dry_run=False,
        provider=yt_publisher,
        db_manager=db,
    )

    assert result.success is False
    assert result.status == PublishStatus.FAILED
    assert result.error.error_code == "AUTH_CREDENTIALS_MISSING"

    job_state = db.get_job(job_id)
    assert job_state["status"] == WorkflowState.FAILED_PUBLISH.value


# =========================================================================
# 4. IDEMPOTENT PUBLISH BEHAVIOR TESTS
# =========================================================================

def test_youtube_publish_idempotency_prevents_duplicate_upload(tmp_path, monkeypatch):
    """Rerunning publish on an already published job skips upload and returns existing receipt."""
    job_id = "job-yt-idempotency"
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    db, media_file, chk = create_sample_job_environment(tmp_path, job_id)

    cfg = Config(artifacts_dir=tmp_path / "artifacts", db_path=tmp_path / "artifacts" / "autopilot.db")
    cfg.youtube_access_token = "valid-access-token"

    upload_count = 0

    def mock_transport(http_req):
        nonlocal upload_count
        upload_count += 1
        if http_req.method == "POST":
            return 200, {"Location": "https://upload.youtube.com/session/sess-idem"}, b""
        return 200, {}, json.dumps({"id": "yt-vid-idem-789"}).encode("utf-8")

    yt_publisher = YouTubePublisher(config=cfg, transport=mock_transport)
    engine = PublishingEngine(config=cfg, db=db)

    # First run -> Executes upload (2 HTTP calls)
    res1 = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        visibility="private",
        provider=yt_publisher,
        db_manager=db,
    )
    assert res1.success is True
    assert res1.status == PublishStatus.SUCCESS
    assert upload_count == 2
    orig_receipt = res1.receipt

    # Second run -> Hits idempotency cache, zero new HTTP calls
    res2 = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        visibility="private",
        provider=yt_publisher,
        db_manager=db,
    )
    assert res2.success is True
    assert res2.status == PublishStatus.SKIPPED_DUPLICATE
    assert upload_count == 2  # No new network requests
    assert res2.receipt.remote_video_id == orig_receipt.remote_video_id
    assert res2.receipt.idempotency_key == orig_receipt.idempotency_key

    # Force retry -> Bypasses cache and executes fresh upload
    res3 = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        visibility="private",
        force_retry=True,
        provider=yt_publisher,
        db_manager=db,
    )
    assert res3.success is True
    assert res3.status == PublishStatus.SUCCESS
    assert upload_count == 4  # 2 additional calls executed


# =========================================================================
# 5. SUCCESSFUL PUBLISHER RESULT PERSISTENCE TESTS
# =========================================================================

def test_youtube_publish_result_persistence(tmp_path, monkeypatch):
    """Verifies complete state machine, DB records, and artifact file persistence."""
    job_id = "job-yt-persistence"
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    db, media_file, chk = create_sample_job_environment(tmp_path, job_id)

    cfg = Config(artifacts_dir=tmp_path / "artifacts", db_path=tmp_path / "artifacts" / "autopilot.db")
    cfg.youtube_access_token = "valid-access-token"

    def mock_transport(http_req):
        if http_req.method == "POST":
            return 200, {"Location": "https://upload.youtube.com/session/sess-persist"}, b""
        return 200, {}, json.dumps({"id": "yt-vid-persist-999"}).encode("utf-8")

    yt_publisher = YouTubePublisher(config=cfg, transport=mock_transport)
    engine = PublishingEngine(config=cfg, db=db)

    result = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        visibility="private",
        provider=yt_publisher,
        db_manager=db,
    )

    assert result.success is True
    assert result.status == PublishStatus.SUCCESS
    assert result.receipt.remote_video_id == "yt-vid-persist-999"
    assert result.receipt.remote_url == "https://youtu.be/yt-vid-persist-999"

    # Verify SQLite DB state
    job_row = db.get_job(job_id)
    assert job_row["status"] == WorkflowState.PUBLISHED.value

    # Verify publication DB record
    pub_record = db.get_publication_by_idempotency(result.receipt.idempotency_key)
    assert pub_record is not None
    assert pub_record["remote_video_id"] == "yt-vid-persist-999"
    assert pub_record["platform"] == "youtube"

    # Verify publish attempts recorded in DB
    attempts = db.get_publish_attempts_for_job(job_id)
    assert len(attempts) >= 1
    assert attempts[0]["status"] == "SUCCESS"

    # Verify Artifact files on disk
    pub_dir = tmp_path / "artifacts" / "jobs" / job_id / "publish"
    assert (pub_dir / "request.json").exists()
    assert (pub_dir / "result.json").exists()
    assert (pub_dir / "receipt.json").exists()

    receipt_json = json.loads((pub_dir / "receipt.json").read_text(encoding="utf-8"))
    assert receipt_json["remote_video_id"] == "yt-vid-persist-999"
    assert receipt_json["remote_url"] == "https://youtu.be/yt-vid-persist-999"
    assert receipt_json["visibility"] == "private"
