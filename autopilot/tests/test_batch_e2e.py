"""Deterministic End-to-End Batch Test — Milestone 7 (Section 27).
Executes a 3-job batch through the full production pipeline:
Manifest -> Queue -> Worker -> Research -> Script -> TTS -> Local Assets -> Render -> QA.
Completely offline, deterministic, zero external API spend.
"""
import json
import pytest
from pathlib import Path

from autopilot.db.manager import DBManager
from autopilot.core.config import Config
from autopilot.core.batch import BatchProcessor
from autopilot.core.worker import LocalWorker
from autopilot.core.pipeline import PipelineOrchestrator


def test_three_job_deterministic_batch_production(tmp_path):
    """End-to-end batch execution of 3 synthetic jobs through the complete pipeline."""
    db_path = tmp_path / "batch_e2e.db"
    db = DBManager(db_path)
    db.init_schema()

    cfg = Config(artifacts_dir=tmp_path, db_path=db_path, default_production_engine="ffmpeg")

    # 1. Create a 3-job batch manifest
    manifest_data = {
        "manifest_id": "mf-e2e-001",
        "profile": "short_vertical",
        "priority": "normal",
        "auto_publish": False,
        "items": [
            {
                "topic": "The Discovery of Penicillin",
                "priority": "high",
                # Pin the mock policy so the worker keeps deterministic mock providers
                # instead of resolving local_only -> live LLM/research/TTS services.
                "payload": {"policy": "mock"},
            },
            {
                "topic": "The History of Aviation",
                "priority": "normal",
                "payload": {"policy": "mock"},
            },
            {
                "topic": "The Origin of Printing Press",
                "priority": "low",
                "payload": {"policy": "mock"},
            },
        ],
    }
    manifest_path = tmp_path / "test_batch.json"
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

    # 2. Submit Batch to Queue
    batch_processor = BatchProcessor(config=cfg, db=db)
    manifest = batch_processor.parse_manifest_file(manifest_path)
    submit_res = batch_processor.submit_manifest(manifest)

    assert submit_res.total_items == 3
    assert submit_res.submitted_count == 3
    assert len(submit_res.queued_ids) == 3

    # 3. Instantiate Worker and run all queued jobs using --once mode
    worker = LocalWorker(worker_id="batch-worker-e2e", config=cfg, db=db)
    processed_count = worker.run(once=True)

    assert processed_count == 3

    # 4. Verify all 3 jobs completed successfully with valid QA receipts
    queue_items = db.list_queue_items()
    assert len(queue_items) == 3

    for q_item in queue_items:
        assert q_item["status"] == "succeeded"
        assert q_item["stage"] == "COMPLETE"
        assert q_item["completed_at"] is not None

        job_id = q_item["job_id"]
        job_row = db.get_job(job_id)
        assert job_row is not None
        assert job_row["status"] == "APPROVED"

        # Check rendered video exists and has non-zero size
        video_path = tmp_path / "jobs" / job_id / "render" / "final.mp4"
        assert video_path.exists()
        assert video_path.stat().st_size > 0

        # Check QA receipt exists and allowed publish
        receipt_path = tmp_path / "jobs" / job_id / "quality" / "receipt.json"
        assert receipt_path.exists()
        rcpt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
        assert rcpt_data.get("publish_allowed") is True
        assert rcpt_data.get("status") in ("PASS", "WARN")
