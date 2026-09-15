"""CLI and End-to-End integration tests for Publishing — Milestone 6."""
import json
import pytest
from pathlib import Path

from autopilot.cli.main import run_publish, run_health
from autopilot.core.config import CONFIG
from autopilot.db.manager import DBManager


def test_cli_publish_dry_run_success(tmp_path, monkeypatch, capsys):
    job_id = "cli-test-job-001"
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    db = DBManager(tmp_path / "test.db")
    db.init_schema()
    db.create_job(job_id=job_id, topic="CLI Topic")

    media_file = tmp_path / "artifacts" / "jobs" / job_id / "render" / "final.mp4"
    media_file.parent.mkdir(parents=True, exist_ok=True)
    media_file.write_bytes(b"CLI_TEST_VIDEO_DATA")
    import hashlib
    chk = hashlib.sha256(b"CLI_TEST_VIDEO_DATA").hexdigest()

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

    code = run_publish(job_id=job_id, platform="youtube", dry_run=True, output_json=False)
    assert code == 0
    captured = capsys.readouterr()
    assert "M6 PUBLISHING ENGINE RESULT" in captured.out
    assert "DRY_RUN" in captured.out


def test_cli_publish_json_output(tmp_path, monkeypatch, capsys):
    job_id = "cli-test-json-001"
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    db = DBManager(tmp_path / "test.db")
    db.init_schema()
    db.create_job(job_id=job_id, topic="JSON Topic")

    media_file = tmp_path / "artifacts" / "jobs" / job_id / "render" / "final.mp4"
    media_file.parent.mkdir(parents=True, exist_ok=True)
    media_file.write_bytes(b"JSON_TEST_VIDEO_DATA")
    import hashlib
    chk = hashlib.sha256(b"JSON_TEST_VIDEO_DATA").hexdigest()

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

    code = run_publish(job_id=job_id, platform="youtube", dry_run=True, output_json=True)
    assert code == 0
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert parsed["success"] is True
    assert parsed["status"] == "DRY_RUN"
    assert parsed["receipt"]["remote_video_id"] == "DRY_RUN_VIDEO_ID"


def test_cli_publish_missing_job_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    code = run_publish(job_id="totally-nonexistent-job-xyz", platform="youtube")
    assert code == 1


def test_cli_health_reports_publisher_status(capsys):
    code = run_health()
    assert code == 0
    captured = capsys.readouterr()
    assert "youtube:" in captured.out
    assert "Publishers:" in captured.out
