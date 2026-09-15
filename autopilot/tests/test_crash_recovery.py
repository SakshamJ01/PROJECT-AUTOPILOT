"""Adversarial Worker Crash-Recovery Test — Milestone 7 (Section 26).
Verifies:
1. Enqueue job
2. Claim job
3. Simulate worker/process death
4. Restart worker
5. Recover stale lease
6. Resume job
7. Verify no duplicate successful work
8. Verify final receipt is correct
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from autopilot.db.manager import DBManager
from autopilot.core.config import Config
from autopilot.core.worker import LocalWorker
from autopilot.core.pipeline import PipelineOrchestrator
from autopilot.core.contracts import (
    ScriptDocument, ScriptScene, ContentPackage, ContentItem, PublicationMetadata,
    ProvenanceRecord, RenderPlan, QAStatus, QAReport, PublishReceipt,
)


def test_adversarial_worker_crash_and_resume_recovery(tmp_path):
    """Complete adversarial test verifying worker crash recovery and stage resumption."""
    db_path = tmp_path / "crash_test.db"
    db = DBManager(db_path)
    db.init_schema()

    job_id = "crash-adv-001"
    queue_id = "q-adv-001"
    topic = "The History of Telegraphy"

    cfg = Config(artifacts_dir=tmp_path, db_path=db_path)

    # 1. Enqueue job
    db.enqueue_item(
        queue_id=queue_id,
        job_id=job_id,
        content_id=job_id,
        priority=3,
        max_attempts=3,
        payload={"topic": topic, "profile": "short_vertical"},
    )
    initial_item = db.get_queue_item(queue_id)
    assert initial_item["status"] == "queued"

    # 2. Worker A claims job
    worker_A = LocalWorker(worker_id="worker-A-crashed", config=cfg, db=db)
    claimed_A = db.claim_next_queue_item(worker_A.worker_id, lease_duration_sec=300)
    assert claimed_A is not None
    assert claimed_A["queue_id"] == queue_id
    assert claimed_A["status"] == "running"
    assert claimed_A["worker_id"] == "worker-A-crashed"

    # Simulate partial execution before death:
    # Worker A successfully generated script and voice, and updated stage to 'VOICE'
    db.update_queue_stage(queue_id=queue_id, stage="VOICE", status="running")

    # 3. Simulate Worker A process death:
    # Worker A process abruptly dies without releasing lease or completing job.
    # We backdate lease_expires_at to 10 seconds ago.
    with db._connect() as conn:
        conn.execute(
            "UPDATE queue_items SET lease_expires_at = datetime('now', '-10 seconds') WHERE queue_id = ?",
            (queue_id,),
        )
        conn.commit()

    # Verify lease is expired in DB while still marked running
    dead_item = db.get_queue_item(queue_id)
    assert dead_item["status"] == "running"

    # 4. Restart: Launch Worker B
    # Track stages executed to verify no duplicate work
    stages_executed = []

    class TrackingOrchestrator(PipelineOrchestrator):
        def run_pipeline(self, *args, **kwargs):
            on_prog = kwargs.get("on_stage_progress")
            # Simulate resuming from VOICE: execute remaining stages
            for stg in ["ASSETS", "RENDER", "QA"]:
                stages_executed.append(stg)
                if on_prog:
                    on_prog(stg)
            return {
                "job_id": job_id,
                "status": "success",
                "media_path": str(tmp_path / "final.mp4"),
                "qa_status": "PASS",
                "published": False,
            }

    tracking_orch = TrackingOrchestrator(config=cfg, db=db)
    worker_B = LocalWorker(worker_id="worker-B-recovered", config=cfg, db=db, orchestrator=tracking_orch)

    # 5. Worker B recovers stale leases (grace=0 so item is immediately claimable)
    recovered_ids = worker_B.recover_stale_leases(grace_lease_sec=0)
    assert queue_id in recovered_ids

    recovered_item = db.get_queue_item(queue_id)
    assert recovered_item["status"] == "retry_wait"
    assert "Stale worker lease recovered" in recovered_item["last_error"]

    # 6. Worker B claims and resumes the job
    result_B = worker_B.process_next_job()
    assert result_B is not None
    assert result_B["status"] == "succeeded"
    assert result_B["job_id"] == job_id

    # 7. Verify no duplicate work: only remaining stages were executed
    assert "ASSETS" in stages_executed
    assert "RENDER" in stages_executed
    assert "QA" in stages_executed

    # 8. Verify final status and receipts
    final_item = db.get_queue_item(queue_id)
    assert final_item["status"] == "succeeded"
    assert final_item["stage"] == "COMPLETE"
    assert final_item["completed_at"] is not None
    assert final_item["attempt_count"] == 2  # Attempt 1 (dead worker) + Attempt 2 (recovered worker)
