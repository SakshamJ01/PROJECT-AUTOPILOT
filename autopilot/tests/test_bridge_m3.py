"""M3 desktop bridge tests — autonomy + scheduler control surface.

Exercises the JSON-RPC bridge as a real child process over stdio against a
temporary SQLite database (read-only and CRUD methods), and in-process with
stubbed engines for the execution paths (autonomy.run sequencing, run_now
delegation) so no real production/provider stack is invoked.

Isolation: temporary DBs only, mock policy, no publishing, no network.
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


class BridgeClient:
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
                    self._inbox.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
                with self._cond:
                    self._cond.notify_all()
        except Exception:  # noqa: BLE001
            pass

    def _next_frame(self, timeout: float = 20.0) -> dict:
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
                if any(k in key.lower() for k in ("token", "secret", "credential", "password", "passwd")):
                    hits.append(key)
                stack.append(val)
        elif isinstance(value, list):
            stack.extend(value)
    return hits


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def m3_db(tmp_path, monkeypatch):
    """Temp DB with one proposal + one autonomy run + one schedule."""
    from autopilot.db.manager import DBManager

    db_path = tmp_path / "m3.db"
    monkeypatch.setenv("AUTOPILOT_DB_PATH", str(db_path))
    db = DBManager(str(db_path))
    db.init_schema()

    # Seed an autonomy run first (the candidate/proposal FK references it).
    db.record_autonomy_run(
        run_id="run-seed", autonomy_level=3, strategy_version="strat-v1",
        config_json="{}", channel_id="default",
    )

    # A proposed idea proposal (candidate + score + proposal row).
    from autopilot.core.contracts import (
        IdeaProposal, ProposalStatus, TopicCandidate, TopicScore,
    )
    cand = TopicCandidate(
        candidate_id="cand-1", run_id="run-seed", proposed_topic="Quantum Computing Basics",
        angle="explainer", hook_hypothesis="what if qubits were coins", content_format="short_vertical",
        supporting_signal_ids=["sig-1"],
    )
    score = TopicScore(score_id="score-1", candidate_id="cand-1", total_score=0.82)
    prop = IdeaProposal(
        proposal_id="prop-1", run_id="run-seed", channel_id="default",
        candidate=cand, score=score, status=ProposalStatus.PROPOSED,
    )
    db.record_idea_proposal(prop)

    db.update_autonomy_run(
        run_id="run-seed", status="completed",
        signals_discovered=3, candidates_generated=2, proposals_created=1, jobs_queued=1,
    )
    yield db_path


@pytest.fixture()
def client(m3_db):
    cli = BridgeClient(m3_db)
    yield cli
    cli.close()


@pytest.fixture()
def stub_loggers(monkeypatch):
    from autopilot.core import autonomy as autonomy_mod
    from autopilot.core import scheduler as scheduler_mod

    class _Recorder:
        def __init__(self, *a, **k):
            self.events = []

        def info(self, event, details=None, **kw):
            self.events.append(event)

        def warning(self, event, details=None, **kw):
            self.events.append(event)

        def error(self, event, error=None, details=None, **kw):
            self.events.append(event)

        def debug(self, event, details=None, **kw):
            self.events.append(event)

    monkeypatch.setattr(scheduler_mod, "StructuredLogger", _Recorder)
    monkeypatch.setattr(autonomy_mod, "StructuredLogger", _Recorder)


# ---------------------------------------------------------------------------
# autonomy.status
# ---------------------------------------------------------------------------

def test_autonomy_status(client):
    res = client.call("autonomy.status")["result"]
    assert res["level"] >= 0
    assert res["mode"] in ("manual", "assisted", "autonomous")
    assert res["operational_status"] in ("ready", "running", "degraded")
    policy = res["policy"]
    assert policy["max_ideas_per_cycle"] >= 1
    assert policy["max_jobs_per_day"] >= 1
    assert policy["max_concurrent_jobs"] >= 1
    assert policy["topic_cooldown_days"] >= 1
    assert "similarity_threshold" in policy
    assert "min_score_threshold" in policy
    assert res["activity"]["proposal_counts"]["proposed"] >= 1
    assert res["activity"]["published"] is not None
    assert res["publish_boundary"]
    assert "scheduler" in res


def test_autonomy_status_no_secrets(client):
    frame = client.call("autonomy.status")
    assert _collect_sensitive(frame) == []
    text = json.dumps(frame).lower()
    for kw in _SENSITIVE_VALUE_KEYWORDS:
        assert kw not in text


# ---------------------------------------------------------------------------
# autonomy.run (in-process, stubbed engine)
# ---------------------------------------------------------------------------

def _handlers(tmp_path, monkeypatch):
    from autopilot.bridge.handlers import BridgeHandlers
    from autopilot.core.config import Config
    from autopilot.db.manager import DBManager

    monkeypatch.setenv("AUTOPILOT_DB_PATH", str(tmp_path / "run.db"))
    monkeypatch.setenv("AUTOPILOT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    db = DBManager(str(tmp_path / "run.db"))
    db.init_schema()
    return BridgeHandlers(Config(), db)


def test_autonomy_run_level3_delegates(monkeypatch, tmp_path, stub_loggers):
    import autopilot.core.autonomy as autonomy_module
    from autopilot.core.contracts import AutonomyCycleSummary

    calls = {}

    class FakeEngine:
        def __init__(self, config=None, db=None, **kw):
            pass

        def run_cycle(self, autonomy_level, channel_id, dry_run, category, limit):
            calls["run_cycle"] = dict(
                autonomy_level=autonomy_level, channel_id=channel_id,
                dry_run=dry_run, category=category, limit=limit,
            )
            return AutonomyCycleSummary(
                run_id="run-l3", channel_id=channel_id, autonomy_level=autonomy_level,
                dry_run=dry_run, status="completed",
            )

        def run_auto_produce_cycle(self, **kw):
            calls["run_auto_produce_cycle"] = kw
            raise AssertionError("Level 3 run must not invoke Level 4")

    monkeypatch.setattr(autonomy_module, "AutonomyEngine", FakeEngine)
    h = _handlers(tmp_path, monkeypatch)

    result = h.dispatch("autonomy.run", {"mode": "level3", "channel_id": "chan1", "limit": 5})
    assert result["operation_mode"] == "level3"
    assert result["status"] == "completed"
    assert result["run_id"] == "run-l3"
    assert result["level4"] is None
    assert calls["run_cycle"]["autonomy_level"] == 3
    assert calls["run_cycle"]["channel_id"] == "chan1"
    assert calls["run_cycle"]["limit"] == 5
    assert "run_auto_produce_cycle" not in calls


def test_autonomy_run_level4_delegates(monkeypatch, tmp_path, stub_loggers):
    import autopilot.core.autonomy as autonomy_module
    from autopilot.core.contracts import AutoProduceSummary

    calls = {}

    class FakeEngine:
        def __init__(self, config=None, db=None, **kw):
            pass

        def run_cycle(self, **kw):
            raise AssertionError("Level 4 run must not invoke Level 3")

        def run_auto_produce_cycle(self, channel_id, limit, dry_run, policy):
            calls["run_auto_produce_cycle"] = dict(
                channel_id=channel_id, limit=limit, dry_run=dry_run, policy=policy,
            )
            return AutoProduceSummary(
                run_id="run-l4", channel_id=channel_id, dry_run=dry_run,
                policy=policy, status="completed", jobs_ready_to_publish=1,
            )

    monkeypatch.setattr(autonomy_module, "AutonomyEngine", FakeEngine)
    h = _handlers(tmp_path, monkeypatch)

    result = h.dispatch("autonomy.run", {"mode": "level4", "policy": "mock", "limit": 3})
    assert result["operation_mode"] == "level4"
    assert result["status"] == "completed"
    assert result["level4"]["jobs_ready_to_publish"] == 1
    assert calls["run_auto_produce_cycle"]["policy"] == "mock"


def test_autonomy_run_combined_isolates_level4_on_level3_failure(monkeypatch, tmp_path, stub_loggers):
    import autopilot.core.autonomy as autonomy_module
    from autopilot.core.contracts import AutonomyCycleSummary

    calls = {}

    class FakeEngine:
        def __init__(self, config=None, db=None, **kw):
            pass

        def run_cycle(self, autonomy_level, channel_id, dry_run, category, limit):
            calls["l3"] = True
            return AutonomyCycleSummary(
                run_id="run-l3-fail", channel_id=channel_id,
                autonomy_level=autonomy_level, status="failed",
                error_message="trend provider down",
            )

        def run_auto_produce_cycle(self, **kw):
            calls["l4"] = True
            raise AssertionError("Level 4 must not run after failed Level 3")

    monkeypatch.setattr(autonomy_module, "AutonomyEngine", FakeEngine)
    h = _handlers(tmp_path, monkeypatch)

    result = h.dispatch("autonomy.run", {"mode": "level3_then_level4"})
    assert result["operation_mode"] == "level3_then_level4"
    assert result["status"] == "failed"
    assert result["level4"] is None
    assert result["level4_skipped_reason"]
    assert "failure isolation" in result["level4_skipped_reason"]
    assert "l4" not in calls


def test_autonomy_run_combined_runs_both_on_success(monkeypatch, tmp_path, stub_loggers):
    import autopilot.core.autonomy as autonomy_module
    from autopilot.core.contracts import AutonomyCycleSummary, AutoProduceSummary

    calls = {}

    class FakeEngine:
        def __init__(self, config=None, db=None, **kw):
            pass

        def run_cycle(self, autonomy_level, channel_id, dry_run, category, limit):
            calls["l3"] = True
            return AutonomyCycleSummary(
                run_id="run-l3", channel_id=channel_id,
                autonomy_level=autonomy_level, status="completed", jobs_queued=2,
            )

        def run_auto_produce_cycle(self, channel_id, limit, dry_run, policy):
            calls["l4"] = True
            return AutoProduceSummary(
                run_id="run-l4", channel_id=channel_id, status="completed",
                jobs_ready_to_publish=2,
            )

    monkeypatch.setattr(autonomy_module, "AutonomyEngine", FakeEngine)
    h = _handlers(tmp_path, monkeypatch)

    result = h.dispatch("autonomy.run", {"mode": "level3_then_level4"})
    assert result["status"] == "completed"
    assert result["level3"]["jobs_queued"] == 2
    assert result["level4"]["jobs_ready_to_publish"] == 2
    assert result["level4_skipped_reason"] is None
    assert "l3" in calls and "l4" in calls


def test_autonomy_run_invalid_mode(client):
    res = client.call("autonomy.run", {"mode": "level5"})
    assert res["error"]["code"] == -32602
    assert "level3" in res["error"]["message"]


# ---------------------------------------------------------------------------
# autonomy.proposals / inspect / proposal approve+reject
# ---------------------------------------------------------------------------

def test_autonomy_proposals(client):
    res = client.call("autonomy.proposals")["result"]
    assert len(res["items"]) >= 1
    item = res["items"][0]
    assert item["proposal_id"] == "prop-1"
    assert item["proposed_topic"] == "Quantum Computing Basics"
    assert item["total_score"] is not None


def test_autonomy_proposals_status_filter(client):
    res = client.call("autonomy.proposals", {"status": "queued"})["result"]
    assert res["items"] == []


def test_autonomy_inspect_run(client):
    res = client.call("autonomy.inspect", {"run_id": "run-seed"})["result"]
    assert res["found"] is True
    assert res["run"]["run_id"] == "run-seed"
    assert res["run"]["status"] == "completed"
    assert res["run"]["signals_discovered"] == 3
    assert "signals" in res and "candidates" in res


def test_autonomy_inspect_proposal(client):
    res = client.call("autonomy.inspect", {"proposal_id": "prop-1"})["result"]
    assert res["found"] is True
    assert res["proposal"]["proposal_id"] == "prop-1"
    assert res["proposal"]["total_score"] is not None


def test_autonomy_inspect_missing(client):
    res = client.call("autonomy.inspect", {"proposal_id": "ghost"})
    assert res["result"]["found"] is False


def test_autonomy_inspect_requires_target(client):
    res = client.call("autonomy.inspect", {})
    assert res["error"]["code"] == -32602


def test_autonomy_proposal_approve_and_reject(monkeypatch, tmp_path, stub_loggers, m3_db):
    """Operator approve enqueues the proposal; reject cancels its queue item.
    Both delegate to the existing AutonomyEngine; neither publishes."""
    from autopilot.bridge.handlers import BridgeHandlers
    from autopilot.core.config import Config
    from autopilot.db.manager import DBManager

    monkeypatch.setenv("AUTOPILOT_DB_PATH", str(m3_db))
    db = DBManager(str(m3_db))
    db.init_schema()
    h = BridgeHandlers(Config(), db)

    approved = h.dispatch("autonomy.proposal.approve", {"proposal_id": "prop-1"})
    assert approved["status"] == "approved"
    assert approved["job_id"].startswith("job-auto-")
    assert db.get_queue_item(approved["queue_id"]) is not None

    rejected = h.dispatch("autonomy.proposal.reject", {"proposal_id": "prop-1", "reason": "off-brand"})
    assert rejected["status"] == "rejected"
    assert rejected["queue_item_cancelled"] is True

    # No publish record was created by either control.
    assert db.get_publications_for_job(approved["job_id"]) == []


def test_autonomy_proposal_approve_missing(monkeypatch, tmp_path, stub_loggers):
    from autopilot.bridge.handlers import BridgeHandlers
    from autopilot.core.config import Config
    from autopilot.db.manager import DBManager

    monkeypatch.setenv("AUTOPILOT_DB_PATH", str(tmp_path / "apr.db"))
    db = DBManager(str(tmp_path / "apr.db"))
    db.init_schema()
    h = BridgeHandlers(Config(), db)

    res = h.dispatch("autonomy.proposal.approve", {"proposal_id": "ghost"})
    # The engine returns an error result (not an exception) for a missing
    # proposal; the bridge surfaces it verbatim without raising.
    assert res["status"] == "error"
    assert "ghost" in res["reason"]


def test_autonomy_proposal_approve_requires_id(client):
    res = client.call("autonomy.proposal.approve", {})
    assert res["error"]["code"] == -32602


# ---------------------------------------------------------------------------
# scheduler.status / list / inspect
# ---------------------------------------------------------------------------

def test_scheduler_status_empty_then_populated(client, m3_db):
    from autopilot.core.scheduler import ScheduleEngine
    from autopilot.core.config import Config
    from autopilot.db.manager import DBManager

    res = client.call("scheduler.status")["result"]
    assert res["state"] in ("running", "stopped", "degraded")
    assert res["summary"]["status"] in ("AVAILABLE", "DEGRADED")
    assert res["active_executions"] == 0

    # Create a schedule out-of-band, then re-check via the bridge.
    db = DBManager(str(m3_db))
    ScheduleEngine(config=Config(db_path=str(m3_db)), db=db).create_schedule(
        channel_id="default", autonomy_level=3, cadence="daily",
    )
    res = client.call("scheduler.status")["result"]
    assert res["state"] == "running"  # an enabled schedule exists
    assert res["summary"]["enabled_schedules"] >= 1


def test_scheduler_list(client):
    res = client.call("scheduler.list")["result"]
    assert "items" in res
    assert res["items"] == []


# ---------------------------------------------------------------------------
# scheduler.create / update / enable / disable / delete (child process)
# ---------------------------------------------------------------------------

def test_scheduler_create_and_list(client):
    res = client.call("scheduler.create", {
        "channel_id": "default",
        "autonomy_level": 4,
        "operation_mode": "level3_then_level4",
        "cadence": "weekly",
        "days_of_week": ["mon", "wed"],
        "timezone": "America/New_York",
        "max_items_per_run": 5,
        "policy": "local_only",
        "include_learning": True,
    })
    assert res.get("error") is None
    schedule = res["result"]
    assert schedule["schedule_id"].startswith("sch-")
    assert schedule["autonomy_level"] == 4
    assert schedule["operation_mode"] == "level3_then_level4"
    assert schedule["cadence"] == "weekly"
    assert schedule["days_of_week"] == ["mon", "wed"]
    assert schedule["timezone"] == "America/New_York"
    assert schedule["include_learning"] is True
    assert schedule["enabled"] is True
    assert schedule["next_run_at"]

    listed = client.call("scheduler.list")["result"]["items"]
    assert any(s["schedule_id"] == schedule["schedule_id"] for s in listed)


def test_scheduler_create_invalid_mode(client):
    res = client.call("scheduler.create", {"autonomy_level": 3, "operation_mode": "level4"})
    assert res["error"]["code"] == -32602


def test_scheduler_create_invalid_timezone(client):
    res = client.call("scheduler.create", {"timezone": "Not/A_Real_Tz"})
    assert res["error"]["code"] == -32602


def test_scheduler_create_invalid_cadence(client):
    res = client.call("scheduler.create", {"cadence": "monthly"})
    assert res["error"]["code"] == -32602


def _make_schedule(client, **over):
    params = {
        "channel_id": "default",
        "autonomy_level": 4,
        "operation_mode": "level3_then_level4",
        "cadence": "daily",
        "timezone": "UTC",
        "max_items_per_run": 3,
        "include_learning": False,
    }
    params.update(over)
    frame = client.call("scheduler.create", params)
    assert frame.get("error") is None, f"create failed: {frame.get('error')}"
    return frame["result"]


def test_scheduler_update(client):
    created = _make_schedule(client)
    sid = created["schedule_id"]

    res = client.call("scheduler.update", {
        "schedule_id": sid,
        "include_learning": True,
        "max_items_per_run": 8,
        "cadence": "hourly",
    })
    assert res.get("error") is None
    updated = res["result"]
    assert updated["include_learning"] is True
    assert updated["max_items_per_run"] == 8
    assert updated["cadence"] == "hourly"
    # Cadence change recomputed next_run_at.
    assert updated["next_run_at"] != created["next_run_at"]


def test_scheduler_update_disabled_via_params(client):
    sid = _make_schedule(client)["schedule_id"]
    res = client.call("scheduler.update", {"schedule_id": sid, "enabled": False})
    assert res["result"]["enabled"] is False


def test_scheduler_update_invalid_combination(client):
    sid = _make_schedule(client, autonomy_level=3, operation_mode="level3")["schedule_id"]
    res = client.call("scheduler.update", {"schedule_id": sid, "operation_mode": "level4"})
    assert res["error"]["code"] == -32602


def test_scheduler_update_missing(client):
    res = client.call("scheduler.update", {"schedule_id": "sch-ghost", "include_learning": True})
    assert res["error"]["code"] == -32602


def test_scheduler_enable_disable(client):
    sid = _make_schedule(client)["schedule_id"]

    disabled = client.call("scheduler.disable", {"schedule_id": sid})["result"]
    assert disabled["enabled"] is False

    enabled = client.call("scheduler.enable", {"schedule_id": sid})["result"]
    assert enabled["enabled"] is True
    assert enabled["next_run_at"]


def test_scheduler_enable_missing(client):
    res = client.call("scheduler.enable", {"schedule_id": "sch-ghost"})
    assert res["error"]["code"] == -32602


def test_scheduler_delete(client):
    sid = _make_schedule(client)["schedule_id"]

    res = client.call("scheduler.delete", {"schedule_id": sid})["result"]
    assert res["deleted"] is True

    listed = client.call("scheduler.list")["result"]["items"]
    assert not any(s["schedule_id"] == sid for s in listed)

    # Deleting again is a clean not-found error.
    res = client.call("scheduler.delete", {"schedule_id": sid})
    assert res["error"]["code"] == -32602


def test_scheduler_inspect_empty_history(client):
    sid = _make_schedule(client)["schedule_id"]
    res = client.call("scheduler.inspect", {"schedule_id": sid})["result"]
    assert res["schedule"]["schedule_id"] == sid
    assert res["runs"] == []


def test_scheduler_inspect_missing(client):
    res = client.call("scheduler.inspect", {"schedule_id": "sch-ghost"})
    assert res["error"]["code"] == -32602


# ---------------------------------------------------------------------------
# scheduler.run_now / run_due (in-process, stubbed autonomy engine)
# ---------------------------------------------------------------------------

def test_scheduler_run_now_delegates_and_records(monkeypatch, tmp_path, stub_loggers):
    import autopilot.core.autonomy as autonomy_module
    import autopilot.core.scheduler as scheduler_module
    from autopilot.bridge.handlers import BridgeHandlers
    from autopilot.core.config import Config
    from autopilot.core.contracts import AutonomyCycleSummary
    from autopilot.db.manager import DBManager

    monkeypatch.setenv("AUTOPILOT_DB_PATH", str(tmp_path / "rn.db"))
    monkeypatch.setenv("AUTOPILOT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    db = DBManager(str(tmp_path / "rn.db"))
    db.init_schema()

    # Level 3 schedule that the real ScheduleEngine will drive with a fake
    # autonomy engine (no providers, no production).
    from autopilot.core.scheduler import ScheduleEngine

    class FakeAutonomy:
        def __init__(self, config=None, db=None, **kw):
            pass

        def run_cycle(self, autonomy_level, channel_id, dry_run, limit, category=None, **kw):
            return AutonomyCycleSummary(
                run_id="cycle-x", channel_id=channel_id,
                autonomy_level=autonomy_level, dry_run=dry_run, status="completed",
                jobs_queued=1,
            )

        def run_learning_cycle(self, channel_id=None, dry_run=False, exclude_job_ids=None, **kw):
            class _S:
                run_id = "learn-x"
                status = "no_change"
                observations_used = 0
                resulting_strategy_version = None
            return _S()

    monkeypatch.setattr(autonomy_module, "AutonomyEngine", FakeAutonomy)

    cfg = Config(db_path=str(tmp_path / "rn.db"), artifacts_dir=str(tmp_path / "artifacts"))
    sched = ScheduleEngine(config=cfg, db=db)
    created = sched.create_schedule(
        channel_id="default", autonomy_level=3, cadence="daily",
        operation_mode="level3", include_learning=True,
    )
    h = BridgeHandlers(cfg, db)

    result = h.dispatch("scheduler.run_now", {"schedule_id": created.schedule_id})
    assert result["status"] == "completed"
    assert result["operation_mode"] == "level3"
    assert result["level3_run_id"] == "cycle-x"
    assert result["publish_calls"] == 0

    # History recorded against the real table.
    runs = db.list_schedule_runs(created.schedule_id)
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    assert runs[0]["cycle_run_id"] == "cycle-x"


def test_scheduler_run_now_missing(monkeypatch, tmp_path, stub_loggers):
    from autopilot.bridge.handlers import BridgeHandlers
    from autopilot.core.config import Config
    from autopilot.db.manager import DBManager

    monkeypatch.setenv("AUTOPILOT_DB_PATH", str(tmp_path / "rn2.db"))
    db = DBManager(str(tmp_path / "rn2.db"))
    db.init_schema()
    h = BridgeHandlers(Config(db_path=str(tmp_path / "rn2.db")), db)

    from autopilot.bridge.protocol import ProtocolError

    with pytest.raises(ProtocolError):
        h.dispatch("scheduler.run_now", {"schedule_id": "sch-ghost"})


def test_scheduler_run_now_requires_id(client):
    res = client.call("scheduler.run_now", {})
    assert res["error"]["code"] == -32602


def test_scheduler_run_now_disabled_schedule_is_blocked(monkeypatch, tmp_path, stub_loggers):
    from autopilot.bridge.handlers import BridgeHandlers
    from autopilot.core.config import Config
    from autopilot.core.scheduler import ScheduleEngine
    from autopilot.db.manager import DBManager

    monkeypatch.setenv("AUTOPILOT_DB_PATH", str(tmp_path / "rn3.db"))
    db = DBManager(str(tmp_path / "rn3.db"))
    db.init_schema()
    cfg = Config(db_path=str(tmp_path / "rn3.db"))
    sched = ScheduleEngine(config=cfg, db=db)
    created = sched.create_schedule(channel_id="default", autonomy_level=3, cadence="daily")
    sched.disable_schedule(created.schedule_id)

    h = BridgeHandlers(cfg, db)
    result = h.dispatch("scheduler.run_now", {"schedule_id": created.schedule_id})
    assert result["status"] == "blocked"
    assert "disabled" in (result["error_message"] or "").lower()


def test_scheduler_run_due_no_schedules(monkeypatch, tmp_path, stub_loggers):
    from autopilot.bridge.handlers import BridgeHandlers
    from autopilot.core.config import Config
    from autopilot.db.manager import DBManager

    monkeypatch.setenv("AUTOPILOT_DB_PATH", str(tmp_path / "rd.db"))
    db = DBManager(str(tmp_path / "rd.db"))
    db.init_schema()
    h = BridgeHandlers(Config(db_path=str(tmp_path / "rd.db")), db)

    result = h.dispatch("scheduler.run_due", {"limit": 5})
    assert result["executed"] == 0
    assert result["summaries"] == []


# ---------------------------------------------------------------------------
# No-secrets guarantee across all M3 methods
# ---------------------------------------------------------------------------

def test_no_secrets_in_m3_scheduler_methods(client):
    for method, params in [
        ("scheduler.status", None),
        ("scheduler.list", None),
        ("scheduler.create", {"channel_id": "default", "autonomy_level": 3}),
    ]:
        frame = client.call(method, params)
        assert _collect_sensitive(frame) == [], f"{method} leaked keys"
        text = json.dumps(frame).lower()
        for kw in _SENSITIVE_VALUE_KEYWORDS:
            assert kw not in text, f"{method} leaked {kw!r}"
