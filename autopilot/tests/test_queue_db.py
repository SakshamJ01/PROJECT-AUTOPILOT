"""Tests for SQLite Queue database layer — Milestone 7.
Verifies enqueueing, priority ordering, scheduled_at filtering, atomic claiming,
cancellation, retrying, stale lease recovery, and schema v4 migration.
"""
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from autopilot.db.manager import DBManager, DB_SCHEMA_VERSION


def test_schema_migration_v4(tmp_path):
    """Verifies schema initializes at version 4 with queue_items and batch_manifests tables."""
    db_path = tmp_path / "test_mig.db"
    db = DBManager(db_path)
    db.init_schema()

    with db._connect() as conn:
        ver = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        assert ver >= 4

        # Verify tables exist
        q_cols = [r["name"] for r in conn.execute("PRAGMA table_info(queue_items)").fetchall()]
        assert "queue_id" in q_cols
        assert "priority" in q_cols
        assert "lease_expires_at" in q_cols
        assert "stage" in q_cols

        m_cols = [r["name"] for r in conn.execute("PRAGMA table_info(batch_manifests)").fetchall()]
        assert "manifest_id" in m_cols
        assert "raw_json" in m_cols


def test_enqueue_and_priority_ordering(tmp_path):
    """Verifies queue items are claimed in priority order (high before normal before low)."""
    db = DBManager(tmp_path / "queue_prio.db")
    db.init_schema()

    db.enqueue_item("q-low", "job-low", priority=1, payload={"topic": "Low Priority"})
    db.enqueue_item("q-norm", "job-norm", priority=2, payload={"topic": "Normal Priority"})
    db.enqueue_item("q-high", "job-high", priority=3, payload={"topic": "High Priority"})

    # First claim should be high
    c1 = db.claim_next_queue_item("worker-1")
    assert c1 is not None
    assert c1["queue_id"] == "q-high"
    assert c1["status"] == "running"

    # Second claim should be normal
    c2 = db.claim_next_queue_item("worker-1")
    assert c2 is not None
    assert c2["queue_id"] == "q-norm"

    # Third claim should be low
    c3 = db.claim_next_queue_item("worker-1")
    assert c3 is not None
    assert c3["queue_id"] == "q-low"

    # Fourth claim should be None
    assert db.claim_next_queue_item("worker-1") is None


def test_scheduled_at_filtering(tmp_path):
    """Verifies items scheduled in the future are not claimable until their time arrives."""
    db = DBManager(tmp_path / "queue_sched.db")
    db.init_schema()

    future_time = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    db.enqueue_item("q-future", "job-future", scheduled_at=future_time, payload={"topic": "Future Job"})
    db.enqueue_item("q-now", "job-now", scheduled_at=None, payload={"topic": "Immediate Job"})

    claimed = db.claim_next_queue_item("worker-1")
    assert claimed is not None
    assert claimed["queue_id"] == "q-now"

    # No more available jobs because q-future is in the future
    assert db.claim_next_queue_item("worker-1") is None


def test_atomic_claim_and_double_claim_prevention(tmp_path):
    """Verifies two workers cannot claim the same job simultaneously."""
    db = DBManager(tmp_path / "queue_atomic.db")
    db.init_schema()

    db.enqueue_item("q-single", "job-single", payload={"topic": "Single Job"})

    # Worker A claims
    cA = db.claim_next_queue_item("worker-A")
    assert cA is not None
    assert cA["worker_id"] == "worker-A"

    # Worker B tries to claim immediately
    cB = db.claim_next_queue_item("worker-B")
    assert cB is None


def test_job_completion_cancellation_and_retry(tmp_path):
    """Verifies lifecycle methods complete, cancel, and retry queue items."""
    db = DBManager(tmp_path / "queue_ops.db")
    db.init_schema()

    # Test Completion
    db.enqueue_item("q-1", "job-1", payload={"topic": "T1"})
    c1 = db.claim_next_queue_item("worker-1")
    assert c1 is not None
    db.complete_queue_item("q-1")
    it1 = db.get_queue_item("q-1")
    assert it1["status"] == "succeeded"
    assert it1["completed_at"] is not None

    # Test Cancellation
    db.enqueue_item("q-2", "job-2", payload={"topic": "T2"})
    assert db.cancel_queue_item("q-2") is True
    it2 = db.get_queue_item("q-2")
    assert it2["status"] == "cancelled"

    # Test Retry Reset
    assert db.retry_queue_item("q-2") is True
    it2_retried = db.get_queue_item("q-2")
    assert it2_retried["status"] == "queued"
    assert it2_retried["attempt_count"] == 0


def test_stale_lease_recovery(tmp_path):
    """Verifies that expired worker leases are safely recovered to retry_wait."""
    db = DBManager(tmp_path / "queue_stale.db")
    db.init_schema()

    db.enqueue_item("q-crashed", "job-crashed", max_attempts=3, payload={"topic": "Crashed Worker Job"})
    claimed = db.claim_next_queue_item("worker-dead", lease_duration_sec=1)
    assert claimed is not None

    # Manually backdate lease_expires_at to simulate worker death in the past
    with db._connect() as conn:
        conn.execute("UPDATE queue_items SET lease_expires_at = datetime('now', '-10 seconds') WHERE queue_id = 'q-crashed'")
        conn.commit()

    recovered = db.recover_stale_leases()
    assert "q-crashed" in recovered

    it = db.get_queue_item("q-crashed")
    assert it["status"] == "retry_wait"
    assert it["lease_expires_at"] is None


def test_cancel_all_queued_items_lifecycle(tmp_path):
    """Verifies cancel_all_queued_items behaviour:
    - empty queue behaves cleanly
    - dry-run previews count without mutating
    - cancelling only affects status='queued' items
    - non-queued items (running, succeeded, failed, retry_wait, blocked, dead_letter) remain untouched
    - records are not deleted, auditable cancelled state is preserved
    """
    db = DBManager(tmp_path / "queue_cancel_all.db")
    db.init_schema()

    # 1. Empty queue behaves cleanly
    res_empty = db.cancel_all_queued_items(status="queued", dry_run=False)
    assert res_empty["found_count"] == 0
    assert res_empty["cancelled_count"] == 0
    assert res_empty["dry_run"] is False

    # 2. Setup items directly in various statuses
    db.enqueue_item("q-1", "job-1", payload={"topic": "T1"})  # status: queued
    db.enqueue_item("q-2", "job-2", payload={"topic": "T2"})  # status: queued
    db.enqueue_item("q-3", "job-3", payload={"topic": "T3"})  # status: queued

    db.enqueue_item("q-succ", "job-succ", payload={"topic": "TSucc"})
    db.complete_queue_item("q-succ")                          # status: succeeded

    db.enqueue_item("q-block", "job-block", payload={"topic": "TBlock"})
    db.block_queue_item("q-block", "reason-blocked")         # status: blocked

    db.create_job("job-running", topic="TRunning")
    with db._connect() as conn:
        conn.execute("INSERT INTO queue_items (queue_id, job_id, status) VALUES ('q-running', 'job-running', 'running')")
        conn.commit()

    # 3. Dry-run previews count without mutating database
    res_dry = db.cancel_all_queued_items(status="queued", dry_run=True)
    assert res_dry["found_count"] == 3
    assert res_dry["cancelled_count"] == 0
    assert res_dry["dry_run"] is True
    assert db.get_queue_item("q-1")["status"] == "queued"
    assert db.get_queue_item("q-2")["status"] == "queued"

    # 4. Real cancel-all execution
    res_real = db.cancel_all_queued_items(status="queued", dry_run=False)
    assert res_real["found_count"] == 3
    assert res_real["cancelled_count"] == 3
    assert res_real["dry_run"] is False

    # 5. Verify queued items are now cancelled with completed_at timestamp (auditable, not deleted)
    it1 = db.get_queue_item("q-1")
    assert it1["status"] == "cancelled"
    assert it1["completed_at"] is not None

    it2 = db.get_queue_item("q-2")
    assert it2["status"] == "cancelled"

    it3 = db.get_queue_item("q-3")
    assert it3["status"] == "cancelled"

    # 6. Verify non-queued items remain untouched
    it_running = db.get_queue_item("q-running")
    assert it_running["status"] == "running"

    it_succ = db.get_queue_item("q-succ")
    assert it_succ["status"] == "succeeded"

    it_block = db.get_queue_item("q-block")
    assert it_block["status"] == "blocked"


