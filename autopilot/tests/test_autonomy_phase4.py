"""Phase 3 / Level 4 Guardrail Tests — "Guarded Auto-Produce".

Level 4 consumes eligible queue items through the existing real worker/pipeline
up to the terminal pre-publish state (APPROVED == READY_TO_PUBLISH) and never
invokes a public publisher, scheduling, or analytics-learning loop.

All deterministic pipeline runs pin ``policy: "mock"`` so the worker keeps mock
providers and uses the deterministic ffmpeg production engine (no network/API).
"""
import json
import pytest
from unittest.mock import MagicMock, patch

from autopilot.db.manager import DBManager
from autopilot.core.config import Config
from autopilot.core.worker import LocalWorker
from autopilot.core.pipeline import PipelineOrchestrator, PipelineError
from autopilot.core.autonomy import AutonomyEngine
from autopilot.core.channel import ChannelManager
from autopilot.core.contracts import (
    AutonomyPolicy,
    ChannelProfile,
    ChannelStatus,
    NicheConfig,
    AutoProduceSummary,
)


def _mk_engine(tmp_path, db, policy=None, config=None):
    cfg = config or Config(
        artifacts_dir=str(tmp_path),
        db_path=str(db.db_path),
        default_production_engine="ffmpeg",
    )
    return AutonomyEngine(config=cfg, db=db, policy=policy)


def _enqueue_auto(db, qid, job, topic, channel_id="default", **extra):
    """Enqueues an autonomy-origin item with pinned mock policy + evidence."""
    payload = {
        "topic": topic,
        "channel_id": channel_id,
        "profile": "short_vertical",
        "origin": "autonomous",
        "parent_signal_ids": ["sig-test-1"],
        "policy": "mock",
    }
    payload.update(extra)
    db.enqueue_item(queue_id=qid, job_id=job, content_id=job, channel_id=channel_id, payload=payload)
    return payload


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test_autonomy_phase4.db"
    d = DBManager(str(db_path))
    d.init_schema()
    return d


def _success_result(job_id="job-1", qa_status="PASS"):
    return {
        "job_id": job_id,
        "status": "success",
        "media_path": "/path/video.mp4",
        "qa_status": qa_status,
        "published": False,
    }


# ---------------------------------------------------------------------------
# TEST 1: Basic real-worker guarded production flow ends at READY_TO_PUBLISH.
# ---------------------------------------------------------------------------
def test_level4_basic_real_worker_flow(tmp_path, db):
    _enqueue_auto(db, "q-l4-1", "job-l4-1", "The Discovery of Penicillin")
    engine = _mk_engine(tmp_path, db)

    summary = engine.run_auto_produce_cycle(channel_id="default", limit=10, policy="mock")

    assert summary.status == "completed"
    assert summary.queued_jobs_discovered == 1
    assert summary.jobs_eligible == 1
    assert summary.jobs_producing == 1
    assert summary.jobs_completed == 1
    assert summary.jobs_ready_to_publish == 1
    assert summary.jobs_qa_failed == 0

    item = db.get_queue_item("q-l4-1")
    assert item["status"] == "succeeded"
    assert item["stage"] == "COMPLETE"

    job = db.get_job("job-l4-1")
    assert job is not None
    assert job["status"] == "APPROVED"

    # Terminal pre-publish boundary: nothing may be in a published/publishing state.
    for qi in db.list_queue_items():
        assert qi["status"] != "published"


# ---------------------------------------------------------------------------
# TEST 2: Level 4 forces auto_publish=False even when the payload asks to publish.
# ---------------------------------------------------------------------------
def test_level4_forces_auto_publish_false(tmp_path, db):
    _enqueue_auto(db, "q-l4-pub", "job-l4-pub", "Safe Topic", auto_publish=True)
    mock_orch = MagicMock(spec=PipelineOrchestrator)
    mock_orch.run_pipeline.return_value = _success_result(job_id="job-l4-pub")
    engine = _mk_engine(tmp_path, db)

    with patch("autopilot.core.publisher.PublishingEngine.publish_job") as mock_publish:
        summary = engine.run_auto_produce_cycle(
            channel_id="default", limit=10, policy="mock", orchestrator=mock_orch
        )

    call_kwargs = mock_orch.run_pipeline.call_args.kwargs
    assert call_kwargs["auto_publish"] is False
    mock_publish.assert_not_called()
    assert summary.jobs_ready_to_publish == 1
    assert summary.jobs_completed == 1


# ---------------------------------------------------------------------------
# TEST 3: Claim-queue-item atomicity for a specific id.
# ---------------------------------------------------------------------------
def test_level4_claim_atomicity(tmp_path, db):
    _enqueue_auto(db, "q-l4-at", "job-l4-at", "Atomic Claim Topic")
    engine = _mk_engine(tmp_path, db)

    first = engine.db.claim_queue_item(queue_id="q-l4-at", worker_id="w-a", lease_duration_sec=300.0)
    assert first is not None
    assert first["queue_id"] == "q-l4-at"
    assert first["status"] == "running"

    second = engine.db.claim_queue_item(queue_id="q-l4-at", worker_id="w-b", lease_duration_sec=300.0)
    assert second is None


# ---------------------------------------------------------------------------
# TEST 4: Items already running (claimed) are never re-discovered.
# ---------------------------------------------------------------------------
def test_level4_running_item_not_eligible(tmp_path, db):
    _enqueue_auto(db, "q-l4-r", "job-l4-r", "Running Topic")
    engine = _mk_engine(tmp_path, db)
    engine.db.claim_queue_item(queue_id="q-l4-r", worker_id="w-x", lease_duration_sec=300.0)

    summary = engine.run_auto_produce_cycle(channel_id="default", limit=10, policy="mock")
    assert summary.queued_jobs_discovered == 0
    assert summary.jobs_producing == 0
    assert summary.jobs_skipped_duplicate == 0


# ---------------------------------------------------------------------------
# TEST 5: Invalid payload (empty topic) fails closed and blocks the item.
# ---------------------------------------------------------------------------
def test_level4_invalid_empty_topic_blocked(tmp_path, db):
    _enqueue_auto(db, "q-l4-inv", "job-l4-inv", "")
    engine = _mk_engine(tmp_path, db)

    summary = engine.run_auto_produce_cycle(channel_id="default", limit=10, policy="mock")
    assert summary.jobs_blocked == 1
    assert summary.jobs_producing == 0
    assert summary.jobs_completed == 0
    item = db.get_queue_item("q-l4-inv")
    assert item["status"] == "blocked"
    assert "pre-flight" in (item["last_error"] or "").lower()


# ---------------------------------------------------------------------------
# TEST 6: Cancelled items are never produced.
# ---------------------------------------------------------------------------
def test_level4_cancelled_not_produced(tmp_path, db):
    _enqueue_auto(db, "q-l4-c", "job-l4-c", "Cancelled Topic")
    db.cancel_queue_item("q-l4-c")
    engine = _mk_engine(tmp_path, db)

    summary = engine.run_auto_produce_cycle(channel_id="default", limit=10, policy="mock")
    assert summary.queued_jobs_discovered == 0
    assert summary.jobs_producing == 0
    item = db.get_queue_item("q-l4-c")
    assert item["status"] == "cancelled"


# ---------------------------------------------------------------------------
# TEST 7: Policy recheck — prohibited content keyword blocks production.
# ---------------------------------------------------------------------------
def test_level4_policy_recheck_prohibited_topic(tmp_path, db):
    _enqueue_auto(db, "q-l4-pro", "job-l4-pro", "Try this dangerous challenge at home")
    engine = _mk_engine(tmp_path, db)

    summary = engine.run_auto_produce_cycle(channel_id="default", limit=10, policy="mock")
    assert summary.jobs_blocked == 1
    assert any("Prohibited" in r["reason"] for r in summary.decision_reasons)
    assert db.get_queue_item("q-l4-pro")["status"] == "blocked"


# ---------------------------------------------------------------------------
# TEST 8: Policy recheck — autonomous job without evidence is blocked.
# ---------------------------------------------------------------------------
def test_level4_policy_recheck_missing_evidence(tmp_path, db):
    _enqueue_auto(db, "q-l4-ev", "job-l4-ev", "Topic With No Evidence", parent_signal_ids=[])
    engine = _mk_engine(tmp_path, db)

    summary = engine.run_auto_produce_cycle(channel_id="default", limit=10, policy="mock")
    assert summary.jobs_blocked == 1
    assert any("evidence" in r["reason"].lower() for r in summary.decision_reasons)


# ---------------------------------------------------------------------------
# TEST 9: Policy recheck — disallowed content profile is blocked.
# ---------------------------------------------------------------------------
def test_level4_policy_recheck_content_format(tmp_path, db):
    _enqueue_auto(db, "q-l4-fmt", "job-l4-fmt", "Long Form Topic", profile="long_form")
    engine = _mk_engine(tmp_path, db)

    summary = engine.run_auto_produce_cycle(channel_id="default", limit=10, policy="mock")
    assert summary.jobs_blocked == 1
    assert any("profile" in r["reason"].lower() for r in summary.decision_reasons)


# ---------------------------------------------------------------------------
# TEST 10: Policy recheck — niche category fit is enforced via channel profile.
# ---------------------------------------------------------------------------
def test_level4_policy_recheck_niche_category(tmp_path, db):
    cm = ChannelManager(db)
    prof = ChannelProfile(
        channel_id="tech_shorts",
        channel_name="Tech Shorts",
        niche=NicheConfig(niche_name="technology", allowed_categories=["technology"]),
    )
    cm.create_channel(prof)

    _enqueue_auto(db, "q-l4-nc", "job-l4-nc", "Cooking Hacks That Work", channel_id="tech_shorts", category="cooking")
    engine = _mk_engine(tmp_path, db)

    summary = engine.run_auto_produce_cycle(channel_id="tech_shorts", limit=10, policy="mock")
    assert summary.jobs_blocked == 1
    assert any("category" in r["reason"].lower() for r in summary.decision_reasons)


# ---------------------------------------------------------------------------
# TEST 11: Disabled channel halts the cycle before any production.
# ---------------------------------------------------------------------------
def test_level4_disabled_channel_halts(tmp_path, db):
    cm = ChannelManager(db)
    cm.create_channel(ChannelProfile(channel_id="offline_ch", channel_name="Offline"))
    cm.disable_channel("offline_ch")

    _enqueue_auto(db, "q-l4-off", "job-l4-off", "Should Not Produce")
    engine = _mk_engine(tmp_path, db)

    summary = engine.run_auto_produce_cycle(channel_id="offline_ch", limit=10, policy="mock")
    assert summary.status == "blocked"
    assert summary.jobs_producing == 0
    assert "disabled" in (summary.error_message or "").lower()


# ---------------------------------------------------------------------------
# TEST 12: Research-stage failure -> retry_wait (backoff), counted as retry.
# ---------------------------------------------------------------------------
def test_level4_research_failure_retry(tmp_path, db):
    _enqueue_auto(db, "q-l4-rs", "job-l4-rs", "Research Failure Topic")
    mock_orch = MagicMock(spec=PipelineOrchestrator)
    mock_orch.run_pipeline.side_effect = PipelineError("Research provider down", category="RETRYABLE", stage="RESEARCH")
    engine = _mk_engine(tmp_path, db)

    summary = engine.run_auto_produce_cycle(channel_id="default", limit=10, policy="mock", orchestrator=mock_orch)
    assert summary.jobs_producing == 1
    assert summary.jobs_retry_wait == 1
    assert summary.jobs_qa_failed == 0
    assert db.get_queue_item("q-l4-rs")["status"] == "retry_wait"


# ---------------------------------------------------------------------------
# TEST 13: Script-stage non-retryable failure -> failed -> QA-failed bucket.
# ---------------------------------------------------------------------------
def test_level4_script_failure_nonretryable(tmp_path, db):
    _enqueue_auto(db, "q-l4-sc", "job-l4-sc", "Script Failure Topic")
    mock_orch = MagicMock(spec=PipelineOrchestrator)
    mock_orch.run_pipeline.side_effect = PipelineError("Malformed schema", category="NON_RETRYABLE", stage="SCRIPT")
    engine = _mk_engine(tmp_path, db)

    summary = engine.run_auto_produce_cycle(channel_id="default", limit=10, policy="mock", orchestrator=mock_orch)
    assert summary.jobs_qa_failed == 1
    assert summary.jobs_completed == 0
    assert db.get_queue_item("q-l4-sc")["status"] == "failed"


# ---------------------------------------------------------------------------
# TEST 14: TTS-stage failure -> retry_wait.
# ---------------------------------------------------------------------------
def test_level4_tts_failure_retry(tmp_path, db):
    _enqueue_auto(db, "q-l4-tt", "job-l4-tt", "TTS Failure Topic")
    mock_orch = MagicMock(spec=PipelineOrchestrator)
    mock_orch.run_pipeline.side_effect = PipelineError("TTS voice unavailable", category="RETRYABLE", stage="VOICE")
    engine = _mk_engine(tmp_path, db)

    summary = engine.run_auto_produce_cycle(channel_id="default", limit=10, policy="mock", orchestrator=mock_orch)
    assert summary.jobs_retry_wait == 1
    assert db.get_queue_item("q-l4-tt")["status"] == "retry_wait"


# ---------------------------------------------------------------------------
# TEST 15: Asset-stage failure -> BLOCKED terminal -> QA-failed bucket.
# ---------------------------------------------------------------------------
def test_level4_asset_failure_blocked(tmp_path, db):
    _enqueue_auto(db, "q-l4-as", "job-l4-as", "Asset Failure Topic")
    mock_orch = MagicMock(spec=PipelineOrchestrator)
    mock_orch.run_pipeline.side_effect = PipelineError("Asset gate blocked: license unknown", category="BLOCKED", stage="ASSETS")
    engine = _mk_engine(tmp_path, db)

    summary = engine.run_auto_produce_cycle(channel_id="default", limit=10, policy="mock", orchestrator=mock_orch)
    assert summary.jobs_qa_failed == 1
    assert summary.jobs_completed == 0
    assert db.get_queue_item("q-l4-as")["status"] == "blocked"


# ---------------------------------------------------------------------------
# TEST 16: MoneyPrinterTurbo render failure -> retry_wait.
# ---------------------------------------------------------------------------
def test_level4_mpt_render_failure_retry(tmp_path, db):
    _enqueue_auto(db, "q-l4-mpt", "job-l4-mpt", "MPT Render Topic")
    mock_orch = MagicMock(spec=PipelineOrchestrator)
    mock_orch.run_pipeline.side_effect = PipelineError("Renderer timeout", category="RETRYABLE", stage="RENDER")
    engine = _mk_engine(tmp_path, db)

    summary = engine.run_auto_produce_cycle(channel_id="default", limit=10, policy="mock", orchestrator=mock_orch)
    assert summary.jobs_retry_wait == 1
    assert db.get_queue_item("q-l4-mpt")["status"] == "retry_wait"


# ---------------------------------------------------------------------------
# TEST 17: QA-stage BLOCK -> terminal blocked -> QA-failed bucket, not ready.
# ---------------------------------------------------------------------------
def test_level4_qa_failure_blocked(tmp_path, db):
    _enqueue_auto(db, "q-l4-qa", "job-l4-qa", "QA Failure Topic")
    mock_orch = MagicMock(spec=PipelineOrchestrator)
    mock_orch.run_pipeline.side_effect = PipelineError("QA Gate Blocked: empty container", category="BLOCKED", stage="QA")
    engine = _mk_engine(tmp_path, db)

    summary = engine.run_auto_produce_cycle(channel_id="default", limit=10, policy="mock", orchestrator=mock_orch)
    assert summary.jobs_qa_failed == 1
    assert summary.jobs_ready_to_publish == 0
    assert db.get_queue_item("q-l4-qa")["status"] == "blocked"


# ---------------------------------------------------------------------------
# TEST 18: A receipt that reports FAIL is completed but NOT READY_TO_PUBLISH.
# ---------------------------------------------------------------------------
def test_level4_qa_fail_receipt_not_ready(tmp_path, db):
    _enqueue_auto(db, "q-l4-rf", "job-l4-rf", "Receipt Fail Topic")
    mock_orch = MagicMock(spec=PipelineOrchestrator)
    mock_orch.run_pipeline.return_value = _success_result(job_id="job-l4-rf", qa_status="FAIL")
    engine = _mk_engine(tmp_path, db)

    summary = engine.run_auto_produce_cycle(channel_id="default", limit=10, policy="mock", orchestrator=mock_orch)
    assert summary.jobs_completed == 1
    assert summary.jobs_ready_to_publish == 0
    assert db.get_queue_item("q-l4-rf")["status"] == "succeeded"


# ---------------------------------------------------------------------------
# TEST 19: Idempotent re-run never re-produces a succeeded job.
# ---------------------------------------------------------------------------
def test_level4_idempotent_rerun_skip(tmp_path, db):
    _enqueue_auto(db, "q-l4-id", "job-l4-id", "Idempotent Topic")
    mock_orch = MagicMock(spec=PipelineOrchestrator)
    mock_orch.run_pipeline.return_value = _success_result(job_id="job-l4-id")
    engine = _mk_engine(tmp_path, db)

    first = engine.run_auto_produce_cycle(channel_id="default", limit=10, policy="mock", orchestrator=mock_orch)
    assert first.jobs_ready_to_publish == 1

    second = engine.run_auto_produce_cycle(channel_id="default", limit=10, policy="mock", orchestrator=mock_orch)
    assert second.queued_jobs_discovered == 0
    assert second.jobs_producing == 0
    assert second.jobs_completed == 0
    assert second.jobs_skipped_duplicate == 0


# ---------------------------------------------------------------------------
# TEST 20: Retry-wait items become eligible again once the window opens.
# ---------------------------------------------------------------------------
def test_level4_retry_window_reopens(tmp_path, db):
    _enqueue_auto(db, "q-l4-rw", "job-l4-rw", "Retry Window Topic")
    fail_orch = MagicMock(spec=PipelineOrchestrator)
    fail_orch.run_pipeline.side_effect = PipelineError("Transient", category="RETRYABLE", stage="RESEARCH")
    engine = _mk_engine(tmp_path, db)

    first = engine.run_auto_produce_cycle(channel_id="default", limit=10, policy="mock", orchestrator=fail_orch)
    assert first.jobs_retry_wait == 1

    with db._connect() as conn:
        conn.execute("UPDATE queue_items SET next_retry_at = datetime('now', '-1 day') WHERE queue_id = 'q-l4-rw'")
        conn.commit()

    ok_orch = MagicMock(spec=PipelineOrchestrator)
    ok_orch.run_pipeline.return_value = _success_result(job_id="job-l4-rw")
    second = engine.run_auto_produce_cycle(channel_id="default", limit=10, policy="mock", orchestrator=ok_orch)
    assert second.queued_jobs_discovered == 1
    assert second.jobs_ready_to_publish == 1
    assert db.get_queue_item("q-l4-rw")["status"] == "succeeded"


# ---------------------------------------------------------------------------
# TEST 21: Retry-limit exhaustion lands in dead_letter and counts as QA-failed.
# ---------------------------------------------------------------------------
def test_level4_retry_limit_dead_letter(tmp_path, db):
    _enqueue_auto(db, "q-l4-dl", "job-l4-dl", "DLQ Topic")
    with db._connect() as conn:
        conn.execute("UPDATE queue_items SET max_attempts = 1 WHERE queue_id = 'q-l4-dl'")
        conn.commit()
    mock_orch = MagicMock(spec=PipelineOrchestrator)
    mock_orch.run_pipeline.side_effect = PipelineError("Exhausted", category="RETRYABLE", stage="RENDER")
    engine = _mk_engine(tmp_path, db)

    summary = engine.run_auto_produce_cycle(channel_id="default", limit=10, policy="mock", orchestrator=mock_orch)
    assert summary.jobs_qa_failed == 1
    assert db.get_queue_item("q-l4-dl")["status"] == "dead_letter"


# ---------------------------------------------------------------------------
# TEST 22: Stale leases from a crashed worker are recovered and re-producible.
# ---------------------------------------------------------------------------
def test_level4_stale_lease_recovery(tmp_path, db):
    _enqueue_auto(db, "q-l4-sl", "job-l4-sl", "Stale Lease Topic")
    engine = _mk_engine(tmp_path, db)
    engine.db.claim_queue_item(queue_id="q-l4-sl", worker_id="crashed-worker", lease_duration_sec=300.0)

    with db._connect() as conn:
        conn.execute("UPDATE queue_items SET lease_expires_at = datetime('now', '-1 day') WHERE queue_id = 'q-l4-sl'")
        conn.commit()
    recovered = db.recover_stale_leases(grace_lease_sec=0)
    assert "q-l4-sl" in recovered

    mock_orch = MagicMock(spec=PipelineOrchestrator)
    mock_orch.run_pipeline.return_value = _success_result(job_id="job-l4-sl")
    summary = engine.run_auto_produce_cycle(channel_id="default", limit=10, policy="mock", orchestrator=mock_orch)
    assert summary.jobs_ready_to_publish == 1


# ---------------------------------------------------------------------------
# TEST 23: Concurrency budget stops production at max_concurrent_jobs.
# ---------------------------------------------------------------------------
def test_level4_concurrency_limit(tmp_path, db):
    _enqueue_auto(db, "q-l4-c1", "job-l4-c1", "Concurrent One")
    _enqueue_auto(db, "q-l4-c2", "job-l4-c2", "Concurrent Two")
    engine = _mk_engine(tmp_path, db, policy=AutonomyPolicy(max_concurrent_jobs=1))
    engine.db.claim_queue_item(queue_id="q-l4-c1", worker_id="other-daemon", lease_duration_sec=300.0)

    summary = engine.run_auto_produce_cycle(channel_id="default", limit=10, policy="mock")
    assert summary.jobs_concurrency_blocked == 1
    assert summary.jobs_eligible == 0
    assert summary.jobs_producing == 0
    assert db.get_queue_item("q-l4-c2")["status"] == "queued"


# ---------------------------------------------------------------------------
# TEST 24: Daily production budget is enforced across in-flight items.
# ---------------------------------------------------------------------------
def test_level4_daily_limit(tmp_path, db):
    _enqueue_auto(db, "q-l4-d1", "job-l4-d1", "Daily One")
    _enqueue_auto(db, "q-l4-d2", "job-l4-d2", "Daily Two")
    engine = _mk_engine(tmp_path, db, policy=AutonomyPolicy(max_jobs_per_day=1))

    summary = engine.run_auto_produce_cycle(channel_id="default", limit=10, policy="mock")
    assert summary.queued_jobs_discovered == 2
    assert summary.jobs_daily_limit_blocked >= 1
    assert summary.jobs_producing == 0
    for qid in ("q-l4-d1", "q-l4-d2"):
        assert db.get_queue_item(qid)["status"] == "queued"


# ---------------------------------------------------------------------------
# TEST 25: Dry-run previews eligibility without mutating queue or DB records.
# ---------------------------------------------------------------------------
def test_level4_dry_run_no_mutation(tmp_path, db):
    _enqueue_auto(db, "q-l4-dr", "job-l4-dr", "Dry Run Topic")
    engine = _mk_engine(tmp_path, db)

    summary = engine.run_auto_produce_cycle(channel_id="default", limit=10, dry_run=True, policy="mock")
    assert summary.jobs_eligible == 1
    assert summary.jobs_producing == 0
    assert summary.jobs_completed == 0
    assert db.get_queue_item("q-l4-dr")["status"] == "queued"
    with db._connect() as conn:
        runs = conn.execute("SELECT count(*) as cnt FROM autonomy_runs WHERE run_id = ?", (summary.run_id,)).fetchone()
    assert runs["cnt"] == 0


# ---------------------------------------------------------------------------
# TEST 26: CLI dispatch for the Level 4 auto-produce run.
# ---------------------------------------------------------------------------
def test_level4_cli_dispatch(tmp_path):
    fake = MagicMock()
    fake.run_auto_produce_cycle.return_value = AutoProduceSummary(
        run_id="ap-test", channel_id="default", autonomy_level=4, dry_run=True, policy="mock",
        queued_jobs_discovered=2, jobs_eligible=1, jobs_blocked=1, jobs_ready_to_publish=1,
    )
    with patch("autopilot.core.autonomy.AutonomyEngine", return_value=fake):
        from autopilot.cli.main import run_autonomy_run
        ret = run_autonomy_run(level=4, output_json=True)
    assert ret == 0
    fake.run_auto_produce_cycle.assert_called_once()
    assert fake.run_auto_produce_cycle.call_args.kwargs["policy"] == "local_only"