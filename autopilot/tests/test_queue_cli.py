"""CLI tests for Batch and Queue subcommands — Milestone 7.
Verifies CLI handlers for batch submit, queue status, queue list,
queue inspect, queue retry, queue cancel, and health command integration.
"""
import json
import pytest
from pathlib import Path

from autopilot.cli.main import (
    run_batch_submit, run_queue_status, run_queue_list,
    run_queue_inspect, run_queue_retry, run_queue_cancel, run_queue_cancel_all, run_health,
)
from autopilot.db.manager import DBManager
from autopilot.core.config import CONFIG


def test_cli_batch_submit_and_status(tmp_path, capsys, monkeypatch):
    """Tests CLI batch submit and queue status commands."""
    db_file = tmp_path / "cli_sub.db"
    monkeypatch.setattr(CONFIG, "db_path", db_file)

    manifest_data = {
        "profile": "short_vertical",
        "items": [
            {"topic": "CLI Topic One"},
            {"topic": "CLI Topic Two"},
        ],
    }
    manifest_file = tmp_path / "cli_batch.json"
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")

    # Dry run
    code_dry = run_batch_submit(str(manifest_file), dry_run=True)
    assert code_dry == 0
    captured_dry = capsys.readouterr().out
    assert "BATCH SUBMISSION (DRY RUN)" in captured_dry

    # Real submit
    code_real = run_batch_submit(str(manifest_file), dry_run=False, output_json=True)
    assert code_real == 0
    captured_real = capsys.readouterr().out
    data = json.loads(captured_real)
    assert data["submitted_count"] == 2

    # Queue status
    code_status = run_queue_status(output_json=True)
    assert code_status == 0
    captured_status = capsys.readouterr().out
    status_data = json.loads(captured_status)
    assert status_data["queued"] == 2


def test_cli_queue_list_and_inspect(tmp_path, capsys, monkeypatch):
    """Tests CLI queue list and inspect commands."""
    db_file = tmp_path / "cli_insp.db"
    monkeypatch.setattr(CONFIG, "db_path", db_file)

    db = DBManager(db_file)
    db.init_schema()
    db.enqueue_item("q-cli-test", "job-cli-test", payload={"topic": "CLI Inspect Topic"})

    code_list = run_queue_list(limit=5)
    assert code_list == 0
    captured_list = capsys.readouterr().out
    assert "QUEUE ITEMS" in captured_list

    code_insp = run_queue_inspect("job-cli-test")
    assert code_insp == 0
    captured_insp = capsys.readouterr().out
    assert "QUEUE ITEM INSPECT" in captured_insp
    assert "job-cli-test" in captured_insp


def test_cli_queue_cancel_and_retry(tmp_path, capsys, monkeypatch):
    """Tests CLI queue cancel and retry commands."""
    db_file = tmp_path / "cli_canc.db"
    monkeypatch.setattr(CONFIG, "db_path", db_file)

    db = DBManager(db_file)
    db.init_schema()
    db.enqueue_item("q-cli-cancel", "job-cli-cancel", payload={"topic": "Cancel Topic"})

    # Cancel
    code_cancel = run_queue_cancel("job-cli-cancel")
    assert code_cancel == 0
    item = db.get_queue_item("q-cli-cancel")
    assert item["status"] == "cancelled"

    # Retry
    code_retry = run_queue_retry("job-cli-cancel")
    assert code_retry == 0
    item_retried = db.get_queue_item("q-cli-cancel")
    assert item_retried["status"] == "queued"


def test_cli_health_includes_queue_engine(capsys):
    """Tests that health command includes Queue Engine, Worker, and Scheduler."""
    code = run_health()
    assert code == 0
    captured = capsys.readouterr().out
    assert "Queue Engine: AVAILABLE" in captured
    assert "Worker: AVAILABLE" in captured
    assert "Scheduler: AVAILABLE" in captured


def test_cli_queue_cancel_all(tmp_path, capsys, monkeypatch):
    """Tests CLI queue cancel-all command with dry-run and real execution."""
    db_file = tmp_path / "cli_cancel_all.db"
    monkeypatch.setattr(CONFIG, "db_path", db_file)

    db = DBManager(db_file)
    db.init_schema()
    db.enqueue_item("q-ca-1", "job-ca-1", payload={"topic": "T1"})
    db.enqueue_item("q-ca-2", "job-ca-2", payload={"topic": "T2"})

    # Dry-run execution
    code_dry = run_queue_cancel_all(status="queued", dry_run=True, output_json=True)
    assert code_dry == 0
    data_dry = json.loads(capsys.readouterr().out)
    assert data_dry["found_count"] == 2
    assert data_dry["cancelled_count"] == 0
    assert data_dry["dry_run"] is True

    # Real execution
    code_real = run_queue_cancel_all(status="queued", dry_run=False, output_json=True)
    assert code_real == 0
    data_real = json.loads(capsys.readouterr().out)
    assert data_real["found_count"] == 2
    assert data_real["cancelled_count"] == 2
    assert data_real["dry_run"] is False

    assert db.get_queue_item("q-ca-1")["status"] == "cancelled"
    assert db.get_queue_item("q-ca-2")["status"] == "cancelled"

