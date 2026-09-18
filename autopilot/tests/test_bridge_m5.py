"""M5/M6 bridge tests — backend-controlled autonomous public publishing switch.

Exercises the backend authority model:

* the effective switch resolver (persisted DB row wins over config),
* fail-closed enable with structural prerequisite validation,
* idempotent enable and immediate/persistent/idempotent disable (kill switch),
* persistence across reloads,
* the final publish-boundary re-check (independent of earlier-captured state)
  scoped to autonomous PUBLIC publishes only,
* manual / non-public / dry-run publishes remaining unaffected.

Isolation: temporary DBs, stubbed YouTube auth check, MockPublisher only.
No real network or publishing provider is ever invoked.
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
        env["AUTOPILOT_YOUTUBE_TOKEN_PATH"] = str(_REPO_ROOT / "credentials" / "no-such-token-m5.json")
        env["YOUTUBE_TOKEN_PATH"] = str(_REPO_ROOT / "credentials" / "no-such-token-m5.json")
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

    def call(self, method: str, params: dict | None = None) -> dict:
        rid = self._next_id
        self._next_id += 1
        req: dict = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            req["params"] = params
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
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
                if key.lower() in ("token_present", "secrets_present"):
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
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def m5_db(tmp_path, monkeypatch):
    db_path = tmp_path / "m5_stdio.db"
    monkeypatch.setenv("AUTOPILOT_DB_PATH", str(db_path))
    monkeypatch.setenv("AUTOPILOT_ANALYTICS_DEFAULT_PROVIDER", "mock")
    from autopilot.db.manager import DBManager

    db = DBManager(str(db_path))
    db.init_schema()
    yield db_path


@pytest.fixture()
def client(m5_db):
    cli = BridgeClient(m5_db)
    yield cli
    cli.close()


def _handlers(tmp_path, monkeypatch):
    from autopilot.bridge.handlers import BridgeHandlers
    from autopilot.core.config import Config
    from autopilot.db.manager import DBManager

    monkeypatch.setenv("AUTOPILOT_DB_PATH", str(tmp_path / "m5.db"))
    monkeypatch.setenv("AUTOPILOT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("AUTOPILOT_ANALYTICS_DEFAULT_PROVIDER", "mock")
    db = DBManager(str(tmp_path / "m5.db"))
    db.init_schema()
    return BridgeHandlers(Config(), db)


def _auth_ok(monkeypatch):
    """Stub the readiness probe so the auth prerequisite verifies."""
    import autopilot.core.auto_publish as ap

    monkeypatch.setattr(ap, "_youtube_authenticated", lambda config: True)


def _auth_blocked(monkeypatch):
    """Force the auth prerequisite to fail regardless of local tokens."""
    import autopilot.core.auto_publish as ap

    monkeypatch.setattr(ap, "_youtube_authenticated", lambda config: False)


def _make_ready_job(handlers, tmp_path, job_id="job-ready-1"):
    """Create an APPROVED job with media + passing QA receipt (no approval yet)."""
    db = handlers.db
    db.create_job(job_id=job_id, channel_id="default", topic="Quantum Computing Basics")
    db.update_job_status(job_id, "APPROVED")

    artifacts = Path(os.environ["AUTOPILOT_ARTIFACTS_DIR"])
    from autopilot.core.artifacts import job_artifact_dir
    from autopilot.core.publisher import compute_file_sha256

    art_dir = job_artifact_dir(job_id, base_dir=artifacts)
    render_dir = art_dir / "render"
    render_dir.mkdir(parents=True, exist_ok=True)
    media = render_dir / "final.mp4"
    media.write_bytes(b"fake-mp4-content-for-m5")

    quality_dir = art_dir / "quality"
    quality_dir.mkdir(parents=True, exist_ok=True)
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
# Effective resolver semantics
# ---------------------------------------------------------------------------

def test_effective_resolver_db_row_wins(monkeypatch, tmp_path):
    from autopilot.core.auto_publish import effective_auto_publish

    h = _handlers(tmp_path, monkeypatch)
    # Config says ON, but a persisted OFF row is authoritative.
    h.config.autonomy_auto_publish = True
    h.db.set_config_value("autonomy_auto_publish", "false")
    assert effective_auto_publish(h.config, h.db) is False
    # Persisted ON row wins over a config OFF.
    h.db.set_config_value("autonomy_auto_publish", "true")
    assert effective_auto_publish(h.config, h.db) is True
    # Absent row falls back to config.
    with h.db._connect() as conn:
        conn.execute("DELETE FROM config WHERE key = 'autonomy_auto_publish'")
    assert effective_auto_publish(h.config, h.db) is True
    h.config.autonomy_auto_publish = False
    assert effective_auto_publish(h.config, h.db) is False


# ---------------------------------------------------------------------------
# Enable semantics (fail-closed / idempotent / persistent)
# ---------------------------------------------------------------------------

def test_enable_fails_closed_without_auth(monkeypatch, tmp_path):
    h = _handlers(tmp_path, monkeypatch)
    _auth_blocked(monkeypatch)
    res = h.dispatch("autonomy.publish_enable", None)
    assert res["ok"] is False
    assert res["changed"] is False
    assert res["enabled"] is False
    assert "youtube_auth" in res["prerequisites"]["failed"]
    # Switch untouched.
    status = h.dispatch("autonomy.publish_status", None)
    assert status["enabled"] is False
    assert h.db.get_config_value("autonomy_auto_publish") is None


def test_enable_fails_closed_on_invalid_config(monkeypatch, tmp_path):
    h = _handlers(tmp_path, monkeypatch)
    _auth_ok(monkeypatch)
    h.config.publish_default_visibility = "bogus"
    res = h.dispatch("autonomy.publish_enable", None)
    assert res["ok"] is False
    assert res["changed"] is False
    assert "config_valid" in res["prerequisites"]["failed"]
    assert res["reason"]
    assert res["prerequisites"]["ok"] is False


def test_enable_success_persists_and_mirrors_config(monkeypatch, tmp_path):
    h = _handlers(tmp_path, monkeypatch)
    _auth_ok(monkeypatch)
    res = h.dispatch("autonomy.publish_enable", None)
    assert res["ok"] is True
    assert res["changed"] is True
    assert res["enabled"] is True
    assert res["state"] == "ENABLED"
    assert res["prerequisites"]["ok"] is True
    assert res["prerequisites"]["failed"] == []
    assert h.db.get_config_value("autonomy_auto_publish") == "true"
    assert h.config.autonomy_auto_publish is True
    # Status + health surfaces reflect the authoritative state.
    assert h.dispatch("autonomy.publish_status", None)["enabled"] is True
    assert h.dispatch("health.get", None)["autonomy_auto_publish_enabled"] is True
    assert h.dispatch("publishing.status", None)["autonomy_auto_publish_enabled"] is True


def test_enable_is_idempotent(monkeypatch, tmp_path):
    h = _handlers(tmp_path, monkeypatch)
    _auth_ok(monkeypatch)
    first = h.dispatch("autonomy.publish_enable", None)
    second = h.dispatch("autonomy.publish_enable", None)
    assert first["ok"] is True and first["changed"] is True
    assert second["ok"] is True and second["changed"] is False
    assert second["enabled"] is True


def test_disable_is_immediate_persistent_idempotent(monkeypatch, tmp_path):
    h = _handlers(tmp_path, monkeypatch)
    _auth_ok(monkeypatch)
    h.dispatch("autonomy.publish_enable", None)
    kill = h.dispatch("autonomy.publish_disable", None)
    assert kill["ok"] is True
    assert kill["changed"] is True
    assert kill["enabled"] is False
    assert h.db.get_config_value("autonomy_auto_publish") == "false"
    # Status + health surfaces follow.
    assert h.dispatch("autonomy.publish_status", None)["enabled"] is False
    assert h.dispatch("health.get", None)["autonomy_auto_publish_enabled"] is False
    # Disable again is still a success but changes nothing.
    again = h.dispatch("autonomy.publish_disable", None)
    assert again["ok"] is True
    assert again["changed"] is False


def test_state_survives_new_connection(monkeypatch, tmp_path):
    from autopilot.core.config import Config
    from autopilot.db.manager import DBManager

    h = _handlers(tmp_path, monkeypatch)
    _auth_ok(monkeypatch)
    h.dispatch("autonomy.publish_enable", None)

    fresh_db = DBManager(str(tmp_path / "m5.db"))
    fresh_db.init_schema()
    fresh = type(h)(Config(), fresh_db)
    assert fresh.dispatch("autonomy.publish_status", None)["enabled"] is True

    h.dispatch("autonomy.publish_disable", None)
    fresh2 = type(h)(Config(), DBManager(str(tmp_path / "m5.db")))
    fresh2.db.init_schema()
    assert fresh2.dispatch("autonomy.publish_status", None)["enabled"] is False


# ---------------------------------------------------------------------------
# Final publish-boundary re-check
# ---------------------------------------------------------------------------

def _publish(handlers, job_id="job-ready-1", *, visibility="public", autonomous=True, dry_run=False):
    from autopilot.core.publisher import PublishingEngine
    from autopilot.providers.mock_publisher import MockPublisher

    return PublishingEngine(handlers.config, handlers.db).publish_job(
        job_id=job_id,
        platform="youtube",
        visibility=visibility,
        dry_run=dry_run,
        provider=MockPublisher(),
        require_approval=True,
        autonomous=autonomous,
    )


def test_boundary_blocks_autonomous_public_with_stale_config(monkeypatch, tmp_path):
    """A disabled switch must win over any earlier-captured in-memory state."""
    h = _handlers(tmp_path, monkeypatch)
    _auth_ok(monkeypatch)
    h.dispatch("autonomy.publish_enable", None)
    h.dispatch("autonomy.publish_disable", None)

    # Simulate a stale/rogue reader holding an ON value after the kill switch.
    h.config.autonomy_auto_publish = True
    assert h.dispatch("autonomy.publish_status", None)["enabled"] is False

    _make_ready_job(h, tmp_path)
    h.dispatch("publishing.approve", {"job_id": "job-ready-1"})

    result = _publish(h)
    assert result.success is False
    assert result.status.value == "BLOCKED_AUTONOMY_SWITCH"
    assert result.error.error_code == "AUTONOMY_SWITCH_OFF"
    # Fail-closed: the approved job stays APPROVED (nothing published).
    assert h.db.get_job("job-ready-1")["status"] == "APPROVED"


def test_boundary_allows_autonomous_public_when_enabled(monkeypatch, tmp_path):
    h = _handlers(tmp_path, monkeypatch)
    _auth_ok(monkeypatch)
    h.dispatch("autonomy.publish_enable", None)
    _make_ready_job(h, tmp_path)
    h.dispatch("publishing.approve", {"job_id": "job-ready-1"})

    result = _publish(h)
    assert result.success is True
    assert result.status.value in ("PUBLISHED", "SUCCESS", "DRY_RUN")


def test_boundary_skips_non_public_autonomous_when_off(monkeypatch, tmp_path):
    """Autonomous non-public publishes are untouched by the switch."""
    h = _handlers(tmp_path, monkeypatch)
    _make_ready_job(h, tmp_path)
    h.dispatch("publishing.approve", {"job_id": "job-ready-1"})

    result = _publish(h, visibility="unlisted")
    assert result.success is True
    assert result.status.value not in ("BLOCKED_AUTONOMY_SWITCH", "BLOCKED_APPROVAL")


def test_boundary_skips_dry_run_when_off(monkeypatch, tmp_path):
    h = _handlers(tmp_path, monkeypatch)
    _make_ready_job(h, tmp_path)
    h.dispatch("publishing.approve", {"job_id": "job-ready-1"})

    result = _publish(h, visibility="public", dry_run=True)
    assert result.success is True
    assert result.status.value == "DRY_RUN"


def test_manual_publish_unaffected_by_switch(monkeypatch, tmp_path):
    """The human/bridge publish path (no autonomous flag) ignores the switch."""
    h = _handlers(tmp_path, monkeypatch)
    _make_ready_job(h, tmp_path)
    h.dispatch("publishing.approve", {"job_id": "job-ready-1"})

    result = _publish(h, autonomous=False)
    assert result.success is True
    assert result.status.value in ("PUBLISHED", "SUCCESS")


# ---------------------------------------------------------------------------
# stdio client surface
# ---------------------------------------------------------------------------

def test_client_default_status_disabled(client):
    res = _result(client.call("autonomy.publish_status"))
    assert res["enabled"] is False
    assert res["state"] == "DISABLED"
    assert res["label"] == "AUTONOMOUS PUBLIC PUBLISHING DISABLED"
    assert res["default"] is False
    assert res["controlled_by"] == "backend"
    assert res["guardrails"]
    assert res["boundary"]


def test_client_enable_fails_closed_by_default(client):
    res = _result(client.call("autonomy.publish_enable"))
    assert res["ok"] is False
    assert res["changed"] is False
    assert res["enabled"] is False
    assert res["prerequisites"]["failed"]
    status = _result(client.call("autonomy.publish_status"))
    assert status["enabled"] is False


def test_client_disable_kill_switch_idempotent(client):
    res = _result(client.call("autonomy.publish_disable"))
    assert res["ok"] is True
    assert res["changed"] is False
    assert res["enabled"] is False
    again = _result(client.call("autonomy.publish_disable"))
    assert again["ok"] is True
    assert again["changed"] is False


def test_client_switch_no_secrets(client):
    for method in ("autonomy.publish_status", "autonomy.publish_enable", "autonomy.publish_disable"):
        frame = client.call(method)
        assert _collect_sensitive(frame) == []
        text = json.dumps(frame).lower()
        for kw in _SENSITIVE_VALUE_KEYWORDS:
            assert kw not in text