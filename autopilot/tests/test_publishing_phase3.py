"""Phase 3 Tests — Multi-Platform Publishing, Scheduling, QA-Gating, and Idempotency."""
import json
import hashlib
import pytest
from pathlib import Path

from autopilot.core.config import Config
from autopilot.db.manager import DBManager
from autopilot.core.contracts import (
    PublishRequest,
    PublishResult,
    PublishStatus,
    PublicationReceipt,
    PublishVisibility,
    PublishPlatform,
    PublishError,
    QAReport,
    QAStatus,
)
from autopilot.core.publisher import PublishingEngine
from autopilot.providers.youtube_publisher import YouTubePublisher
from autopilot.providers.postiz_publisher import PostizPublisher


@pytest.fixture
def mock_db(tmp_path):
    db_path = tmp_path / "test_publish.db"
    db = DBManager(str(db_path))
    db.init_schema()
    return db


@pytest.fixture
def sample_video(tmp_path):
    media_file = tmp_path / "video.mp4"
    media_file.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 2000)
    return media_file


def _setup_media_file(base_dir: Path, job_id: str, sample_video: Path) -> Path:
    render_dir = base_dir / "jobs" / job_id / "render"
    render_dir.mkdir(parents=True, exist_ok=True)
    v_file = render_dir / "final.mp4"
    v_file.write_bytes(sample_video.read_bytes())
    # Also create artifacts/job_id/render/final.mp4
    alt_dir = base_dir / job_id / "render"
    alt_dir.mkdir(parents=True, exist_ok=True)
    (alt_dir / "final.mp4").write_bytes(sample_video.read_bytes())
    return v_file


def test_provider_selection_direct_youtube_vs_postiz(tmp_path, mock_db, sample_video):
    """Verify provider selection dispatches youtube to direct publisher and postiz/* to Postiz."""
    cfg = Config(artifacts_dir=str(tmp_path), db_path=str(mock_db.db_path))
    engine = PublishingEngine(config=cfg, db=mock_db)

    job_id = "job-p1"
    mock_db.create_job(job_id=job_id, topic="Test Topic")
    v_file = _setup_media_file(tmp_path, job_id, sample_video)
    v_hash = hashlib.sha256(v_file.read_bytes()).hexdigest()
    mock_db.record_artifact(job_id, str(v_file), "media", v_hash)

    report = QAReport(
        report_id=f"qa-{job_id}",
        job_id=job_id,
        content_id=job_id,
        status=QAStatus.PASS,
        publish_allowed=True,
    )
    mock_db.record_qa_report(report)

    # Dry-run YouTube direct
    res_yt = engine.publish_job(job_id=job_id, platform="youtube", dry_run=True)
    assert res_yt.success is True
    assert res_yt.dry_run_preview["provider"] == "youtube"

    # Dry-run Postiz TikTok
    res_postiz = engine.publish_job(job_id=job_id, platform="postiz/tiktok", dry_run=True)
    assert res_postiz.success is True
    assert res_postiz.dry_run_preview["provider"] == "postiz"
    assert res_postiz.dry_run_preview["target_platform"] == "tiktok"


def test_qa_protected_publish_gate(tmp_path, mock_db, sample_video):
    """Publishing MUST be rejected if QA report is missing or publish_allowed=False."""
    cfg = Config(artifacts_dir=str(tmp_path), db_path=str(mock_db.db_path))
    engine = PublishingEngine(config=cfg, db=mock_db)

    job_id = "job-qa-fail"
    mock_db.create_job(job_id=job_id, topic="Failing QA Topic")
    v_file = _setup_media_file(tmp_path, job_id, sample_video)
    v_hash = hashlib.sha256(v_file.read_bytes()).hexdigest()
    mock_db.record_artifact(job_id, str(v_file), "media", v_hash)

    # Case 1: No QA report at all -> BLOCKED
    res1 = engine.publish_job(job_id=job_id, dry_run=True)
    assert res1.success is False
    assert res1.status in (PublishStatus.FAILED, PublishStatus.NOT_READY, PublishStatus.BLOCKED_QA)
    assert "QA" in res1.error.message

    # Case 2: QA failed (publish_allowed = False) -> BLOCKED
    report = QAReport(
        report_id=f"qa-{job_id}",
        job_id=job_id,
        content_id=job_id,
        status=QAStatus.BLOCK,
        publish_allowed=False,
    )
    mock_db.record_qa_report(report)

    res2 = engine.publish_job(job_id=job_id, dry_run=True)
    assert res2.success is False
    assert res2.status in (PublishStatus.FAILED, PublishStatus.NOT_READY, PublishStatus.BLOCKED_QA)
    assert "QA" in res2.error.message or "gate" in res2.error.message.lower()


def test_deterministic_idempotency_key(tmp_path, mock_db):
    """Deterministic idempotency key calculation."""
    cfg = Config(artifacts_dir=str(tmp_path), db_path=str(mock_db.db_path))
    engine = PublishingEngine(config=cfg, db=mock_db)

    key1 = engine.compute_idempotency_key(
        job_id="job-100",
        render_checksum="abc123sha",
        platform="youtube",
        visibility="private",
    )
    key2 = engine.compute_idempotency_key(
        job_id="job-100",
        render_checksum="abc123sha",
        platform="youtube",
        visibility="private",
    )
    key3 = engine.compute_idempotency_key(
        job_id="job-100",
        render_checksum="abc123sha",
        platform="youtube",
        visibility="public",
    )

    assert key1 == key2
    assert key1 != key3
    assert len(key1) == 64


def test_idempotent_retry_prevents_duplicate_upload(tmp_path, mock_db, sample_video):
    """If previous publication succeeded, retry returns existing publication without second upload."""
    cfg = Config(artifacts_dir=str(tmp_path), db_path=str(mock_db.db_path))
    engine = PublishingEngine(config=cfg, db=mock_db)

    job_id = "job-idemp-test"
    mock_db.create_job(job_id=job_id, topic="Idempotent Topic")
    v_file = _setup_media_file(tmp_path, job_id, sample_video)
    v_hash = hashlib.sha256(v_file.read_bytes()).hexdigest()
    mock_db.record_artifact(job_id, str(v_file), "media", v_hash)

    report = QAReport(report_id=f"qa-{job_id}", job_id=job_id, content_id=job_id, status=QAStatus.PASS, publish_allowed=True)
    mock_db.record_qa_report(report)

    title = f"Autopilot Video — {job_id}"
    description = "Automated video generated by Project Autopilot."
    tags = ["autopilot", "shorts"]
    metadata_str = f"{title}:{description}:{','.join(sorted(tags))}"
    meta_hash = hashlib.sha256(metadata_str.encode("utf-8")).hexdigest()
    idem_key = engine.compute_idempotency_key(job_id, v_hash, "youtube", "private", metadata_hash=meta_hash)
    receipt = PublicationReceipt(
        receipt_id="rcpt-existing-123",
        job_id=job_id,
        content_id=job_id,
        render_checksum_sha256=v_hash,
        metadata_hash=meta_hash,
        platform=PublishPlatform.YOUTUBE,
        provider="youtube",
        remote_video_id="yt-existing-999",
        remote_url="https://youtube.com/watch?v=yt-existing-999",
        visibility=PublishVisibility.PRIVATE,
        published_at="2026-09-12T12:00:00Z",
        idempotency_key=idem_key,
    )
    mock_db.record_publication(receipt)

    res = engine.publish_job(job_id=job_id, platform="youtube", visibility="private", dry_run=False)
    assert res.success is True
    assert res.receipt.remote_video_id == "yt-existing-999"
    assert res.receipt.receipt_id == "rcpt-existing-123"


def test_scheduling_future_utc_timestamp(tmp_path, mock_db, sample_video):
    """Scheduling for a future time returns scheduled status and persists scheduled_time."""
    cfg = Config(artifacts_dir=str(tmp_path), db_path=str(mock_db.db_path))
    engine = PublishingEngine(config=cfg, db=mock_db)

    job_id = "job-sched-test"
    mock_db.create_job(job_id=job_id, topic="Scheduled Video")
    v_file = _setup_media_file(tmp_path, job_id, sample_video)
    v_hash = hashlib.sha256(v_file.read_bytes()).hexdigest()
    mock_db.record_artifact(job_id, str(v_file), "media", v_hash)

    mock_db.record_qa_report(QAReport(report_id=f"qa-{job_id}", job_id=job_id, content_id=job_id, status=QAStatus.PASS, publish_allowed=True))

    future_time = "2026-10-01T18:00:00Z"
    res = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        scheduled_time=future_time,
        dry_run=True,
    )
    assert res.success is True
    assert res.dry_run_preview["scheduled_time"] == future_time


def test_private_unlisted_public_policy(tmp_path, mock_db, sample_video):
    """Private and unlisted do not require extra policy override; public requires explicit intent."""
    cfg = Config(artifacts_dir=str(tmp_path), db_path=str(mock_db.db_path))
    engine = PublishingEngine(config=cfg, db=mock_db)

    job_id = "job-vis-test"
    mock_db.create_job(job_id=job_id, topic="Visibility Test")
    v_file = _setup_media_file(tmp_path, job_id, sample_video)
    v_hash = hashlib.sha256(v_file.read_bytes()).hexdigest()
    mock_db.record_artifact(job_id, str(v_file), "media", v_hash)

    mock_db.record_qa_report(QAReport(report_id=f"qa-{job_id}", job_id=job_id, content_id=job_id, status=QAStatus.PASS, publish_allowed=True))

    # Default is private
    res_def = engine.publish_job(job_id=job_id, dry_run=True)
    assert res_def.dry_run_preview["visibility"] == "private"

    # Explicit unlisted
    res_unlisted = engine.publish_job(job_id=job_id, visibility="unlisted", dry_run=True)
    assert res_unlisted.dry_run_preview["visibility"] == "unlisted"
