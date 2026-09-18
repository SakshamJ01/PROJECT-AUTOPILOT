"""Regression tests for true publish readiness gate and counts.

Verifies:
- APPROVED but not publishable (no approval, missing QA, etc.)
- Genuinely publishable job (all gates satisfied)
- Already published job (idempotency, not ready to re-publish)
- Missing QA receipt
- Missing operator approval
- Checksum mismatch (media altered after QA or approval)
- Counts and list semantics in engine and RPC bridge handlers:
  - publishing.status
  - publishing.list_ready (including ready_only filter)
  - publishing.inspect
  - health.get
  - autonomy.status
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import pytest

from autopilot.bridge.handlers import BridgeHandlers
from autopilot.core.artifacts import job_artifact_dir
from autopilot.core.config import Config
from autopilot.core.publisher import PublishingEngine, compute_file_sha256
from autopilot.db.manager import DBManager


@pytest.fixture()
def env_setup(tmp_path, monkeypatch):
    db_path = tmp_path / "readiness.db"
    art_path = tmp_path / "artifacts"
    art_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AUTOPILOT_DB_PATH", str(db_path))
    monkeypatch.setenv("AUTOPILOT_ARTIFACTS_DIR", str(art_path))
    monkeypatch.setenv("AUTOPILOT_ANALYTICS_DEFAULT_PROVIDER", "mock")

    db = DBManager(str(db_path))
    db.init_schema()
    cfg = Config()
    handlers = BridgeHandlers(cfg, db)
    engine = PublishingEngine(config=cfg, db=db)
    return {
        "db": db,
        "cfg": cfg,
        "handlers": handlers,
        "engine": engine,
        "artifacts_dir": art_path,
        "tmp_path": tmp_path,
    }


def _seed_job_with_media(env, job_id="job-1", status="APPROVED", media_content=b"video-bytes-123"):
    db = env["db"]
    art_dir = job_artifact_dir(job_id, base_dir=env["artifacts_dir"])
    db.create_job(job_id=job_id, channel_id="default", topic="Test Topic")
    db.update_job_status(job_id, status)

    render_dir = art_dir / "render"
    render_dir.mkdir(parents=True, exist_ok=True)
    media_file = render_dir / "final.mp4"
    media_file.write_bytes(media_content)
    checksum = compute_file_sha256(media_file)
    return media_file, checksum


def _seed_qa_receipt(env, job_id="job-1", status="PASS", publish_allowed=True, checksum=None):
    art_dir = job_artifact_dir(job_id, base_dir=env["artifacts_dir"])
    quality_dir = art_dir / "quality"
    quality_dir.mkdir(parents=True, exist_ok=True)
    receipt = {
        "status": status,
        "publish_allowed": publish_allowed,
        "media_checksum_sha256": checksum or "fake-sha256",
        "content_id": job_id,
    }
    (quality_dir / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")


def _seed_approval(env, job_id="job-1", status="approved", checksum=None, platform="youtube", channel_id="default"):
    h = env["handlers"]
    if status == "approved":
        h.dispatch("publishing.approve", {"job_id": job_id})
    elif status == "rejected":
        # Make sure approval exists before rejecting
        h.dispatch("publishing.approve", {"job_id": job_id})
        h.dispatch("publishing.reject", {"job_id": job_id})
    else:
        db = env["db"]
        db.create_publish_approval(job_id=job_id, channel_id=channel_id, platform=platform)


# ---------------------------------------------------------------------------
# Core Engine Readiness Gate Tests
# ---------------------------------------------------------------------------

def test_approved_but_no_approval_is_not_publishable(env_setup):
    """Job has status == APPROVED and valid QA, but NO operator approval."""
    env = env_setup
    job_id = "job-unapproved"
    _, checksum = _seed_job_with_media(env, job_id=job_id, status="APPROVED")
    _seed_qa_receipt(env, job_id=job_id, checksum=checksum)

    res = env["engine"].evaluate_job_publishability(job_id)
    assert res["publishable"] is False
    assert "Explicit operator approval is required" in res["reason"]
    assert env["engine"].is_job_publishable(job_id) is False
    assert env["engine"].count_publishable_jobs() == 0


def test_genuinely_publishable_job(env_setup):
    """Job satisfies every gate: APPROVED, media present, QA PASS, operator APPROVED."""
    env = env_setup
    job_id = "job-ready"
    _, checksum = _seed_job_with_media(env, job_id=job_id, status="APPROVED")
    _seed_qa_receipt(env, job_id=job_id, checksum=checksum)
    _seed_approval(env, job_id=job_id, status="approved")

    res = env["engine"].evaluate_job_publishability(job_id)
    assert res["publishable"] is True
    assert res["reason"] is None
    assert env["engine"].is_job_publishable(job_id) is True
    assert env["engine"].count_publishable_jobs() == 1


def test_already_published_job_is_not_publishable(env_setup):
    """Job has already been published to the target platform."""
    env = env_setup
    job_id = "job-published"
    _, checksum = _seed_job_with_media(env, job_id=job_id, status="APPROVED")
    _seed_qa_receipt(env, job_id=job_id, checksum=checksum)
    _seed_approval(env, job_id=job_id, status="approved")

    # Record successful publish in DB and update status to PUBLISHED
    env["db"].record_publish_record(
        job_id=job_id,
        platform="youtube",
        remote_video_id="yt-video-123",
        status="SUCCESS",
        idempotency_key=f"idemp-{job_id}",
    )
    env["db"].update_job_status(job_id, "PUBLISHED")

    res = env["engine"].evaluate_job_publishability(job_id)
    assert res["publishable"] is False
    assert "in state 'PUBLISHED'" in res["reason"] or "already published" in res["reason"]
    assert env["engine"].is_job_publishable(job_id) is False
    assert env["engine"].count_publishable_jobs() == 0


def test_missing_qa_receipt_is_not_publishable(env_setup):
    """Job has APPROVED and operator approval, but QA receipt is missing."""
    env = env_setup
    job_id = "job-no-qa"
    _, checksum = _seed_job_with_media(env, job_id=job_id, status="APPROVED")
    _seed_approval(env, job_id=job_id, status="approved")

    res = env["engine"].evaluate_job_publishability(job_id)
    assert res["publishable"] is False
    assert "No QA receipt found" in res["reason"]
    assert env["engine"].is_job_publishable(job_id) is False
    assert env["engine"].count_publishable_jobs() == 0


def test_qa_failed_or_blocked_is_not_publishable(env_setup):
    """Job has QA with status BLOCK and publish_allowed=False."""
    env = env_setup
    job_id = "job-qa-fail"
    _, checksum = _seed_job_with_media(env, job_id=job_id, status="APPROVED")
    _seed_qa_receipt(env, job_id=job_id, status="BLOCK", publish_allowed=False, checksum=checksum)
    _seed_approval(env, job_id=job_id, status="approved")

    res = env["engine"].evaluate_job_publishability(job_id)
    assert res["publishable"] is False
    assert "Publishing blocked by QA Gate" in res["reason"]
    assert env["engine"].is_job_publishable(job_id) is False


def test_checksum_mismatch_qa_receipt(env_setup):
    """Media checksum does not match the checksum recorded in QA receipt."""
    env = env_setup
    job_id = "job-qa-checksum-mismatch"
    _, checksum = _seed_job_with_media(env, job_id=job_id, status="APPROVED")
    _seed_qa_receipt(env, job_id=job_id, checksum="mismatched-qa-checksum")
    _seed_approval(env, job_id=job_id, status="approved")

    res = env["engine"].evaluate_job_publishability(job_id)
    assert res["publishable"] is False
    assert "does not match QA receipt checksum" in res["reason"]
    assert env["engine"].is_job_publishable(job_id) is False


def test_checksum_mismatch_post_approval_tampering(env_setup):
    """Media was altered after operator approved the job (bound checksum mismatch)."""
    env = env_setup
    job_id = "job-tampered"
    media_file, checksum = _seed_job_with_media(env, job_id=job_id, status="APPROVED")
    _seed_qa_receipt(env, job_id=job_id, checksum=checksum)
    _seed_approval(env, job_id=job_id, status="approved")

    # Tamper with the media file
    media_file.write_bytes(b"tampered-bytes-after-approval")

    res = env["engine"].evaluate_job_publishability(job_id)
    assert res["publishable"] is False
    assert "does not match" in res["reason"]
    assert env["engine"].is_job_publishable(job_id) is False
    assert env["engine"].count_publishable_jobs() == 0


def test_rejected_approval_is_not_publishable(env_setup):
    """Operator explicitly rejected the job."""
    env = env_setup
    job_id = "job-rejected"
    _, checksum = _seed_job_with_media(env, job_id=job_id, status="APPROVED")
    _seed_qa_receipt(env, job_id=job_id, checksum=checksum)
    _seed_approval(env, job_id=job_id, status="rejected")

    res = env["engine"].evaluate_job_publishability(job_id)
    assert res["publishable"] is False
    assert "rejected" in res["reason"].lower()
    assert env["engine"].is_job_publishable(job_id) is False


# ---------------------------------------------------------------------------
# Bridge Handlers & UI Readiness Semantics Tests
# ---------------------------------------------------------------------------

def test_bridge_publishing_status_reflects_true_readiness(env_setup):
    """publishing.status must return ready_to_publish based on true publishability."""
    env = env_setup
    h = env["handlers"]

    # 1 unapproved job with status APPROVED
    _, cs1 = _seed_job_with_media(env, job_id="job-unapproved", status="APPROVED")
    _seed_qa_receipt(env, job_id="job-unapproved", checksum=cs1)

    status_res = h.dispatch("publishing.status", None)
    assert status_res["ready_to_publish"] == 0
    assert status_res["counts"]["ready"] == 0

    # 1 genuinely ready job
    _, cs2 = _seed_job_with_media(env, job_id="job-ready", status="APPROVED")
    _seed_qa_receipt(env, job_id="job-ready", checksum=cs2)
    _seed_approval(env, job_id="job-ready", status="approved")

    status_res = h.dispatch("publishing.status", None)
    assert status_res["ready_to_publish"] == 1
    assert status_res["counts"]["ready"] == 1


def test_bridge_publishing_list_ready_semantics(env_setup):
    """publishing.list_ready returns items with publishable flag, reason, and summary."""
    env = env_setup
    h = env["handlers"]

    # Seed 1 unapproved job
    _, cs1 = _seed_job_with_media(env, job_id="job-unapproved", status="APPROVED")
    _seed_qa_receipt(env, job_id="job-unapproved", checksum=cs1)

    # Seed 1 fully ready job
    _, cs2 = _seed_job_with_media(env, job_id="job-ready", status="APPROVED")
    _seed_qa_receipt(env, job_id="job-ready", checksum=cs2)
    _seed_approval(env, job_id="job-ready", status="approved")

    # List all
    list_res = h.dispatch("publishing.list_ready", {"limit": 10})
    assert list_res["summary"]["ready_to_publish"] == 1
    items = {item["job_id"]: item for item in list_res["items"]}

    assert items["job-unapproved"]["publishable"] is False
    assert items["job-unapproved"]["publishability_reason"] is not None
    assert "approval" in items["job-unapproved"]["publishability_reason"].lower()

    assert items["job-ready"]["publishable"] is True
    assert items["job-ready"]["publishability_reason"] is None

    # Filter with ready_only=True
    ready_only_res = h.dispatch("publishing.list_ready", {"limit": 10, "ready_only": True})
    assert len(ready_only_res["items"]) == 1
    assert ready_only_res["items"][0]["job_id"] == "job-ready"
    assert ready_only_res["summary"]["ready_to_publish"] == 1


def test_bridge_health_get_and_autonomy_status(env_setup):
    """health.get and autonomy.status must report accurate ready counts."""
    env = env_setup
    h = env["handlers"]

    # Seed 1 unapproved job
    _, cs1 = _seed_job_with_media(env, job_id="job-unapproved", status="APPROVED")
    _seed_qa_receipt(env, job_id="job-unapproved", checksum=cs1)

    health_res = h.dispatch("health.get", None)
    assert health_res["publishing"]["counts"]["ready"] == 0

    autonomy_res = h.dispatch("autonomy.status", None)
    assert autonomy_res["activity"]["ready_to_publish"] == 0

    # Seed approval for job
    _seed_approval(env, job_id="job-unapproved", status="approved")

    health_res2 = h.dispatch("health.get", None)
    assert health_res2["publishing"]["counts"]["ready"] == 1

    autonomy_res2 = h.dispatch("autonomy.status", None)
    assert autonomy_res2["activity"]["ready_to_publish"] == 1


def test_bridge_publishing_inspect(env_setup):
    """publishing.inspect returns consistent publishable boolean and reason."""
    env = env_setup
    h = env["handlers"]

    _, cs = _seed_job_with_media(env, job_id="job-inspect", status="APPROVED")
    _seed_qa_receipt(env, job_id="job-inspect", checksum=cs)

    # Before approval
    inspect_unapp = h.dispatch("publishing.inspect", {"job_id": "job-inspect"})
    assert inspect_unapp["publishable"] is False
    assert "approval" in inspect_unapp["publishability_reason"].lower()

    # Approve
    _seed_approval(env, job_id="job-inspect", status="approved")
    inspect_app = h.dispatch("publishing.inspect", {"job_id": "job-inspect"})
    assert inspect_app["publishable"] is True
    assert inspect_app["publishability_reason"] is None
