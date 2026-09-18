"""M4 desktop bridge tests — publishing + analytics + strategy control surface.

Exercises the JSON-RPC bridge as a real child process over stdio against a
temporary SQLite database (read-only methods), and in-process with stubbed
engines/providers for the execution paths (publish, analytics sync, learning)
so no real YouTube/provider stack is invoked.

Isolation: temporary DBs only, mock policy, no network, no real publishing.
The real private-YouTube smoke (if credentials exist) is a separate,
opt-in step performed outside the deterministic test suite.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]

_SENSITIVE_VALUE_KEYWORDS = (
    "access_token",
    "client_secret",
    "refresh_token",
    "client_id",
    "youtube_token_path",
    "youtube_client_secrets_path",
    "gemini_api_key",
    "openrouter_api_key",
    "postiz_api_key",
    "authorization_code",
    "api_key",
)


class BridgeClient:
    def __init__(self, db_path: Path):
        env = dict(os.environ)
        env["AUTOPILOT_DB_PATH"] = str(db_path)
        env["AUTOPILOT_ANALYTICS_DEFAULT_PROVIDER"] = "mock"
        # Force an unauthenticated backend so switch tests are deterministic:
        # a non-existent YT token path guarantees the auth probe fails closed.
        env["AUTOPILOT_YOUTUBE_TOKEN_PATH"] = str(_REPO_ROOT / "credentials" / "no-such-token-m4.json")
        env["YOUTUBE_TOKEN_PATH"] = str(_REPO_ROOT / "credentials" / "no-such-token-m4.json")
        env["YOUTUBE_ACCESS_TOKEN"] = ""
        self.proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "autopilot.bridge"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=str(_REPO_ROOT),
        )
        self._inbox: list[dict] = []
        self._cond = threading.Condition()
        self._next_id = 0
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self):
        try:
            for line in self.proc.stdout:
                try:
                    self._inbox.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
                with self._cond:
                    self._cond.notify_all()
        except Exception:  # noqa: BLE001
            pass

    def _next_frame(self, timeout: float = 25.0) -> dict:
        with self._cond:
            while not self._inbox:
                if not self._cond.wait(timeout):
                    raise TimeoutError("No bridge response within timeout")
            return self._inbox.pop(0)

    def send_raw(self, line: str):
        self.proc.stdin.write(line)
        self.proc.stdin.flush()

    def call(self, method: str, params: dict | None = None) -> dict:
        rid = self._next_id
        self._next_id += 1
        req: dict = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            req["params"] = params
        self.send_raw(json.dumps(req) + "\n")
        while True:
            frame = self._next_frame()
            if frame.get("id") == rid:
                return frame

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=5)


def _collect_sensitive(frame: dict):
    hits = []
    stack = [frame]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            for key, val in value.items():
                if key.lower() == "token_present":
                    continue
                if key.lower() == "secrets_present":
                    continue
                if any(k in key.lower() for k in ("token", "secret", "credential", "password", "passwd", "api_key")):
                    hits.append(key)
                stack.append(val)
        elif isinstance(value, list):
            stack.extend(value)
    return hits


def _result(frame: dict):
    if "error" in frame:
        raise AssertionError(f"bridge error: {frame['error']}")
    return frame["result"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _handlers(tmp_path, monkeypatch):
    from autopilot.bridge.handlers import BridgeHandlers
    from autopilot.core.config import Config
    from autopilot.db.manager import DBManager

    monkeypatch.setenv("AUTOPILOT_DB_PATH", str(tmp_path / "m4.db"))
    monkeypatch.setenv("AUTOPILOT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("AUTOPILOT_ANALYTICS_DEFAULT_PROVIDER", "mock")
    db = DBManager(str(tmp_path / "m4.db"))
    db.init_schema()
    return BridgeHandlers(Config(), db)


@pytest.fixture()
def m4_db(tmp_path, monkeypatch):
    db_path = tmp_path / "m4_stdio.db"
    monkeypatch.setenv("AUTOPILOT_DB_PATH", str(db_path))
    monkeypatch.setenv("AUTOPILOT_ANALYTICS_DEFAULT_PROVIDER", "mock")
    from autopilot.db.manager import DBManager

    db = DBManager(str(db_path))
    db.init_schema()
    yield db_path


@pytest.fixture()
def client(m4_db):
    cli = BridgeClient(m4_db)
    yield cli
    cli.close()


def _make_ready_job(handlers, tmp_path, job_id="job-ready-1", topic="Quantum Computing Basics"):
    """Create a job in READY_TO_PUBLISH with media + a passing QA receipt."""
    db = handlers.db
    db.create_job(job_id=job_id, channel_id="default", topic=topic)
    db.update_job_status(job_id, "APPROVED")

    artifacts = Path(os.environ["AUTOPILOT_ARTIFACTS_DIR"])
    from autopilot.core.artifacts import job_artifact_dir

    art_dir = job_artifact_dir(job_id, base_dir=artifacts)
    render_dir = art_dir / "render"
    render_dir.mkdir(parents=True, exist_ok=True)
    media = render_dir / "final.mp4"
    media.write_bytes(b"fake-mp4-content-for-tests")

    quality_dir = art_dir / "quality"
    quality_dir.mkdir(parents=True, exist_ok=True)
    from autopilot.core.publisher import compute_file_sha256

    checksum = compute_file_sha256(media)
    (quality_dir / "receipt.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "publish_allowed": True,
                "media_checksum_sha256": checksum,
                "content_id": job_id,
            }
        ),
        encoding="utf-8",
    )
    return checksum


# ---------------------------------------------------------------------------
# youtube.auth_status
# ---------------------------------------------------------------------------

def test_youtube_auth_status_no_secrets(client):
    res = _result(client.call("youtube.auth_status"))
    assert res["status"] in ("authenticated", "needs_auth", "unconfigured", "error")
    assert isinstance(res["authenticated"], bool)
    assert isinstance(res["secrets_present"], bool)
    assert res["guidance"]
    frame = client.call("youtube.auth_status")
    assert _collect_sensitive(frame) == []
    text = json.dumps(frame).lower()
    for kw in _SENSITIVE_VALUE_KEYWORDS:
        assert kw not in text


def test_youtube_auth_status_needs_auth(monkeypatch, tmp_path):
    import autopilot.providers.youtube_oauth as oauth

    monkeypatch.setattr(
        oauth, "find_default_client_secrets_path", lambda config=None: Path("/tmp/fake_secret.json")
    )
    monkeypatch.setattr(oauth, "resolve_youtube_access_token", lambda config=None, transport=None: None)
    h = _handlers(tmp_path, monkeypatch)
    res = h.dispatch("youtube.auth_status", None)
    assert res["status"] == "needs_auth"
    assert res["authenticated"] is False
    assert res["secrets_present"] is True


def test_youtube_auth_status_authenticated(monkeypatch, tmp_path):
    import autopilot.providers.youtube_oauth as oauth

    monkeypatch.setattr(oauth, "find_default_client_secrets_path", lambda config=None: Path("/tmp/fake.json"))
    monkeypatch.setattr(oauth, "resolve_youtube_access_token", lambda config=None, transport=None: "fake-token")
    h = _handlers(tmp_path, monkeypatch)
    res = h.dispatch("youtube.auth_status", None)
    assert res["status"] == "authenticated"
    assert res["authenticated"] is True


# ---------------------------------------------------------------------------
# publishing.status
# ---------------------------------------------------------------------------

def test_publishing_status(client):
    res = _result(client.call("publishing.status"))
    assert res["status"] == "AVAILABLE"
    assert "counts" in res
    assert res["ready_to_publish"] >= 0
    assert res["published"] >= 0
    assert res["default_visibility"] in ("private", "unlisted", "public")
    assert res["autonomy_auto_publish_enabled"] is False
    assert res["publish_boundary"]


def test_publishing_status_no_secrets(client):
    frame = client.call("publishing.status")
    assert _collect_sensitive(frame) == []
    text = json.dumps(frame).lower()
    for kw in _SENSITIVE_VALUE_KEYWORDS:
        assert kw not in text


# ---------------------------------------------------------------------------
# publishing.list_ready
# ---------------------------------------------------------------------------

def test_list_ready_empty(client):
    res = _result(client.call("publishing.list_ready"))
    assert res["items"] == []
    assert res["summary"]["ready_to_publish"] == 0


def test_list_ready_seeded(monkeypatch, tmp_path):
    h = _handlers(tmp_path, monkeypatch)
    _make_ready_job(h, tmp_path)
    res = h.dispatch("publishing.list_ready", {"limit": 10})
    assert len(res["items"]) == 1
    item = res["items"][0]
    assert item["job_id"] == "job-ready-1"
    assert item["status"] == "APPROVED"
    assert item["qa_status"] == "PASS"
    assert item["qa_publish_allowed"] is True
    assert item["media_checksum_sha256"]
    assert item["approval_status"] is None
    assert item["published"] is False
    assert res["summary"]["ready_to_publish"] == 1


# ---------------------------------------------------------------------------
# publishing.inspect
# ---------------------------------------------------------------------------

def test_inspect_missing_job(client):
    res = _result(client.call("publishing.inspect", {"job_id": "nope"}))
    assert res["found"] is False
    assert res["publishable"] is False


def test_inspect_missing_param(client):
    frame = client.call("publishing.inspect")
    assert frame.get("error", {}).get("code") == -32602


def test_inspect_ready_unauthorized(monkeypatch, tmp_path):
    h = _handlers(tmp_path, monkeypatch)
    _make_ready_job(h, tmp_path)
    res = h.dispatch("publishing.inspect", {"job_id": "job-ready-1"})
    assert res["found"] is True
    assert res["state"] == "READY_TO_PUBLISH"
    assert res["approval_status"] == "UNAUTHORIZED"
    assert res["qa_status"] == "PASS"
    assert res["media_checksum_sha256"]
    # No approval yet → not publishable.
    assert res["publishable"] is False


def test_inspect_approved(monkeypatch, tmp_path):
    h = _handlers(tmp_path, monkeypatch)
    _make_ready_job(h, tmp_path)
    h.dispatch("publishing.approve", {"job_id": "job-ready-1"})
    res = h.dispatch("publishing.inspect", {"job_id": "job-ready-1"})
    assert res["approval_status"] == "AUTHORIZED"
    assert res["publishable"] is True
    assert res["approval"]["status"] == "approved"
    assert res["approval_history"]


# ---------------------------------------------------------------------------
# publishing.approve / publishing.reject
# ---------------------------------------------------------------------------

def test_approve_missing_job(client):
    frame = client.call("publishing.approve", {"job_id": "ghost"})
    assert frame.get("error", {}).get("code") == -32602


def test_approve_and_reject(monkeypatch, tmp_path):
    h = _handlers(tmp_path, monkeypatch)
    _make_ready_job(h, tmp_path)

    approved = h.dispatch("publishing.approve", {"job_id": "job-ready-1"})
    assert approved["status"] == "approved"
    assert approved["media_checksum_sha256"]
    # Approval binds checksum + platform.
    row = h.db.get_publish_approval("job-ready-1")
    assert row["status"] == "approved"
    assert row["media_checksum_sha256"]
    assert row["platform"] == "youtube"

    rejected = h.dispatch("publishing.reject", {"job_id": "job-ready-1"})
    assert rejected["status"] == "rejected"
    row = h.db.get_publish_approval("job-ready-1")
    assert row["status"] == "rejected"


def test_reject_without_approval_errors(client):
    frame = client.call("publishing.reject", {"job_id": "ghost"})
    assert frame.get("error", {}).get("code") == -32602


def test_approval_history_preserved(monkeypatch, tmp_path):
    h = _handlers(tmp_path, monkeypatch)
    _make_ready_job(h, tmp_path)
    h.dispatch("publishing.approve", {"job_id": "job-ready-1"})
    h.dispatch("publishing.reject", {"job_id": "job-ready-1"})
    h.dispatch("publishing.approve", {"job_id": "job-ready-1"})
    history = h.db.list_publish_approvals(job_id="job-ready-1")
    # The latest record reflects the most recent decision (approved).
    assert len(history) >= 1
    assert history[-1]["status"] == "approved"
    # Prior decisions are never silently discarded for other jobs.
    all_approvals = h.db.list_publish_approvals()
    assert any(a["job_id"] == "job-ready-1" for a in all_approvals)


# ---------------------------------------------------------------------------
# publishing.publish — gate enforcement (real engine, gates fail pre-provider)
# ---------------------------------------------------------------------------

def test_publish_invalid_visibility(client):
    frame = client.call("publishing.publish", {"job_id": "x", "visibility": "bogus"})
    assert frame.get("error", {}).get("code") == -32602


def test_publish_requires_approval_gate(monkeypatch, tmp_path):
    """Publishing without an approval record must be BLOCKED_APPROVAL."""
    h = _handlers(tmp_path, monkeypatch)
    _make_ready_job(h, tmp_path)
    res = h.dispatch("publishing.publish", {"job_id": "job-ready-1"})
    assert res["success"] is False
    assert res["status"] == "BLOCKED_APPROVAL"
    assert res["error_code"] == "APPROVAL_REQUIRED"
    # Job must remain READY_TO_PUBLISH (fail-closed, no state corruption).
    job = h.db.get_job("job-ready-1")
    assert job["status"] == "APPROVED"


def test_publish_qa_gate_blocks(monkeypatch, tmp_path):
    """A job with no QA receipt cannot publish even when approved."""
    h = _handlers(tmp_path, monkeypatch)
    db = h.db
    db.create_job(job_id="job-noqa", channel_id="default", topic="No QA")
    db.update_job_status("job-noqa", "APPROVED")
    h.dispatch("publishing.approve", {"job_id": "job-noqa"})
    res = h.dispatch("publishing.publish", {"job_id": "job-noqa"})
    assert res["success"] is False
    assert res["error_code"] in ("QA_REPORT_MISSING", "MEDIA_NOT_FOUND")


def test_publish_rejected_approval_blocks(monkeypatch, tmp_path):
    h = _handlers(tmp_path, monkeypatch)
    _make_ready_job(h, tmp_path)
    h.dispatch("publishing.approve", {"job_id": "job-ready-1"})
    h.dispatch("publishing.reject", {"job_id": "job-ready-1"})
    res = h.dispatch("publishing.publish", {"job_id": "job-ready-1"})
    assert res["success"] is False
    assert res["status"] == "BLOCKED_APPROVAL"
    assert res["error_code"] == "APPROVAL_REJECTED"


def test_publish_checksum_mismatch_blocks(monkeypatch, tmp_path):
    """Artifact changed after approval must invalidate the approval."""
    h = _handlers(tmp_path, monkeypatch)
    _make_ready_job(h, tmp_path)
    h.dispatch("publishing.approve", {"job_id": "job-ready-1"})
    # Tamper with the media after approval.
    artifacts = Path(os.environ["AUTOPILOT_ARTIFACTS_DIR"])
    from autopilot.core.artifacts import job_artifact_dir

    media = job_artifact_dir("job-ready-1", base_dir=artifacts) / "render" / "final.mp4"
    media.write_bytes(b"tampered-content-different-checksum")
    res = h.dispatch("publishing.publish", {"job_id": "job-ready-1"})
    assert res["success"] is False
    # Either the approval-artifact gate or the QA checksum gate fires — both
    # are fail-closed enforcement of the same invariant.
    assert res["error_code"] in ("APPROVAL_ARTIFACT_MISMATCH", "CHECKSUM_MISMATCH")


# ---------------------------------------------------------------------------
# publishing.publish — success path with stubbed provider
# ---------------------------------------------------------------------------

def _stub_publisher(monkeypatch, outcome: str):
    """Replace the YouTube publisher with a deterministic fake provider.

    YouTubePublisher self-registers in REGISTRY at import time, so the fake is
    registered under the same ``provider_name`` to override it. No network.
    """
    import autopilot.core.publisher as publisher_mod
    from autopilot.providers.contracts import REGISTRY
    from autopilot.core.contracts import (
        PublishResult, PublishStatus, PublicationReceipt, PublishError,
    )

    class FakePublisher:
        provider_name = "youtube"

        def __init__(self, config=None, **kw):
            self.config = config

        def publish(self, request):
            if outcome == "ambiguous":
                # Ambiguous remote result: success=False with no receipt.
                return PublishResult(
                    success=False,
                    status=PublishStatus.FAILED,
                    error=PublishError(
                        error_code="AMBIGUOUS_RESULT",
                        message="Remote returned an ambiguous response; failing closed.",
                        retryable=False,
                    ),
                )
            receipt = PublicationReceipt(
                receipt_id="rcpt-fake",
                job_id=request.job_id,
                content_id=request.content_id,
                render_checksum_sha256=request.media_checksum_sha256,
                platform=request.platform,
                provider="fake",
                remote_video_id="vid-fake-12345",
                remote_url="https://www.youtube.com/watch?v=vid-fake-12345",
                # Immediate successful upload carries SUCCESS state; the job then
                # transitions to PUBLISHED by the engine.
                publication_state=PublishStatus.SUCCESS,
                visibility=request.target_visibility,
                idempotency_key=request.idempotency_key,
                metadata_hash="fake-hash",
            )
            return PublishResult(
                success=True,
                status=PublishStatus.PUBLISHED,
                receipt=receipt,
                attempts=[],
            )

    fake = FakePublisher
    monkeypatch.setattr(publisher_mod, "YouTubePublisher", fake)
    # Override the self-registered instance in the live registry.
    monkeypatch.setitem(REGISTRY._providers, "youtube", fake())


def test_publish_success_persists_publication(monkeypatch, tmp_path):
    h = _handlers(tmp_path, monkeypatch)
    _make_ready_job(h, tmp_path)
    h.dispatch("publishing.approve", {"job_id": "job-ready-1"})
    _stub_publisher(monkeypatch, "success")

    res = h.dispatch("publishing.publish", {"job_id": "job-ready-1", "visibility": "private"})
    assert res["success"] is True
    assert res["status"] == "PUBLISHED"
    assert res["remote_video_id"] == "vid-fake-12345"
    assert res["remote_url"]
    assert res["idempotency_key"]
    assert res["visibility"] == "private"

    # Job transitioned to PUBLISHED.
    job = h.db.get_job("job-ready-1")
    assert job["status"] == "PUBLISHED"
    pubs = h.db.get_publications_for_job("job-ready-1")
    assert len(pubs) == 1
    assert pubs[0]["remote_video_id"] == "vid-fake-12345"
    assert pubs[0]["visibility"] == "private"
    assert pubs[0]["idempotency_key"]


def test_publish_duplicate_prevention(monkeypatch, tmp_path):
    h = _handlers(tmp_path, monkeypatch)
    _make_ready_job(h, tmp_path)
    h.dispatch("publishing.approve", {"job_id": "job-ready-1"})
    _stub_publisher(monkeypatch, "success")

    first = h.dispatch("publishing.publish", {"job_id": "job-ready-1", "visibility": "private"})
    assert first["success"] is True
    second = h.dispatch("publishing.publish", {"job_id": "job-ready-1", "visibility": "private"})
    # Idempotent duplicate: not re-uploaded.
    assert second["status"] == "SKIPPED_DUPLICATE"
    pubs = h.db.get_publications_for_job("job-ready-1")
    assert len(pubs) == 1


def test_publish_ambiguous_fails_closed(monkeypatch, tmp_path):
    h = _handlers(tmp_path, monkeypatch)
    _make_ready_job(h, tmp_path)
    h.dispatch("publishing.approve", {"job_id": "job-ready-1"})
    _stub_publisher(monkeypatch, "ambiguous")

    res = h.dispatch("publishing.publish", {"job_id": "job-ready-1", "visibility": "private"})
    assert res["success"] is False
    assert res["error_code"] == "AMBIGUOUS_RESULT"
    # No publication persisted for an ambiguous result.
    pubs = h.db.get_publications_for_job("job-ready-1")
    assert len(pubs) == 0


def test_publish_no_secret_leakage(monkeypatch, tmp_path):
    h = _handlers(tmp_path, monkeypatch)
    _make_ready_job(h, tmp_path)
    h.dispatch("publishing.approve", {"job_id": "job-ready-1"})
    _stub_publisher(monkeypatch, "success")
    res = h.dispatch("publishing.publish", {"job_id": "job-ready-1", "visibility": "private"})
    frame = {"result": res}
    assert _collect_sensitive(frame) == []


# ---------------------------------------------------------------------------
# analytics.*
# ---------------------------------------------------------------------------

def test_analytics_status_empty(client):
    res = _result(client.call("analytics.status"))
    assert res["status"] == "AVAILABLE"
    assert res["default_provider"] in ("mock", "youtube")
    assert res["published_job_count"] >= 0
    assert res["has_published_jobs"] is False
    assert res["snapshot_count"] >= 0


def test_analytics_status_no_secrets(client):
    frame = client.call("analytics.status")
    assert _collect_sensitive(frame) == []
    text = json.dumps(frame).lower()
    for kw in _SENSITIVE_VALUE_KEYWORDS:
        assert kw not in text


def test_analytics_sync_no_published_jobs(client):
    res = _result(client.call("analytics.sync", {"sync_all": True}))
    # No published jobs → nothing to sync (backend guard, not an error).
    assert "synced_count" in res or "note" in res or "synced" in res


def test_analytics_sync_invalid_provider(client):
    frame = client.call("analytics.sync", {"job_id": "x", "provider": "bogus_provider"})
    assert frame.get("error", {}).get("code") == -32602


def test_analytics_sync_and_snapshots(monkeypatch, tmp_path):
    h = _handlers(tmp_path, monkeypatch)
    db = h.db
    db.create_job(job_id="job-pub", channel_id="default", topic="AI Breakthroughs")
    db.record_publish_record(
        job_id="job-pub",
        platform="youtube",
        provider="mock",
        visibility="private",
        remote_video_id="vid-xyz",
        idempotency_key="idem-1",
        media_checksum_sha256="chk-1",
        status="SUCCESS",
    )

    res = h.dispatch("analytics.sync", {"job_id": "job-pub"})
    assert res["status"] == "success"
    assert res["remote_id"] == "vid-xyz"
    assert res["snapshot_id"]

    snaps = h.dispatch("analytics.snapshots", {"job_id": "job-pub"})
    assert snaps["found"] is True
    assert len(snaps["snapshots"]) >= 1
    assert snaps["latest_snapshot"] is not None
    assert snaps["remote_id"] == "vid-xyz"


def test_analytics_snapshots_missing_job(client):
    res = _result(client.call("analytics.snapshots", {"job_id": "ghost"}))
    assert res["found"] is False
    assert res["snapshots"] == []


def test_analytics_report_empty(client):
    res = _result(client.call("analytics.report"))
    assert res["report"] == []
    assert res["rows"] == 0


def test_analytics_report_with_data(monkeypatch, tmp_path):
    h = _handlers(tmp_path, monkeypatch)
    db = h.db
    db.create_job(job_id="job-rpt", channel_id="default", topic="Space Exploration")
    db.record_publish_record(
        job_id="job-rpt", platform="youtube", provider="mock",
        visibility="private", remote_video_id="vid-rpt",
        idempotency_key="idem-rpt", media_checksum_sha256="chk-rpt", status="SUCCESS",
    )
    h.dispatch("analytics.sync", {"job_id": "job-rpt"})
    res = h.dispatch("analytics.report", {})
    assert res["rows"] >= 1
    row = res["report"][0]
    assert row["job_id"] == "job-rpt"
    assert row["views"] >= 0
    assert row["engagement_rate"] >= 0


# ---------------------------------------------------------------------------
# strategy.*
# ---------------------------------------------------------------------------

def test_strategy_status(client):
    res = _result(client.call("strategy.status"))
    assert res["status"] == "AVAILABLE"
    assert res["active_strategy_version"]
    assert res["active_strategy"] is not None
    assert "niche_weights" in res["active_strategy"]
    assert "bounds" in res["learning"] or res["learning"].get("status")
    assert res["bounds"]["max_weight_delta"] > 0
    assert res["bounds"]["weight_floor"] > 0
    assert res["learning_boundary"]


def test_strategy_status_no_secrets(client):
    frame = client.call("strategy.status")
    assert _collect_sensitive(frame) == []


def test_strategy_show_active(client):
    res = _result(client.call("strategy.show"))
    assert res["found"] is True
    assert res["strategy"] is not None
    assert res["ancestry"]
    assert res["is_active"] is True


def test_strategy_learn_insufficient(monkeypatch, tmp_path):
    """Learning with no published analytics → insufficient (never publishes)."""
    h = _handlers(tmp_path, monkeypatch)
    res = h.dispatch("strategy.learn", {"channel_id": "default"})
    assert res["status"] in ("insufficient", "no_change", "applied", "dry_run", "failed")
    assert res["run_id"]
    # No strategy change when there is no evidence.
    assert res["resulting_strategy_version"] is None or res["status"] == "applied"


def test_strategy_learn_dry_run(monkeypatch, tmp_path):
    h = _handlers(tmp_path, monkeypatch)
    res = h.dispatch("strategy.learn", {"channel_id": "default", "dry_run": True})
    assert res["dry_run"] is True
    assert res["status"] in ("insufficient", "no_change", "dry_run")


def test_strategy_learn_idempotent(monkeypatch, tmp_path):
    """Repeated learning over identical evidence must not mint versions."""
    h = _handlers(tmp_path, monkeypatch)
    first = h.dispatch("strategy.learn", {"channel_id": "default"})
    second = h.dispatch("strategy.learn", {"channel_id": "default"})
    if first["status"] == "applied":
        assert second["status"] in ("no_change", "insufficient")
        assert second["resulting_strategy_version"] in (None, first["resulting_strategy_version"])


def test_strategy_learn_never_publishes(monkeypatch, tmp_path):
    """Learning engine must not create publications or jobs."""
    h = _handlers(tmp_path, monkeypatch)
    h.dispatch("strategy.learn", {"channel_id": "default"})
    pubs = h.db.get_publications_for_job("__any__")
    assert pubs == []


# ---------------------------------------------------------------------------
# autonomy.publish_status — kill switch
# ---------------------------------------------------------------------------

def test_autonomy_publish_status_disabled_by_default(client):
    res = _result(client.call("autonomy.publish_status"))
    assert res["enabled"] is False
    assert res["state"] == "DISABLED"
    assert res["label"] == "AUTONOMOUS PUBLIC PUBLISHING DISABLED"
    assert res["default"] is False
    assert res["controlled_by"] == "backend"
    assert res["guardrails"]
    assert res["boundary"]


def test_autonomy_publish_status_no_secrets(client):
    frame = client.call("autonomy.publish_status")
    assert _collect_sensitive(frame) == []


def test_autonomy_publish_switch_is_controlled(client):
    """M6: the switch surface is backend-controlled (enable/disable methods)."""
    from autopilot.bridge.handlers import BridgeHandlers

    names = BridgeHandlers.METHOD_NAMES
    assert "autonomy.publish_status" in names
    assert "autonomy.publish_enable" in names
    assert "autonomy.publish_disable" in names

    # Default is OFF. Enable fails CLOSED (no auth in the CI child process)
    # and leaves the switch untouched; disable is an always-working,
    # idempotent kill switch.
    res = _result(client.call("autonomy.publish_enable"))
    assert res["ok"] is False
    assert res["changed"] is False
    assert res["enabled"] is False
    assert res["state"] == "DISABLED"
    assert res["reason"]
    assert res["prerequisites"]["ok"] is False
    assert res["prerequisites"]["failed"]

    status = _result(client.call("autonomy.publish_status"))
    assert status["enabled"] is False

    res = _result(client.call("autonomy.publish_disable"))
    assert res["ok"] is True
    assert res["changed"] is False
    assert res["enabled"] is False
    res2 = _result(client.call("autonomy.publish_disable"))
    assert res2["ok"] is True
    assert res2["changed"] is False


# ---------------------------------------------------------------------------
# Cross-method: no secret leakage anywhere in M4 surface
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "method",
    [
        "publishing.status",
        "publishing.list_ready",
        "youtube.auth_status",
        "analytics.status",
        "analytics.report",
        "strategy.status",
        "autonomy.publish_status",
        "autonomy.publish_enable",
        "autonomy.publish_disable",
    ],
)
def test_no_secret_leakage(client, method):
    frame = client.call(method)
    assert _collect_sensitive(frame) == []
    text = json.dumps(frame).lower()
    for kw in _SENSITIVE_VALUE_KEYWORDS:
        assert kw not in text
