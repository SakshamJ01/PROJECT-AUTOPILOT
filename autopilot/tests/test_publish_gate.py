"""Integration tests for the Strict QA Publishing Gate — Milestone 6."""
import json
import pytest
from pathlib import Path

from autopilot.core.config import CONFIG
from autopilot.core.contracts import QAStatus, PublishStatus
from autopilot.core.publisher import PublishingEngine
from autopilot.providers.mock_publisher import MockPublisher
from autopilot.db.manager import DBManager


def create_test_media(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"TEST_VIDEO_PAYLOAD_FOR_M6_PUBLISH_GATE")
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_publish_gate_allows_qa_pass(tmp_path, monkeypatch):
    job_id = "test-gate-pass-001"
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    db = DBManager(tmp_path / "test.db")
    db.init_schema()
    db.create_job(job_id=job_id, topic="Gate Pass Topic")

    media_file = tmp_path / "artifacts" / "jobs" / job_id / "render" / "final.mp4"
    chk = create_test_media(media_file)

    # Write passing QA receipt
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

    engine = PublishingEngine(CONFIG)
    mock_prov = MockPublisher()
    result = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        dry_run=True,
        provider=mock_prov,
        db_manager=db,
    )

    assert result.success is True
    assert result.status == PublishStatus.DRY_RUN
    assert result.receipt is not None


def test_publish_gate_blocks_qa_block(tmp_path, monkeypatch):
    job_id = "test-gate-block-001"
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    db = DBManager(tmp_path / "test.db")
    db.init_schema()
    db.create_job(job_id=job_id, topic="Gate Block Topic")

    media_file = tmp_path / "artifacts" / "jobs" / job_id / "render" / "final.mp4"
    chk = create_test_media(media_file)

    # Write BLOCKING QA receipt
    qa_dir = tmp_path / "artifacts" / "jobs" / job_id / "quality"
    qa_dir.mkdir(parents=True, exist_ok=True)
    qa_receipt = {
        "receipt_id": f"rcpt-qa-{job_id}",
        "job_id": job_id,
        "content_id": job_id,
        "status": "BLOCK",
        "publish_allowed": False,
        "media_path": str(media_file),
        "media_checksum_sha256": chk,
    }
    (qa_dir / "receipt.json").write_text(json.dumps(qa_receipt), encoding="utf-8")

    engine = PublishingEngine(CONFIG)
    mock_prov = MockPublisher()
    result = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        dry_run=False,
        provider=mock_prov,
        db_manager=db,
    )

    assert result.success is False
    assert result.status == PublishStatus.BLOCKED_QA
    assert result.error is not None
    assert result.error.error_code == "QA_GATE_BLOCKED"
    # Ensure provider was never called
    job_row = db.get_job(job_id)
    assert job_row["status"] == "FAILED_PUBLISH"


def test_publish_gate_blocks_missing_qa_receipt(tmp_path, monkeypatch):
    job_id = "test-gate-missing-qa"
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    db = DBManager(tmp_path / "test.db")
    db.init_schema()
    db.create_job(job_id=job_id, topic="Missing QA Topic")

    media_file = tmp_path / "artifacts" / "jobs" / job_id / "render" / "final.mp4"
    create_test_media(media_file)

    engine = PublishingEngine(CONFIG)
    mock_prov = MockPublisher()
    result = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        provider=mock_prov,
        db_manager=db,
    )

    assert result.success is False
    assert result.status == PublishStatus.BLOCKED_QA
    assert result.error.error_code == "QA_REPORT_MISSING"


def test_publish_gate_blocks_checksum_mismatch(tmp_path, monkeypatch):
    job_id = "test-gate-checksum-mismatch"
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    db = DBManager(tmp_path / "test.db")
    db.init_schema()
    db.create_job(job_id=job_id, topic="Mismatch Topic")

    media_file = tmp_path / "artifacts" / "jobs" / job_id / "render" / "final.mp4"
    create_test_media(media_file)

    # QA recorded a different checksum
    qa_dir = tmp_path / "artifacts" / "jobs" / job_id / "quality"
    qa_dir.mkdir(parents=True, exist_ok=True)
    qa_receipt = {
        "receipt_id": f"rcpt-qa-{job_id}",
        "job_id": job_id,
        "content_id": job_id,
        "status": "PASS",
        "publish_allowed": True,
        "media_path": str(media_file),
        "media_checksum_sha256": "tampered_or_different_hash_value",
    }
    (qa_dir / "receipt.json").write_text(json.dumps(qa_receipt), encoding="utf-8")

    engine = PublishingEngine(CONFIG)
    mock_prov = MockPublisher()
    result = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        provider=mock_prov,
        db_manager=db,
    )

    assert result.success is False
    assert result.status == PublishStatus.BLOCKED_QA
    assert result.error.error_code == "CHECKSUM_MISMATCH"
