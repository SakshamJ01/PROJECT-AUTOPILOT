"""Tests for Publishing Idempotency and Duplicate Protection — Milestone 6."""
import json
import pytest
from pathlib import Path

from autopilot.core.config import CONFIG
from autopilot.core.contracts import PublishStatus
from autopilot.core.publisher import PublishingEngine
from autopilot.providers.mock_publisher import MockPublisher
from autopilot.db.manager import DBManager


def setup_passing_job(tmp_path, job_id: str, content: bytes = b"MEDIA_DATA"):
    db = DBManager(tmp_path / "test.db")
    db.init_schema()
    db.create_job(job_id=job_id, topic=f"Topic for {job_id}")

    media_file = tmp_path / "artifacts" / "jobs" / job_id / "render" / "final.mp4"
    media_file.parent.mkdir(parents=True, exist_ok=True)
    media_file.write_bytes(content)
    import hashlib
    chk = hashlib.sha256(content).hexdigest()

    qa_dir = tmp_path / "artifacts" / "jobs" / job_id / "quality"
    qa_dir.mkdir(parents=True, exist_ok=True)
    qa_receipt = {
        "receipt_id": f"rcpt-qa-{job_id}",
        "job_id": job_id,
        "content_id": job_id,
        "status": "PASS",
        "publish_allowed": True,
        "media_path": str(media_file),
        "media_checksum_sha256": chk,
    }
    (qa_dir / "receipt.json").write_text(json.dumps(qa_receipt), encoding="utf-8")
    return db, media_file, chk


def test_idempotent_publish_skips_duplicate(tmp_path, monkeypatch):
    job_id = "test-idem-001"
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    db, media_file, chk = setup_passing_job(tmp_path, job_id)

    engine = PublishingEngine(CONFIG)
    mock_prov = MockPublisher()

    # First publication
    res1 = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        dry_run=False,
        provider=mock_prov,
        db_manager=db,
    )
    assert res1.success is True
    assert res1.status == PublishStatus.SUCCESS
    orig_receipt = res1.receipt

    # Second publication with identical parameters
    res2 = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        dry_run=False,
        provider=mock_prov,
        db_manager=db,
    )
    assert res2.success is True
    assert res2.status == PublishStatus.SKIPPED_DUPLICATE
    assert res2.receipt.receipt_id == orig_receipt.receipt_id
    assert res2.receipt.remote_video_id == orig_receipt.remote_video_id


def test_force_retry_bypasses_idempotency_cache(tmp_path, monkeypatch):
    job_id = "test-idem-force-retry"
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    db, media_file, chk = setup_passing_job(tmp_path, job_id)

    engine = PublishingEngine(CONFIG)
    mock_prov = MockPublisher()

    res1 = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        dry_run=False,
        provider=mock_prov,
        db_manager=db,
    )
    assert res1.status == PublishStatus.SUCCESS

    # Force retry initiates new execution
    res2 = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        dry_run=False,
        force_retry=True,
        provider=mock_prov,
        db_manager=db,
    )
    assert res2.status == PublishStatus.SUCCESS


def test_different_visibility_creates_distinct_publication(tmp_path, monkeypatch):
    job_id = "test-idem-vis"
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    db, media_file, chk = setup_passing_job(tmp_path, job_id)

    engine = PublishingEngine(CONFIG)
    mock_prov = MockPublisher()

    res_priv = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        visibility="private",
        dry_run=False,
        provider=mock_prov,
        db_manager=db,
    )
    assert res_priv.receipt.visibility.value == "private"

    res_pub = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        visibility="public",
        dry_run=False,
        provider=mock_prov,
        db_manager=db,
    )
    assert res_pub.receipt.visibility.value == "public"
    assert res_priv.receipt.idempotency_key != res_pub.receipt.idempotency_key


def test_idempotent_publish_skips_when_receipt_is_corrupt(tmp_path, monkeypatch):
    """P0-02: A matched idempotency key with an unreadable receipt must NOT silently
    re-publish — it must be treated as a soft duplicate to prevent duplicate uploads."""
    job_id = "test-idem-corrupt"
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    db, media_file, chk = setup_passing_job(tmp_path, job_id)

    engine = PublishingEngine(CONFIG)
    mock_prov = MockPublisher()

    # First publication creates the idempotency record
    res1 = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        dry_run=False,
        provider=mock_prov,
        db_manager=db,
    )
    assert res1.status == PublishStatus.SUCCESS
    key = res1.receipt.idempotency_key

    # Corrupt the receipt_json in the publish_records table
    with db._connect() as conn:
        conn.execute("UPDATE publish_records SET receipt_json = '{not-valid-json' WHERE idempotency_key = ?", (key,))
        conn.commit()

    res2 = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        dry_run=False,
        provider=mock_prov,
        db_manager=db,
    )
    assert res2.status == PublishStatus.SKIPPED_DUPLICATE
    assert res2.error is not None
    assert res2.error.error_code == "RECEIPT_DESERIALIZATION"

    # Re-publishing was prevented: record still holds the corrupted receipt untouched
    with db._connect() as conn:
        row = conn.execute("SELECT receipt_json FROM publish_records WHERE idempotency_key = ?", (key,)).fetchone()
    assert row["receipt_json"] == "{not-valid-json"
