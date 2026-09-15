"""CLI integration tests for Analytics & Performance Intelligence — Milestone 8."""
import json
import pytest
from pathlib import Path

from autopilot.cli.main import (
    run_analytics_sync,
    run_analytics_show,
    run_analytics_report,
    run_health,
)
from autopilot.core.config import CONFIG
from autopilot.db.manager import DBManager


@pytest.fixture
def test_env(tmp_path, monkeypatch):
    """Set up temporary database and environment for CLI analytics tests."""
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(CONFIG, "db_path", db_file)
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    db = DBManager(db_file)
    db.init_schema()

    job_id = "cli-analytics-job-001"
    db.create_job(job_id=job_id, topic="Quantum Computing Explained")

    from autopilot.core.contracts import PublicationReceipt, PublishPlatform
    rcpt = PublicationReceipt(
        receipt_id=f"rcpt-{job_id}",
        job_id=job_id,
        content_id=job_id,
        render_checksum_sha256="cli-test-render-sha256",
        platform=PublishPlatform.YOUTUBE,
        remote_video_id="yt-vid-cli-123",
        remote_url="https://youtu.be/yt-vid-cli-123",
        metadata_hash="cli-meta-hash",
        idempotency_key=f"idemp-{job_id}",
    )
    db.record_publication(rcpt)

    return {"db": db, "job_id": job_id, "remote_id": "yt-vid-cli-123"}


def test_cli_analytics_sync_dry_run(test_env, capsys):
    job_id = test_env["job_id"]
    code = run_analytics_sync(job_id=job_id, dry_run=True, output_json=False)
    assert code == 0
    captured = capsys.readouterr()
    assert f"ANALYTICS SYNC [{job_id}]" in captured.out
    assert "Dry Run:      True" in captured.out


def parse_cli_json(output: str):
    """Filter out StructuredLogger json lines and parse the remaining CLI output."""
    content_lines = []
    for line in output.strip().splitlines():
        try:
            d = json.loads(line)
            if isinstance(d, dict) and "stage" in d and "event" in d:
                continue
        except Exception:
            pass
        content_lines.append(line)
    return json.loads("\n".join(content_lines))


def test_cli_analytics_sync_json(test_env, capsys):
    job_id = test_env["job_id"]
    code = run_analytics_sync(job_id=job_id, dry_run=True, output_json=True)
    assert code == 0
    captured = capsys.readouterr()
    parsed = parse_cli_json(captured.out)
    assert parsed["job_id"] == job_id
    assert parsed["status"] == "simulated"
    assert parsed["dry_run"] is True


def test_cli_analytics_sync_execute_and_show(test_env, capsys):
    job_id = test_env["job_id"]
    # Execute actual mock sync
    code = run_analytics_sync(job_id=job_id, dry_run=False, output_json=False)
    assert code == 0
    captured = capsys.readouterr()
    assert "Snapshot ID:" in captured.out
    assert "views:" in captured.out

    # Show performance details
    show_code = run_analytics_show(job_id=job_id, output_json=False)
    assert show_code == 0
    captured_show = capsys.readouterr()
    assert f"CONTENT PERFORMANCE [{job_id}]" in captured_show.out
    assert "Synthetic/Test" in captured_show.out
    assert "views" in captured_show.out


def test_cli_analytics_show_json(test_env, capsys):
    job_id = test_env["job_id"]
    # Run sync first
    run_analytics_sync(job_id=job_id, dry_run=False, output_json=False)
    capsys.readouterr()

    # Show JSON
    code = run_analytics_show(job_id=job_id, output_json=True)
    assert code == 0
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert parsed["job_id"] == job_id
    assert parsed["latest_snapshot"] is not None
    assert "views" in parsed["latest_snapshot"]["metrics"]


def test_cli_analytics_show_not_found(test_env, capsys):
    code = run_analytics_show(job_id="nonexistent-job-404", output_json=False)
    assert code == 1
    captured = capsys.readouterr()
    assert "Error: No content performance record found" in captured.err


def test_cli_analytics_report_text_and_json(test_env, capsys):
    job_id = test_env["job_id"]
    # Sync so data is available
    run_analytics_sync(job_id=job_id, dry_run=False, output_json=False)
    capsys.readouterr()

    # Report text
    code = run_analytics_report(output_json=False)
    assert code == 0
    captured = capsys.readouterr()
    assert "PERFORMANCE REPORT" in captured.out
    assert job_id in captured.out
    assert "SYNTHETIC" in captured.out

    # Report JSON
    code_json = run_analytics_report(output_json=True)
    assert code_json == 0
    captured_json = capsys.readouterr()
    parsed = json.loads(captured_json.out)
    assert isinstance(parsed, list)
    assert len(parsed) >= 1
    assert parsed[0]["job_id"] == job_id


def test_cli_analytics_sync_all_batch(test_env, capsys):
    # Batch sync all published jobs
    code = run_analytics_sync(sync_all_jobs=True, dry_run=False, output_json=False)
    assert code == 0
    captured = capsys.readouterr()
    assert "ANALYTICS BATCH SYNC" in captured.out
    assert "Synced Count:" in captured.out


def test_cli_health_reports_analytics_engine(capsys):
    code = run_health()
    assert code == 0
    captured = capsys.readouterr()
    assert "analytics_engine" in captured.out
    assert "AVAILABLE" in captured.out
