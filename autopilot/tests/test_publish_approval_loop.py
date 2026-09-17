"""Protected Publishing Loop — Operator Approval Gate Tests.

The final milestone hardens publishing so real publications require an explicit
operator approval (publish_approvals.status == 'approved') ON TOP of the QA gate
(WorkflowState.APPROVED == READY_TO_PUBLISH). Dry runs are workflow-exempt.

Test map (TESTS 1-26):
  1  no approval record          -> BLOCKED_APPROVAL, job stays APPROVED
  2  pending approval            -> BLOCKED_APPROVAL / APPROVAL_PENDING, no provider call
  3  rejected approval           -> BLOCKED_APPROVAL / APPROVAL_REJECTED, job stays APPROVED
  4  approved approval           -> SUCCESS / PUBLISHED
  5  legacy require_approval=False path still publishes without approval
  6  checksum mismatch post-approval -> invalidates approval, job stays APPROVED
  7  re-approval after artifact change -> publishes; full history preserved
  8  already published + new approval -> SKIPPED_DUPLICATE (no re-upload)
  9  already published + no new approval -> SKIPPED_DUPLICATE (idempotency before gate)
  10 platform mismatch            -> APPROVAL_PLATFORM_MISMATCH
  11 dry-run workflow exempt (no approval required at all)
  12 approval history preserved across decide calls
  13 CLI reject then run          -> rejected publization blocked
  14 autonomous mode holds scrutiny (pending approval, zero publish calls)
  15 autonomous auto-publish only with existing approved approval
  16 CLI `publish approve` creates + binds checksum/platform
  17 end-to-end CLI approve -> run -> PUBLISHED
  18 job not ready (not APPROVED) -> JOB_NOT_READY block
  19 approval channel mismatch    -> APPROVAL_CHANNEL_MISMATCH
  20 legacy `publish <job>` enforces the gate (no dry-run)
  21 pipeline auto_publish holds/blocked without approval (job stays APPROVED)
  22 lazy checksum binding at publish time
  23 lazy platform binding at publish time
  24 pending approvals are visible via list_pending_approvals / health counts
  25 health report exposes publication-loop counts
  26 no provider/artifacts written on a blocked publication
"""
import hashlib
import json
import uuid
import pytest
from pathlib import Path
from unittest.mock import patch

from autopilot.core.config import CONFIG
from autopilot.core.contracts import PublishStatus
from autopilot.core.publisher import PublishingEngine, compute_file_sha256
from autopilot.core.state_machine import WorkflowState
from autopilot.providers.mock_publisher import MockPublisher
from autopilot.db.manager import DBManager


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def make_ready_job(
    db: DBManager,
    tmp_path: Path,
    job_id: str,
    channel_id: str = "chan1",
    payload: bytes = b"APPROVAL_LOOP_VIDEO_PAYLOAD",
) -> str:
    """Create a QA-passing, READY_TO_PUBLISH job and return its meridian checksum."""
    media_file = tmp_path / "artifacts" / "jobs" / job_id / "render" / "final.mp4"
    media_file.parent.mkdir(parents=True, exist_ok=True)
    media_file.write_bytes(payload)
    checksum = compute_file_sha256(media_file)

    qa_dir = tmp_path / "artifacts" / "jobs" / job_id / "quality"
    qa_dir.mkdir(parents=True, exist_ok=True)
    receipt = {
        "receipt_id": f"rcpt-qa-{job_id}",
        "job_id": job_id,
        "content_id": job_id,
        "status": "PASS",
        "publish_allowed": True,
        "media_path": str(media_file),
        "media_checksum_sha256": checksum,
    }
    (qa_dir / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")

    db.create_job(job_id=job_id, channel_id=channel_id, topic=f"Topic {job_id}")
    db.update_job_status(job_id, WorkflowState.APPROVED.value)
    return checksum


def approve(db: DBManager, job_id: str, checksum: str, platform: str = "youtube") -> None:
    if db.get_publish_approval(job_id) is None:
        db.create_publish_approval(job_id=job_id, channel_id="chan1", notes="test approval")
    db.decide_publish_approval(job_id, approved=True, decided_by="tester", notes="go",
                               artifact_checksum=checksum, platform=platform)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    monkeypatch.setattr(CONFIG, "db_path", tmp_path / "approval.db")
    db = DBManager(CONFIG.db_path)
    db.init_schema()
    return {"tmp_path": tmp_path, "db": db}


def run_engine(env, job_id, provider=None, **kwargs):
    db = env["db"]
    kwargs.setdefault("provider", provider or MockPublisher())
    kwargs.setdefault("db_manager", db)
    return PublishingEngine(CONFIG).publish_job(job_id=job_id, **kwargs)


def run_with_approval(env, job_id, **kwargs):
    kwargs.setdefault("require_approval", True)
    return run_engine(env, job_id, **kwargs)


# ---------------------------------------------------------------------------
# TEST 1 — no approval record
# ---------------------------------------------------------------------------

def test_approval_required_without_approval(env):
    job_id = "t1-no-approval"
    make_ready_job(env["db"], env["tmp_path"], job_id)
    result = run_with_approval(env, job_id)

    assert result.success is False
    assert result.status == PublishStatus.BLOCKED_APPROVAL
    assert result.error.error_code == "APPROVAL_REQUIRED"
    assert env["db"].get_job(job_id)["status"] == "APPROVED"  # never FAILED_PUBLISH


# ---------------------------------------------------------------------------
# TEST 2 — pending approval
# ---------------------------------------------------------------------------

def test_approval_pending_blocks(env):
    job_id = "t2-pending"
    make_ready_job(env["db"], env["tmp_path"], job_id)
    env["db"].create_publish_approval(job_id=job_id, channel_id="chan1", notes="awaiting operator")
    result = run_with_approval(env, job_id)

    assert result.success is False
    assert result.status == PublishStatus.BLOCKED_APPROVAL
    assert result.error.error_code == "APPROVAL_PENDING"
    assert env["db"].get_publications_for_job(job_id) == []


# ---------------------------------------------------------------------------
# TEST 3 — rejected approval
# ---------------------------------------------------------------------------

def test_approval_rejected_blocks(env):
    job_id = "t3-rejected"
    make_ready_job(env["db"], env["tmp_path"], job_id)
    env["db"].create_publish_approval(job_id=job_id, channel_id="chan1", notes="nope")
    env["db"].decide_publish_approval(job_id, approved=False, decided_by="tester", notes="rejected")
    result = run_with_approval(env, job_id)

    assert result.success is False
    assert result.status == PublishStatus.BLOCKED_APPROVAL
    assert result.error.error_code == "APPROVAL_REJECTED"
    assert env["db"].get_job(job_id)["status"] == "APPROVED"


# ---------------------------------------------------------------------------
# TEST 4 — approved approval publishes
# ---------------------------------------------------------------------------

def test_approved_approval_publishes(env):
    job_id = "t4-approved"
    checksum = make_ready_job(env["db"], env["tmp_path"], job_id)
    approve(env["db"], job_id, checksum)
    result = run_with_approval(env, job_id)

    assert result.success is True
    assert result.status in (PublishStatus.PUBLISHED, PublishStatus.SUCCESS)
    assert env["db"].get_job(job_id)["status"] == "PUBLISHED"
    pubs = env["db"].get_publications_for_job(job_id)
    assert len(pubs) == 1
    assert pubs[0]["status"] == "SUCCESS"


# ---------------------------------------------------------------------------
# TEST 5 — legacy (unprotected) publish path
# ---------------------------------------------------------------------------

def test_legacy_unapproval_path_publishes(env):
    job_id = "t5-legacy"
    make_ready_job(env["db"], env["tmp_path"], job_id)
    result = run_engine(env, job_id)  # require_approval defaults to False
    assert result.success is True
    assert env["db"].get_job(job_id)["status"] == "PUBLISHED"


# ---------------------------------------------------------------------------
# TEST 6 — checksum mismatch invalidates approval
# ---------------------------------------------------------------------------

def test_approval_checksum_mismatch_invalidates(env):
    job_id = "t6-mismatch"
    checksum = make_ready_job(env["db"], env["tmp_path"], job_id)
    approve(env["db"], job_id, checksum)

    # Operator modifies the artifact AFTER approving. Refresh the QA receipt so
    # the QA gate stays green; only the APPROVAL binding is now stale.
    media_file = env["tmp_path"] / "artifacts" / "jobs" / job_id / "render" / "final.mp4"
    media_file.write_bytes(b"TAMPERED_POST_APPROVAL_PAYLOAD")
    new_checksum = compute_file_sha256(media_file)
    qa_file = env["tmp_path"] / "artifacts" / "jobs" / job_id / "quality" / "receipt.json"
    qa_receipt = json.loads(qa_file.read_text(encoding="utf-8"))
    qa_receipt["media_checksum_sha256"] = new_checksum
    qa_file.write_text(json.dumps(qa_receipt), encoding="utf-8")

    result = run_with_approval(env, job_id)
    assert result.success is False
    assert result.status == PublishStatus.BLOCKED_APPROVAL
    assert result.error.error_code == "APPROVAL_ARTIFACT_MISMATCH"

    approval = env["db"].get_publish_approval(job_id)
    assert approval["status"] == "rejected"
    assert approval["decided_by"] == "system"
    assert env["db"].get_job(job_id)["status"] == "APPROVED"  # still ready, not failed


# ---------------------------------------------------------------------------
# TEST 7 — re-approval after artifact change publishes, history preserved
# ---------------------------------------------------------------------------

def test_reapproval_after_artifact_change_publishes(env):
    job_id = "t7-reapprove"
    checksum = make_ready_job(env["db"], env["tmp_path"], job_id)
    approve(env["db"], job_id, checksum)

    media_file = env["tmp_path"] / "artifacts" / "jobs" / job_id / "render" / "final.mp4"
    media_file.write_bytes(b"REVISED_AFTER_APPROVAL_PAYLOAD")
    new_checksum = compute_file_sha256(media_file)
    qa_file = env["tmp_path"] / "artifacts" / "jobs" / job_id / "quality" / "receipt.json"
    qa_receipt = json.loads(qa_file.read_text(encoding="utf-8"))
    qa_receipt["media_checksum_sha256"] = new_checksum
    qa_file.write_text(json.dumps(qa_receipt), encoding="utf-8")

    blocked = run_with_approval(env, job_id)
    assert blocked.status == PublishStatus.BLOCKED_APPROVAL

    # Operator requests a fresh approval for the NEW artifact and approves it.
    env["db"].create_publish_approval(job_id=job_id, channel_id="chan1", notes="fresh approval for revised artifact")
    env["db"].decide_publish_approval(job_id, approved=True, decided_by="tester", notes="approved v2",
                                      artifact_checksum=new_checksum, platform="youtube")
    result = run_with_approval(env, job_id)
    assert result.success is True
    assert result.status in (PublishStatus.PUBLISHED, PublishStatus.SUCCESS)
    assert env["db"].get_job(job_id)["status"] == "PUBLISHED"

    history = env["db"].list_publish_approvals(job_id=job_id)
    assert len(history) >= 2  # invalidated(rejected/system) -> approved(re-approval)
    assert history[-1]["status"] == "approved"
    assert env["db"].get_publish_approval(job_id)["status"] == "approved"


# ---------------------------------------------------------------------------
# TEST 8 — already published + new approval -> SKIPPED_DUPLICATE
# ---------------------------------------------------------------------------

def test_already_published_with_new_approval_skips(env):
    job_id = "t8-dup-new"
    checksum = make_ready_job(env["db"], env["tmp_path"], job_id)
    approve(env["db"], job_id, checksum)
    first = run_with_approval(env, job_id)
    assert first.status in (PublishStatus.PUBLISHED, PublishStatus.SUCCESS)

    # A brand-new (pending) approval request arrives after publication.
    env["db"].create_publish_approval(job_id=job_id, channel_id="chan1", notes="post-publish request")
    second = run_with_approval(env, job_id)

    assert second.success is True
    assert second.status == PublishStatus.SKIPPED_DUPLICATE
    assert second.receipt is not None
    assert env["db"].get_job(job_id)["status"] == "PUBLISHED"
    assert len(env["db"].get_publications_for_job(job_id)) == 1  # no re-upload


# ---------------------------------------------------------------------------
# TEST 9 — already published, no new approval -> still SKIPPED_DUPLICATE
# ---------------------------------------------------------------------------

def test_already_published_with_stale_approval_skips(env):
    job_id = "t9-dup-stale"
    checksum = make_ready_job(env["db"], env["tmp_path"], job_id)
    approve(env["db"], job_id, checksum)
    first = run_with_approval(env, job_id)
    assert first.status in (PublishStatus.PUBLISHED, PublishStatus.SUCCESS)

    second = run_with_approval(env, job_id)
    assert second.success is True
    assert second.status == PublishStatus.SKIPPED_DUPLICATE
    assert len(env["db"].get_publications_for_job(job_id)) == 1


# ---------------------------------------------------------------------------
# TEST 10 — platform mismatch
# ---------------------------------------------------------------------------

def test_approval_platform_mismatch_blocks(env):
    job_id = "t10-platform"
    checksum = make_ready_job(env["db"], env["tmp_path"], job_id)
    approve(env["db"], job_id, checksum, platform="youtube")

    result = run_with_approval(env, job_id, platform="postiz")
    assert result.success is False
    assert result.status == PublishStatus.BLOCKED_APPROVAL
    assert result.error.error_code == "APPROVAL_PLATFORM_MISMATCH"

    # Same approval works for the bound platform.
    ok = run_with_approval(env, job_id, platform="youtube")
    assert ok.success is True


# ---------------------------------------------------------------------------
# TEST 11 — dry run workflow-exempt
# ---------------------------------------------------------------------------

def test_dry_run_is_workflow_exempt(env):
    job_id = "t11-dryrun"
    make_ready_job(env["db"], env["tmp_path"], job_id)  # no approval at all
    result = run_with_approval(env, job_id, dry_run=True)

    assert result.success is True
    assert result.status == PublishStatus.DRY_RUN
    job_row = env["db"].get_job(job_id)
    assert job_row["status"] in ("APPROVED", "READY")


# ---------------------------------------------------------------------------
# TEST 12 — approval history preserved across decide calls
# ---------------------------------------------------------------------------

def test_approval_history_preserved(env):
    job_id = "t12-history"
    make_ready_job(env["db"], env["tmp_path"], job_id)
    env["db"].create_publish_approval(job_id=job_id, channel_id="chan1", notes="request 1")
    env["db"].decide_publish_approval(job_id, approved=True, decided_by="tester", notes="ok",
                                      artifact_checksum="chk-a", platform="youtube")
    env["db"].create_publish_approval(job_id=job_id, channel_id="chan1", notes="request 2")
    env["db"].decide_publish_approval(job_id, approved=False, decided_by="tester", notes="redo")
    env["db"].create_publish_approval(job_id=job_id, channel_id="chan1", notes="request 3")
    env["db"].decide_publish_approval(job_id, approved=True, decided_by="tester", notes="approved",
                                      artifact_checksum="chk-b", platform="youtube")

    history = env["db"].list_publish_approvals(job_id=job_id)
    assert len(history) == 3
    assert [h["status"] for h in history] == ["approved", "rejected", "approved"]
    latest = env["db"].get_publish_approval(job_id)
    assert latest["status"] == "approved"
    assert latest["media_checksum_sha256"] == "chk-b"


# ---------------------------------------------------------------------------
# TEST 13 — CLI reject then run
# ---------------------------------------------------------------------------

def test_cli_reject_then_run_blocked(env, capsys, monkeypatch):
    import autopilot.cli.main as cli_main
    job_id = "t13-cli-reject"
    make_ready_job(env["db"], env["tmp_path"], job_id)
    env["db"].create_publish_approval(job_id=job_id, channel_id="chan1", notes="initial")

    rc = cli_main.run_publish_reject(job_id=job_id, decided_by="alice", notes="not ready")
    assert rc == 0

    monkeypatch.setattr("sys.argv", ["autopilot", "publish", "run", job_id, "--platform", "youtube"])
    rc = cli_main.main()
    assert rc == 1
    assert "APPROVAL_REJECTED" in capsys.readouterr().out
    assert env["db"].get_job(job_id)["status"] == "APPROVED"


# ---------------------------------------------------------------------------
# TEST 14 — autonomous mode holds scrutiny (pending approval, zero publishes)
# ---------------------------------------------------------------------------

def _canned_pipeline(*args, **kwargs):
    job_id = kwargs.get("job_id") or (args[0] if args else None)
    return {
        "job_id": job_id,
        "status": "APPROVED",
        "qa_status": "PASS",
        "publish_allowed": True,
        "qa_report": {"status": "PASS", "publish_allowed": True},
    }


def _fixed_uuid():
    return uuid.UUID(hex="12345678123456781234567812345678")


def test_autonomous_mode_holds_approval(env, monkeypatch):
    import autopilot.core.autonomy as autonomy_mod
    from autopilot.core.autonomy import AutonomyEngine

    monkeypatch.setattr(CONFIG, "autonomy_auto_publish", False)
    monkeypatch.setattr(autonomy_mod.uuid, "uuid4", _fixed_uuid)
    monkeypatch.setattr("autopilot.core.pipeline.PipelineOrchestrator.run_pipeline", _canned_pipeline)

    fixed_uuid = _fixed_uuid()
    job_id = f"job-cyc-{fixed_uuid.hex[:8]}-{fixed_uuid.hex[:4]}"
    make_ready_job(env["db"], env["tmp_path"], job_id)

    engine = AutonomyEngine(config=CONFIG, db=env["db"])
    with patch("autopilot.core.publisher.PublishingEngine.publish_job") as mock_pub:
        res = engine.run_autonomous_cycle(seed_topic="Autonomy Hold", channel_id="default", mode="autonomous")

    mock_pub.assert_not_called()
    assert res["approval_status"] == "pending"
    approval = env["db"].get_publish_approval(job_id)
    assert approval is not None and approval["status"] == "pending"
    assert env["db"].get_job(job_id)["status"] == "APPROVED"
    assert env["db"].get_publications_for_job(job_id) == []


# ---------------------------------------------------------------------------
# TEST 15 — autonomous auto-publish only with existing approved approval
# ---------------------------------------------------------------------------

def test_autonomous_auto_publish_requires_approved_approval(env, monkeypatch):
    import autopilot.core.autonomy as autonomy_mod
    from autopilot.core.autonomy import AutonomyEngine
    from autopilot.providers.contracts import REGISTRY

    monkeypatch.setattr(CONFIG, "autonomy_auto_publish", True)
    monkeypatch.setattr(autonomy_mod.uuid, "uuid4", _fixed_uuid)
    monkeypatch.setattr("autopilot.core.pipeline.PipelineOrchestrator.run_pipeline", _canned_pipeline)

    orig_get = REGISTRY.get
    monkeypatch.setattr(
        REGISTRY, "get",
        lambda name, **kw: MockPublisher() if str(name).lower() in ("youtube", "postiz") else orig_get(name, **kw),
    )

    fixed_uuid = _fixed_uuid()
    job_id = f"job-cyc-{fixed_uuid.hex[:8]}-{fixed_uuid.hex[:4]}"
    checksum = make_ready_job(env["db"], env["tmp_path"], job_id, channel_id="default")

    engine = AutonomyEngine(config=CONFIG, db=env["db"])
    # A FRESH job with no prior approval must still be held despite the flag.
    res1 = engine.run_autonomous_cycle(seed_topic="AutoPublish Test", channel_id="default", mode="autonomous")
    assert res1["approval_status"] == "pending"

    # Operator approves; the next autonomous run executes the approved publish.
    approve(env["db"], job_id, checksum)
    res2 = engine.run_autonomous_cycle(seed_topic="AutoPublish Test", channel_id="default", mode="autonomous")
    assert res2["approval_status"] == "auto_approved"
    assert res2["publish_result"] is not None
    assert env["db"].get_job(job_id)["status"] == "PUBLISHED"
    pubs = env["db"].get_publications_for_job(job_id)
    assert pubs and pubs[0]["status"] == "SUCCESS"


# ---------------------------------------------------------------------------
# TEST 16 — CLI `publish approve` creates record and binds checksum/platform
# ---------------------------------------------------------------------------

def test_cli_approve_creates_and_binds(env):
    import autopilot.cli.main as cli_main
    job_id = "t16-cli-approve"
    checksum = make_ready_job(env["db"], env["tmp_path"], job_id)
    assert env["db"].get_publish_approval(job_id) is None

    rc = cli_main.run_publish_approve(job_id=job_id, decided_by="eve", notes="approved via CLI", platform="youtube")
    assert rc == 0
    approval = env["db"].get_publish_approval(job_id)
    assert approval["status"] == "approved"
    assert approval["media_checksum_sha256"] == checksum
    assert approval["platform"] == "youtube"


# ---------------------------------------------------------------------------
# TEST 17 — end-to-end CLI approve -> run -> PUBLISHED
# ---------------------------------------------------------------------------

def test_e2e_cli_approve_then_run_publishes(env, monkeypatch):
    import autopilot.cli.main as cli_main
    job_id = "t17-e2e"
    make_ready_job(env["db"], env["tmp_path"], job_id)

    monkeypatch.setattr("sys.argv", ["autopilot", "publish", "approve", job_id, "--by", "ops", "--notes", "all good"])
    assert cli_main.main() == 0
    assert env["db"].get_publish_approval(job_id)["status"] == "approved"

    monkeypatch.setattr("sys.argv", ["autopilot", "publish", "run", job_id, "--platform", "youtube"])
    assert cli_main.main() == 0
    assert env["db"].get_job(job_id)["status"] == "PUBLISHED"


# ---------------------------------------------------------------------------
# TEST 18 — job not ready (not APPROVED)
# ---------------------------------------------------------------------------

def test_job_not_ready_blocks(env):
    job_id = "t18-not-ready"
    make_ready_job(env["db"], env["tmp_path"], job_id)
    env["db"].update_job_status(job_id, WorkflowState.SCRIPTED.value)
    chk = compute_file_sha256(env["tmp_path"] / "artifacts" / "jobs" / job_id / "render" / "final.mp4")
    approve(env["db"], job_id, chk)

    result = run_with_approval(env, job_id)
    assert result.success is False
    assert result.status == PublishStatus.BLOCKED_APPROVAL
    assert result.error.error_code == "JOB_NOT_READY"
    assert env["db"].get_job(job_id)["status"] == "SCRIPTED"


# ---------------------------------------------------------------------------
# TEST 19 — approval channel mismatch
# ---------------------------------------------------------------------------

def test_approval_channel_mismatch_blocks(env):
    job_id = "t19-channel"
    chk = make_ready_job(env["db"], env["tmp_path"], job_id, channel_id="chanA")
    env["db"].create_publish_approval(job_id=job_id, channel_id="chanB", notes="wrong channel")
    env["db"].decide_publish_approval(job_id, approved=True, decided_by="tester", artifact_checksum=chk)

    result = run_with_approval(env, job_id)
    assert result.success is False
    assert result.status == PublishStatus.BLOCKED_APPROVAL
    assert result.error.error_code == "APPROVAL_CHANNEL_MISMATCH"


# ---------------------------------------------------------------------------
# TEST 20 — legacy `publish <job_id>` enforces the gate (no dry-run)
# ---------------------------------------------------------------------------

def test_legacy_cli_publish_requires_approval(env, monkeypatch, capsys):
    import autopilot.cli.main as cli_main
    job_id = "t20-legacy-cli"
    make_ready_job(env["db"], env["tmp_path"], job_id)

    monkeypatch.setattr("sys.argv", ["autopilot", "publish", job_id, "--platform", "youtube"])
    rc = cli_main.main()
    assert rc == 1
    out = capsys.readouterr().out
    assert "BLOCKED_APPROVAL" in out
    assert "APPROVAL_REQUIRED" in out
    assert env["db"].get_job(job_id)["status"] == "APPROVED"


# ---------------------------------------------------------------------------
# TEST 21 — pipeline auto_publish held without approval (job stays APPROVED)
# ---------------------------------------------------------------------------

def test_pipeline_auto_publish_requires_approval(env, monkeypatch):
    from autopilot.core.pipeline import PipelineOrchestrator, PipelineError
    from autopilot.providers.contracts import REGISTRY

    orig_get = REGISTRY.get
    monkeypatch.setattr(
        REGISTRY, "get",
        lambda name, **kw: MockPublisher() if str(name).lower() in ("youtube", "postiz") else orig_get(name, **kw),
    )

    job_id = "t21-pipeline"
    orch = PipelineOrchestrator(CONFIG)
    with pytest.raises(PipelineError) as exc_info:
        orch.run_pipeline(
            job_id=job_id,
            topic="Approval Gate Pipeline Test",
            channel_id="default",
            auto_publish=True,
            publish_platform="youtube",
            publish_visibility="private",
            production_engine="ffmpeg",
            tts_provider="mock",
            llm_provider="mock",
            research_provider="local",
        )
    assert exc_info.value.category == "NON_RETRYABLE"
    assert "approval" in str(exc_info.value).lower()
    job_row = env["db"].get_job(job_id)
    assert job_row is not None
    assert job_row["status"] == "APPROVED"  # held, never FAILED_PUBLISH
    assert env["db"].get_publications_for_job(job_id) == []


# ---------------------------------------------------------------------------
# TEST 22 — lazy checksum binding at publish time
# ---------------------------------------------------------------------------

def test_approval_lazy_checksum_binding(env):
    job_id = "t22-lazy-chk"
    checksum = make_ready_job(env["db"], env["tmp_path"], job_id)
    env["db"].create_publish_approval(job_id=job_id, channel_id="chan1", notes="approved w/o checksum")
    env["db"].decide_publish_approval(job_id, approved=True, decided_by="tester", platform="youtube")

    result = run_with_approval(env, job_id)
    assert result.success is True
    approval = env["db"].get_publish_approval(job_id)
    assert approval["media_checksum_sha256"] == checksum  # lazily bound


# ---------------------------------------------------------------------------
# TEST 23 — lazy platform binding at publish time
# ---------------------------------------------------------------------------

def test_approval_lazy_platform_binding(env):
    job_id = "t23-lazy-platform"
    checksum = make_ready_job(env["db"], env["tmp_path"], job_id)
    env["db"].create_publish_approval(job_id=job_id, channel_id="chan1",
                                      media_checksum_sha256=checksum, platform=None)
    env["db"].decide_publish_approval(job_id, approved=True, decided_by="tester")

    result = run_with_approval(env, job_id, platform="youtube")
    assert result.success is True
    assert env["db"].get_publish_approval(job_id)["platform"] == "youtube"


# ---------------------------------------------------------------------------
# TEST 24 — pending approvals visible in governance lists
# ---------------------------------------------------------------------------

def test_pending_approvals_visible(env):
    job_id = "t24-pending"
    make_ready_job(env["db"], env["tmp_path"], job_id)
    env["db"].create_publish_approval(job_id=job_id, channel_id="chan1", notes="awaiting")

    pending = env["db"].list_pending_approvals(channel_id="chan1")
    assert any(p["job_id"] == job_id and p["status"] == "pending" for p in pending)
    assert any(p["job_id"] == job_id for p in env["db"].list_pending_approvals())
    counts = env["db"].get_publish_health_counts()
    assert counts["awaiting_approval"] >= 1
    assert counts["approved"] == 0


# ---------------------------------------------------------------------------
# TEST 25 — health report exposes publication-loop counts
# ---------------------------------------------------------------------------

def test_health_report_includes_publication_loop(env, capsys, monkeypatch):
    import autopilot.cli.main as cli_main
    job_id = "t25-health"
    make_ready_job(env["db"], env["tmp_path"], job_id)
    env["db"].create_publish_approval(job_id=job_id, channel_id="chan1", notes="pending for health")

    rc = cli_main.run_health()
    assert rc == 0
    out = capsys.readouterr().out
    assert '"publishing"' in out
    assert '"awaiting_approval": 1' in out
    assert '"publish_failures": 0' in out


# ---------------------------------------------------------------------------
# TEST 26 — provider never invoked on a blocked publication
# ---------------------------------------------------------------------------

def test_provider_not_invoked_on_block(env):
    job_id = "t26-no-provider"
    make_ready_job(env["db"], env["tmp_path"], job_id)

    with patch.object(MockPublisher, "upload_video") as mock_pub:
        result = run_with_approval(env, job_id, provider=MockPublisher())
        mock_pub.assert_not_called()

    assert result.status == PublishStatus.BLOCKED_APPROVAL
    assert env["db"].get_publications_for_job(job_id) == []
    result_art = env["tmp_path"] / "artifacts" / "jobs" / job_id / "publish" / "result.json"
    assert not result_art.exists()  # no request/result artifacts written