"""Tests for LocalWorker process — Milestone 7.
Verifies worker execution loop, retry classification, exponential backoff scheduling,
and dead-letter terminal handling.
"""
import pytest
from unittest.mock import MagicMock
from pathlib import Path

from autopilot.db.manager import DBManager
from autopilot.core.config import Config
from autopilot.core.worker import LocalWorker
from autopilot.core.pipeline import PipelineOrchestrator, PipelineError


def test_worker_processes_successful_job(tmp_path):
    """Verifies that worker claims job, calls orchestrator, and marks succeeded."""
    db = DBManager(tmp_path / "worker_succ.db")
    db.init_schema()
    db.enqueue_item("q-1", "job-1", payload={"topic": "Test Worker Success"})

    mock_orch = MagicMock(spec=PipelineOrchestrator)
    mock_orch.run_pipeline.return_value = {
        "job_id": "job-1",
        "status": "success",
        "media_path": "/path/video.mp4",
        "qa_status": "PASS",
        "published": False,
    }

    worker = LocalWorker(worker_id="test-worker-1", db=db, orchestrator=mock_orch)
    res = worker.process_next_job()

    assert res is not None
    assert res["status"] == "succeeded"
    assert res["job_id"] == "job-1"

    item = db.get_queue_item("q-1")
    assert item["status"] == "succeeded"
    assert item["stage"] == "COMPLETE"
    assert item["completed_at"] is not None


def test_worker_handles_retryable_error_with_backoff(tmp_path):
    """Verifies retryable errors transition to retry_wait with exponential backoff timestamp."""
    db = DBManager(tmp_path / "worker_retry.db")
    db.init_schema()
    db.enqueue_item("q-ret", "job-ret", max_attempts=3, payload={"topic": "Retryable Task"})

    mock_orch = MagicMock(spec=PipelineOrchestrator)
    mock_orch.run_pipeline.side_effect = PipelineError("Network timeout", category="RETRYABLE", stage="VOICE")

    cfg = Config(artifacts_dir=tmp_path, queue_retry_backoff_base_seconds=5.0)
    worker = LocalWorker(worker_id="test-worker-ret", config=cfg, db=db, orchestrator=mock_orch)

    # Attempt 1
    res1 = worker.process_next_job()
    assert res1["status"] == "retry_wait"

    item1 = db.get_queue_item("q-ret")
    assert item1["status"] == "retry_wait"
    assert item1["attempt_count"] == 1
    assert item1["next_retry_at"] is not None
    assert "Network timeout" in item1["last_error"]


def test_worker_handles_non_retryable_error(tmp_path):
    """Verifies non-retryable errors transition immediately to failed without waiting for retries."""
    db = DBManager(tmp_path / "worker_nonret.db")
    db.init_schema()
    db.enqueue_item("q-fail", "job-fail", max_attempts=3, payload={"topic": "Bad Task"})

    mock_orch = MagicMock(spec=PipelineOrchestrator)
    mock_orch.run_pipeline.side_effect = PipelineError("Malformed schema", category="NON_RETRYABLE", stage="SCRIPT")

    worker = LocalWorker(worker_id="test-worker-fail", db=db, orchestrator=mock_orch)
    res = worker.process_next_job()

    assert res["status"] == "failed"
    item = db.get_queue_item("q-fail")
    assert item["status"] == "failed"
    assert item["completed_at"] is not None


def test_worker_handles_blocked_qa_error(tmp_path):
    """Verifies QA BLOCK transitions to blocked terminal state."""
    db = DBManager(tmp_path / "worker_block.db")
    db.init_schema()
    db.enqueue_item("q-blk", "job-blk", payload={"topic": "Blocked Task"})

    mock_orch = MagicMock(spec=PipelineOrchestrator)
    mock_orch.run_pipeline.side_effect = PipelineError("QA Gate Blocked: Container empty", category="BLOCKED", stage="QA")

    worker = LocalWorker(worker_id="test-worker-blk", db=db, orchestrator=mock_orch)
    res = worker.process_next_job()

    assert res["status"] == "blocked"
    item = db.get_queue_item("q-blk")
    assert item["status"] == "blocked"


def test_worker_exceeds_max_attempts_dead_letter(tmp_path):
    """Verifies that exceeding max_attempts moves job to dead_letter."""
    db = DBManager(tmp_path / "worker_dl.db")
    db.init_schema()
    db.enqueue_item("q-dl", "job-dl", max_attempts=1, payload={"topic": "Exhausted Task"})

    mock_orch = MagicMock(spec=PipelineOrchestrator)
    mock_orch.run_pipeline.side_effect = PipelineError("Transient error", category="RETRYABLE", stage="RENDER")

    worker = LocalWorker(worker_id="test-worker-dl", db=db, orchestrator=mock_orch)
    res = worker.process_next_job()

    assert res["status"] == "dead_letter"
    item = db.get_queue_item("q-dl")
    assert item["status"] == "dead_letter"
    assert item["completed_at"] is not None


def test_worker_run_once_mode(tmp_path):
    """Verifies worker with once=True processes all currently claimable jobs and exits cleanly."""
    db = DBManager(tmp_path / "worker_once.db")
    db.init_schema()
    db.enqueue_item("q-a", "job-a", payload={"topic": "Job A"})
    db.enqueue_item("q-b", "job-b", payload={"topic": "Job B"})

    mock_orch = MagicMock(spec=PipelineOrchestrator)
    mock_orch.run_pipeline.return_value = {"status": "success"}

    worker = LocalWorker(worker_id="test-worker-once", db=db, orchestrator=mock_orch)
    processed = worker.run(once=True)

    assert processed == 2
    assert db.get_queue_item("q-a")["status"] == "succeeded"
    assert db.get_queue_item("q-b")["status"] == "succeeded"
