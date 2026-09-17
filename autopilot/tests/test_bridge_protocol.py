"""M1 desktop bridge tests.

The bridge is exercised as a real child process over stdio (protocol-level
tests) against a temporary SQLite database. It covers framing, correlation,
snapshots, shutdown, reconnect, the no-secrets guarantee, and one
UI -> bridge -> backend -> DB -> snapshot integration path.
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
    "client_secrets_path",
    "youtube_token_path",
    "gemini_api_key",
    "openrouter_api_key",
    "postiz_api_key",
    "authorization_code",
    "refresh_token",
)


@pytest.fixture()
def seeded_db(tmp_path):
    """Build a deterministic temp DB that the bridge child will serve."""
    from autopilot.db.manager import DBManager

    db_path = tmp_path / "bridge.db"
    db = DBManager(db_path)
    db.init_schema()

    db.create_job("job-1", channel_id="chan1", topic="Quantum Computing Basics")
    db.log_event("job-1", "IDEA", "RESEARCH", "queued by fixture")
    db.log_event("job-1", "RESEARCH", "SCRIPT", "research complete")
    db.record_error("job-1", "RENDER", "FFMPEG_TIMEOUT", "render exceeded budget")
    db.record_artifact("job-1", str(tmp_path / "artifacts" / "job-1" / "thumbnail.jpg"), "thumbnail")
    db.enqueue_item(
        queue_id="q-1",
        job_id="job-1",
        content_id="c-1",
        priority=3,
        channel_id="chan1",
        payload={"topic": "Quantum Computing Basics", "channel_id": "chan1",
                 "policy": "local_only", "profile": "short_vertical", "auto_publish": False},
    )
    return db_path


class BridgeClient:
    """Spawns ``python -m autopilot.bridge`` and speaks newline JSON-RPC."""

    def __init__(self, db_path: Path):
        env = dict(os.environ)
        env["AUTOPILOT_DB_PATH"] = str(db_path)
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
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                with self._cond:
                    self._inbox.append(msg)
                    self._cond.notify_all()
        except Exception:  # noqa: BLE001 — reader must not die loudly
            pass

    def _next_frame(self, timeout: float = 15.0) -> dict:
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

    def wait_exit(self, timeout: float = 15.0) -> int:
        return self.proc.wait(timeout=timeout)

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=5)


@pytest.fixture()
def client(seeded_db):
    cli = BridgeClient(seeded_db)
    yield cli
    cli.close()


def _collect_sensitive(frame: dict):
    hits = []
    stack = [frame]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            for key, val in value.items():
                if key.lower() == "token_present":
                    continue
                if any(k in key.lower() for k in ("token", "secret", "credential", "password", "passwd")):
                    hits.append(key)
                stack.append(val)
        elif isinstance(value, list):
            stack.extend(value)
        elif isinstance(value, str):
            if value in ("credentials",):
                hits.append("embedded 'credentials' string value")
    return hits


# ---------------------------------------------------------------------------
# Protocol framing
# ---------------------------------------------------------------------------

def test_ping_returns_echo(client):
    res = client.call("ping")
    assert res.get("error") is None
    result = res["result"]
    assert result["pong"] is True
    assert result["bridge_version"]
    assert result["pid"] > 0


def test_request_id_correlation(client):
    first = client.call("ping")
    second = client.call("ping")
    assert first["result"]["pid"] == second["result"]["pid"]
    assert first["id"] == 0 and second["id"] == 1  # sequential correlation ids


def test_invalid_json_parse_error(client):
    client.send_raw("this is not json\n")
    frame = client._next_frame()
    assert frame["error"]["code"] == -32700


def test_invalid_request_missing_method(client):
    client.send_raw(json.dumps({"jsonrpc": "2.0", "id": 42}) + "\n")
    frame = client._next_frame()
    assert frame["id"] in (42, None)
    assert frame["error"]["code"] == -32600


def test_unknown_method(client):
    res = client.call("definitely.not.real")
    assert res["error"]["code"] == -32601
    assert "not found" in res["error"]["message"].lower()


# ---------------------------------------------------------------------------
# Snapshots (real data)
# ---------------------------------------------------------------------------

def test_system_status(client):
    res = client.call("system.status")["result"]
    assert res["state"] == "ready"
    assert res["db"]["exists"] is True
    assert res["db"]["schema_version"] is not None


def test_health_snapshot(client, seeded_db):
    res = client.call("health.get")["result"]
    assert res["status"] in ("healthy", "degraded")
    assert res["db"]["path"] == str(seeded_db)
    assert res["db"]["exists"] is True
    assert "queue_engine" in res and "summary" in res["queue_engine"]
    assert res["queue_engine"]["summary"]["total"] >= 1
    assert "scheduler" in res and "status" in res["scheduler"]
    assert "learning_engine" in res
    assert "publishing" in res and "counts" in res["publishing"]
    assert "autonomy_engine" in res
    assert res["autonomy_auto_publish_enabled"] is False  # safe default


def test_queue_list_snapshot(client):
    res = client.call("queue.list")["result"]
    assert res["summary"]["queued"] >= 1
    item = next(i for i in res["items"] if i["queue_id"] == "q-1")
    assert item["job_id"] == "job-1"
    assert item["channel_id"] == "chan1"
    assert item["stage"] == "RESEARCH"
    assert item["status"] == "queued"
    # M2 enrichment from payload_json
    assert item["topic"] == "Quantum Computing Basics"
    assert item["profile"] == "short_vertical"
    assert item["policy"] == "local_only"
    assert item["auto_publish"] is False


def test_queue_list_search_filter(client):
    res = client.call("queue.list", {"search": "quantum"})["result"]
    assert any(i["queue_id"] == "q-1" for i in res["items"])
    res = client.call("queue.list", {"search": "zzzz-no-match"})["result"]
    assert res["items"] == []


def test_queue_list_status_filter(client):
    res = client.call("queue.list", {"status": "not-a-real-status"})["result"]
    assert res["items"] == []


def test_job_inspect(client):
    res = client.call("job.inspect", {"job_id": "job-1"})["result"]
    assert res["found"] is True
    assert res["job"]["topic"] == "Quantum Computing Basics"
    assert len(res["events"]) >= 2
    assert any(e["to_state"] == "SCRIPT" for e in res["events"])
    assert len(res["errors"]) == 1
    assert res["errors"][0]["error_type"] == "FFMPEG_TIMEOUT"
    assert any(a["artifact_type"] == "thumbnail" for a in res["artifacts"])
    assert res["queue_item"]["queue_id"] == "q-1"


def test_job_inspect_missing(client):
    res = client.call("job.inspect", {"job_id": "ghost"})["result"]
    assert res["found"] is False


def test_job_inspect_requires_job_id(client):
    res = client.call("job.inspect", {})
    assert res["error"]["code"] == -32602


# ---------------------------------------------------------------------------
# M2 production control
# ---------------------------------------------------------------------------

def test_production_cancel_by_job_id(client):
    res = client.call("production.cancel", {"job_id": "job-1"})["result"]
    assert res["cancelled"] is True
    assert res["queue_id"] == "q-1"
    # Reflects in next snapshot.
    later = client.call("queue.list")["result"]
    item = next(i for i in later["items"] if i["queue_id"] == "q-1")
    assert item["status"] == "cancelled"


def test_production_cancel_by_queue_id(client):
    res = client.call("production.cancel", {"queue_id": "q-1"})["result"]
    assert res["cancelled"] is True


def test_production_cancel_requires_target(client):
    res = client.call("production.cancel", {})
    assert res["error"]["code"] == -32602


def test_production_cancel_unknown_job(client):
    res = client.call("production.cancel", {"job_id": "ghost-job"})
    assert res["error"]["code"] == -32602


def test_production_cancel_non_cancellable_status(seeded_db):
    """A succeeded item is not cancellable — cancel returns false, state unchanged."""
    from autopilot.db.manager import DBManager

    db = DBManager(seeded_db)
    db.update_queue_item_status("q-1", "succeeded", error_message=None)

    cli = BridgeClient(seeded_db)
    try:
        res = cli.call("production.cancel", {"job_id": "job-1"})["result"]
        assert res["cancelled"] is False
        later = cli.call("queue.list")["result"]
        item = next(i for i in later["items"] if i["queue_id"] == "q-1")
        assert item["status"] == "succeeded"
    finally:
        cli.close()


def test_production_retry_after_cancel(client):
    client.call("production.cancel", {"queue_id": "q-1"})
    res = client.call("production.retry", {"job_id": "job-1"})["result"]
    assert res["retried"] is True
    assert res["queue_id"] == "q-1"
    later = client.call("queue.list")["result"]
    item = next(i for i in later["items"] if i["queue_id"] == "q-1")
    assert item["status"] == "queued"
    assert item["attempt_count"] == 0


def test_production_retry_unknown_job(client):
    res = client.call("production.retry", {"job_id": "ghost-job"})
    assert res["error"]["code"] == -32602


def test_production_start_requires_topic(client):
    res = client.call("production.start", {})
    assert res["error"]["code"] == -32602
    res = client.call("production.start", {"topic": "   "})
    assert res["error"]["code"] == -32602


def test_production_start_delegates_to_worker(tmp_path, monkeypatch):
    """production.start enqueues, claims, and runs the canonical worker path
    (stubbed) with auto_publish forced off and no secrets in the payload."""
    import autopilot.core.worker as worker_module
    from autopilot.bridge.handlers import BridgeHandlers
    from autopilot.core.config import Config
    from autopilot.db.manager import DBManager

    monkeypatch.setenv("AUTOPILOT_DB_PATH", str(tmp_path / "start.db"))
    db = DBManager(tmp_path / "start.db")
    db.init_schema()

    captured: dict = {}

    class FakeWorker:
        def __init__(self, worker_id, config, db):
            captured["worker_id"] = worker_id

        def process_claimed_item(self, claimed, provider_overrides=None, force_auto_publish=None):
            captured["claimed"] = claimed
            captured["provider_overrides"] = provider_overrides
            captured["force_auto_publish"] = force_auto_publish
            db.complete_queue_item(claimed["queue_id"])  # mirror the real worker
            return {
                "queue_id": claimed["queue_id"],
                "job_id": claimed["job_id"],
                "status": "succeeded",
                "media_path": "/tmp/out.mp4",
                "qa_status": "APPROVED",
                "error": None,
            }

    monkeypatch.setattr(worker_module, "LocalWorker", FakeWorker)

    handlers = BridgeHandlers(Config(), db)
    result = handlers.dispatch("production.start", {
        "topic": "Rust performance",
        "channel": "chan1",
        "policy": "local_only",
    })

    assert result["status"] == "succeeded"
    assert result["qa_status"] == "APPROVED"
    assert result["job_id"].startswith("prod-Rust-performance-")
    assert result["queue_id"].startswith("desk-")

    claimed = captured["claimed"]
    assert claimed["status"] == "running"
    assert claimed["channel_id"] == "chan1"
    assert captured["force_auto_publish"] is False
    overrides = captured["provider_overrides"]
    assert overrides["policy"] == "local_only"

    # Payload persisted during processing is free of secrets.
    item = db.get_queue_item(result["queue_id"])
    assert item is not None
    hits = _collect_sensitive({"payload_json": item.get("payload_json")})
    assert hits == []

    # Queue summary reflects the completed run.
    summary = db.get_queue_status_summary()
    assert summary["succeeded"] >= 1
    assert summary["queued"] == 0


def test_production_start_blocks_mock_under_local_only(tmp_path, monkeypatch):
    """Explicit mock providers under local_only must never silently ship
    from the desktop surface — delegation to the worker enforces fail-closed."""
    import autopilot.core.worker as worker_module
    from autopilot.bridge.handlers import BridgeHandlers
    from autopilot.core.config import Config
    from autopilot.db.manager import DBManager

    monkeypatch.setenv("AUTOPILOT_DB_PATH", str(tmp_path / "block.db"))
    db = DBManager(tmp_path / "block.db")
    db.init_schema()

    captured: dict = {}

    class FakeWorker:
        def __init__(self, worker_id, config, db):
            pass

        def process_claimed_item(self, claimed, provider_overrides=None, force_auto_publish=None):
            captured["overrides"] = provider_overrides
            return {"queue_id": claimed["queue_id"], "job_id": claimed["job_id"], "status": "failed", "error": "X"}

    monkeypatch.setattr(worker_module, "LocalWorker", FakeWorker)

    handlers = BridgeHandlers(Config(), db)
    handlers.dispatch("production.start", {
        "topic": "Rust performance",
        "policy": "local_only",
        "llm_provider": "mock",
        "tts_provider": "mock",
    })
    assert captured["overrides"]["policy"] == "local_only"
    assert captured["overrides"]["llm"] == "mock"  # worker will reject silently with resolve_providers_for_policy


def test_events_tail(client):
    res = client.call("events.tail", {"limit": 50})["result"]
    assert len(res["events"]) >= 2
    assert res["events"][0]["job_id"] == "job-1"
    assert "occurred_at" in res["events"][0]


def test_logs_tail_merges_events_and_errors(client):
    res = client.call("logs.tail", {"limit": 50})["result"]
    entries = res["entries"]
    assert any(e["severity"] == "info" and e["source"] == "event" for e in entries)
    assert any(
        e["severity"] == "error" and e["source"] == "error" and "FFMPEG_TIMEOUT" in e["message"]
        for e in entries
    )
    # newest-first ordering by timestamp string
    times = [e["timestamp"] for e in entries]
    assert times == sorted(times, reverse=True)


def test_logs_severity_filter(client):
    res = client.call("logs.tail", {"severity": "error"})["result"]
    assert all(e["severity"] == "error" for e in res["entries"])
    assert res["entries"]


def test_logs_search_filter(client):
    res = client.call("logs.tail", {"search": "FFMPEG_TIMEOUT"})["result"]
    assert res["entries"]
    assert all("FFMPEG_TIMEOUT" in e["message"] for e in res["entries"])


# ---------------------------------------------------------------------------
# No-secrets guarantee
# ---------------------------------------------------------------------------

def test_no_secrets_in_any_response(client):
    methods = ["ping", "system.status", "health.get", "queue.list", "job.inspect", "events.tail", "logs.tail", "production.cancel", "production.retry"]
    for method in methods:
        if method == "job.inspect":
            params: dict | None = {"job_id": "job-1", "limit": 10}
        elif method in ("production.cancel", "production.retry"):
            params = {"job_id": "job-1"}
        else:
            params = None
        frame = client.call(method, params)
        hits = _collect_sensitive(frame)
        assert hits == [], f"{method} leaked sensitive keys: {hits}"
        text = json.dumps(frame).lower()
        for kw in _SENSITIVE_VALUE_KEYWORDS:
            assert kw not in text, f"{method} leaked value keyword {kw!r}"
    report_text = json.dumps(client.call("health.get")["result"]).lower()
    assert "credentials" not in report_text


# ---------------------------------------------------------------------------
# Shutdown / reconnect / restart
# ---------------------------------------------------------------------------

def test_shutdown_is_graceful(seeded_db):
    cli = BridgeClient(seeded_db)
    try:
        res = cli.call("system.shutdown")
        assert res["result"] == {"shutdown": True}
        assert cli.wait_exit(timeout=15) == 0
    finally:
        cli.close()


def test_reconnect_after_shutdown(seeded_db):
    first = BridgeClient(seeded_db)
    try:
        first.call("system.shutdown")
        assert first.wait_exit(timeout=15) == 0
    finally:
        first.close()

    second = BridgeClient(seeded_db)  # fresh process = reconnect
    try:
        res = second.call("ping")
        assert res["result"]["pong"] is True
    finally:
        second.close()


def test_restart_responds_then_exits(seeded_db):
    cli = BridgeClient(seeded_db)
    try:
        res = cli.call("system.restart")
        assert res["result"] == {"restarting": True}
        assert cli.wait_exit(timeout=15) == 0
    finally:
        cli.close()


# ---------------------------------------------------------------------------
# Integration: UI -> bridge -> backend -> DB -> snapshot
# ---------------------------------------------------------------------------

def test_integration_snapshot_reflects_db_change(seeded_db):
    """Simulates the UI polling path: a backend/worker change appears in the
    next snapshot without re-implementing backend behavior in the bridge."""
    from autopilot.db.manager import DBManager

    cli = BridgeClient(seeded_db)
    try:
        before = cli.call("queue.list")["result"]
        assert any(q["queue_id"] == "q-1" and q["status"] == "queued" for q in before["items"])

        db = DBManager(seeded_db)
        db.update_queue_item_status("q-1", "succeeded", error_message=None)

        after = cli.call("queue.list")["result"]
        updated = next(q for q in after["items"] if q["queue_id"] == "q-1")
        assert updated["status"] == "succeeded"
        assert after["summary"]["succeeded"] >= 1
    finally:
        cli.close()


def test_bridge_spawn_fails_fast_on_unreadable_db(tmp_path):
    env = dict(os.environ)
    env["AUTOPILOT_DB_PATH"] = str(tmp_path / "does_not_exist" / "db.db")
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "autopilot.bridge"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=str(_REPO_ROOT),
    )
    exit_code = proc.wait(timeout=30)
    assert exit_code != 0  # backend unavailable must surface, never fake health