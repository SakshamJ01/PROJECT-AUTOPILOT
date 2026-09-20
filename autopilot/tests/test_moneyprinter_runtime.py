"""Regression tests for the MoneyPrinterTurbo runtime lifecycle.

Covers the four required cases — already-running, auto-start, readiness
timeout, and clean shutdown — plus the fail-closed paths (not installed,
autostart disabled) and the no-duplicate-start guarantee.

Nothing here touches the real MoneyPrinterTurbo service: probing, home
resolution and process spawning are all patched.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autopilot.core.moneyprinter_runtime import (
    MoneyPrinterRuntime,
    find_moneyprinter_home,
    probe_mpt_api,
)


_FAKE_HOME = Path("C:/fake/MoneyPrinterTurbo")
_FAKE_PYTHON = "C:/fake/MoneyPrinterTurbo/.venv/Scripts/python.exe"


def _fake_home_valid(path: Path = _FAKE_HOME) -> None:
    """Patch filesystem checks so ``path`` looks like a real MPT checkout."""
    real_is_dir = Path.is_dir
    real_is_file = Path.is_file

    def fake_is_dir(self, *a, **k):
        if Path(self) == path:
            return True
        return real_is_dir(self, *a, **k)

    def fake_is_file(self, *a, **k):
        if Path(self) in (path / "main.py", path / "app" / "asgi.py", Path(_FAKE_PYTHON)):
            return True
        return real_is_file(self, *a, **k)

    patcher_dir = patch.object(Path, "is_dir", fake_is_dir)
    patcher_file = patch.object(Path, "is_file", fake_is_file)
    patcher_dir.start()
    patcher_file.start()
    return [patcher_dir, patcher_file]


@pytest.fixture()
def cleanup_patches():
    yield
    patch.stopall()


@pytest.fixture()
def runtime():
    return MoneyPrinterRuntime(
        endpoint="http://127.0.0.1:8080",
        startup_timeout_seconds=5.0,
        poll_interval_seconds=0.01,
    )


def _fake_proc(alive: bool = True) -> MagicMock:
    proc = MagicMock()
    proc.pid = 4242
    proc.poll.return_value = None if alive else 0
    proc.wait.return_value = 0
    return proc


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------


def test_probe_success_on_valid_response():
    with patch("autopilot.core.moneyprinter_runtime.urllib.request.urlopen") as mock_open:
        resp = MagicMock()
        resp.status = 200
        resp.__enter__.return_value = resp
        resp.read.return_value = b'{"status": 200, "message": "success", "data": {"tasks": []}}'
        mock_open.return_value = resp
        assert probe_mpt_api("http://127.0.0.1:8080") is True


def test_probe_failure_on_connection_refused():
    with patch(
        "autopilot.core.moneyprinter_runtime.urllib.request.urlopen",
        side_effect=ConnectionRefusedError("connection refused"),
    ):
        assert probe_mpt_api("http://127.0.0.1:59999", timeout=1.0) is False


def test_probe_normalises_trailing_slash():
    with patch("autopilot.core.moneyprinter_runtime.urllib.request.urlopen") as mock_open:
        resp = MagicMock()
        resp.status = 200
        resp.__enter__.return_value = resp
        resp.read.return_value = b'{"data": {}}'
        mock_open.return_value = resp
        assert probe_mpt_api("http://127.0.0.1:8080/") is True
        called_url = mock_open.call_args[0][0].full_url
        assert called_url == "http://127.0.0.1:8080/api/v1/tasks?page=1&page_size=1"


# ---------------------------------------------------------------------------
# home resolution
# ---------------------------------------------------------------------------


def test_find_home_uses_env_override(monkeypatch):
    monkeypatch.setenv("MONEYPRINTER_HOME", str(_FAKE_HOME))
    patches = _fake_home_valid()
    try:
        assert find_moneyprinter_home(Path("C:/some/repo")) == _FAKE_HOME
    finally:
        for p in patches:
            p.stop()


def test_find_home_returns_none_when_not_installed(tmp_path):
    with patch("autopilot.core.moneyprinter_runtime._is_valid_home", return_value=False):
        assert find_moneyprinter_home(tmp_path) is None


# ---------------------------------------------------------------------------
# 1. already-running: never spawns, never duplicates
# ---------------------------------------------------------------------------


def test_ensure_running_reuses_already_running_service(runtime):
    """The API is up, so no child is spawned even if we track none."""
    with patch.object(MoneyPrinterRuntime, "is_service_running", return_value=True), patch(
        "autopilot.core.moneyprinter_runtime.subprocess.Popen"
    ) as mock_popen:
        result = runtime.ensure_running()

    assert result["running"] is True
    assert result["mode"] == "already_running"
    mock_popen.assert_not_called()
    # No tracked child to shut down.
    runtime.shutdown()


def test_ensure_running_is_idempotent_across_calls(runtime):
    """Repeated ensure calls keep one child and never spawn a second."""
    proc = _fake_proc()
    started = []

    def fake_probe():
        # Becomes healthy exactly after the child is spawned.
        return len(started) > 0

    def fake_popen(cmd, **kw):
        started.append(cmd)
        return proc

    with patch.object(MoneyPrinterRuntime, "is_service_running", side_effect=fake_probe), patch(
        "autopilot.core.moneyprinter_runtime.subprocess.Popen", side_effect=fake_popen
    ), patch("autopilot.core.moneyprinter_runtime.resolve_mpt_python", return_value=_FAKE_PYTHON), patch(
        "autopilot.core.moneyprinter_runtime.find_moneyprinter_home", return_value=_FAKE_HOME
    ):
        first = runtime.ensure_running()
        second = runtime.ensure_running()
        third = runtime.ensure_running()

    assert first["running"] is True and first["mode"] == "started"
    assert second["mode"] == "already_running"
    assert third["mode"] == "already_running"
    # One child only — the same pid throughout.
    assert len(started) == 1
    assert first["pid"] == second["pid"] == third["pid"] == 4242
    runtime.shutdown()
    proc.terminate.assert_called()


# ---------------------------------------------------------------------------
# 2. auto-start: spawns the local service and waits for readiness
# ---------------------------------------------------------------------------


def test_ensure_running_auto_starts_and_waits_for_readiness(runtime, tmp_path):
    """Service down -> spawn from the clone -> readiness endpoint answers."""
    proc = _fake_proc()
    probe_calls = []

    def fake_probe():
        probe_calls.append(1)
        return len(probe_calls) >= 3  # ready on the 3rd probe

    captured = {}

    def fake_popen(cmd, **kw):
        captured["cmd"] = cmd
        captured["cwd"] = kw.get("cwd")
        return proc

    with patch.object(MoneyPrinterRuntime, "is_service_running", side_effect=fake_probe), patch(
        "autopilot.core.moneyprinter_runtime.subprocess.Popen", side_effect=fake_popen
    ), patch("autopilot.core.moneyprinter_runtime.resolve_mpt_python", return_value=_FAKE_PYTHON), patch(
        "autopilot.core.moneyprinter_runtime.find_moneyprinter_home", return_value=_FAKE_HOME
    ), patch.object(
        runtime, "_service_log_path", return_value=tmp_path / "mpt.log"
    ):
        result = runtime.ensure_running()

    assert result["running"] is True
    assert result["mode"] == "started"
    assert result["pid"] == 4242
    assert result["managed"] is True
    # Entrypoint is the v1.3.6 API server, run from the clone's root.
    assert captured["cmd"] == [_FAKE_PYTHON, "-u", "main.py"]
    assert captured["cwd"] == str(_FAKE_HOME)
    runtime.shutdown()


def test_ensure_running_uses_venv_python_when_available(tmp_path):
    """The clone's own virtualenv interpreter is preferred (has fastapi/uvicorn)."""
    home = tmp_path / "MoneyPrinterTurbo"
    (home / "app").mkdir(parents=True)
    (home / "main.py").write_text("# entrypoint")
    (home / "app" / "asgi.py").write_text("# asgi")
    venv_python = home / ".venv" / "Scripts" / "python.exe"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("")

    from autopilot.core.moneyprinter_runtime import resolve_mpt_python

    assert resolve_mpt_python(home) == str(venv_python)


def test_ensure_running_rejects_interpreter_without_uvicorn(tmp_path):
    """Never silently pick an interpreter that cannot run the service."""
    from autopilot.core.moneyprinter_runtime import resolve_mpt_python

    with patch("autopilot.core.moneyprinter_runtime.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        assert resolve_mpt_python(tmp_path) is None


# ---------------------------------------------------------------------------
# 3. readiness timeout: bounded, then fail-closed
# ---------------------------------------------------------------------------


def test_ensure_running_times_out_and_shuts_down_child(runtime):
    """Service never becomes ready -> bounded timeout, child terminated, clear error."""
    proc = _fake_proc()

    with patch.object(MoneyPrinterRuntime, "is_service_running", return_value=False), patch(
        "autopilot.core.moneyprinter_runtime.subprocess.Popen", return_value=proc
    ), patch("autopilot.core.moneyprinter_runtime.resolve_mpt_python", return_value=_FAKE_PYTHON), patch(
        "autopilot.core.moneyprinter_runtime.find_moneyprinter_home", return_value=_FAKE_HOME
    ), patch.object(
        runtime, "_service_log_path", return_value=Path("nul")
    ):
        result = runtime.ensure_running()

    assert result["running"] is False
    assert result["mode"] == "readiness_timeout"
    assert "did not become ready" in result["error"]
    # Child cleaned up so a later retry starts fresh.
    proc.terminate.assert_called()
    assert runtime._proc is None


def test_ensure_running_reports_child_exit_during_startup(runtime):
    """If the child exits before readiness, we fail fast rather than waiting."""
    proc = MagicMock()
    proc.pid = 999
    proc.poll.return_value = 1  # already dead
    proc.wait.return_value = 1

    with patch.object(MoneyPrinterRuntime, "is_service_running", return_value=False), patch(
        "autopilot.core.moneyprinter_runtime.subprocess.Popen", return_value=proc
    ), patch("autopilot.core.moneyprinter_runtime.resolve_mpt_python", return_value=_FAKE_PYTHON), patch(
        "autopilot.core.moneyprinter_runtime.find_moneyprinter_home", return_value=_FAKE_HOME
    ), patch.object(
        runtime, "_service_log_path", return_value=Path("nul")
    ):
        result = runtime.ensure_running()

    assert result["running"] is False
    assert result["mode"] == "readiness_timeout"


# ---------------------------------------------------------------------------
# 4. clean shutdown
# ---------------------------------------------------------------------------


def test_shutdown_terminates_tracked_child(runtime):
    proc = _fake_proc()
    runtime._proc = proc

    runtime.shutdown()

    proc.terminate.assert_called_once()
    proc.wait.assert_called()
    assert runtime._proc is None


def test_shutdown_is_idempotent_and_safe_when_untracked(runtime):
    runtime.shutdown()  # no child: must not raise
    runtime.shutdown()


def test_shutdown_kills_child_that_ignores_terminate(runtime):
    proc = _fake_proc()
    # terminate then wait times out -> kill is used.
    proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="mpt", timeout=10), 0]
    runtime._proc = proc

    runtime.shutdown()

    proc.kill.assert_called_once()
    assert runtime._proc is None


# ---------------------------------------------------------------------------
# fail-closed paths
# ---------------------------------------------------------------------------


def test_ensure_running_reports_when_not_installed(runtime):
    """No clone found -> deterministic failure with an actionable message."""
    with patch.object(MoneyPrinterRuntime, "is_service_running", return_value=False), patch(
        "autopilot.core.moneyprinter_runtime.find_moneyprinter_home", return_value=None
    ):
        result = runtime.ensure_running()

    assert result["running"] is False
    assert result["mode"] == "not_installed"
    assert "not found" in result["error"].lower()
    runtime.shutdown()


def test_ensure_running_respects_autostart_disabled(runtime):
    """autostart=False never spawns and explains how to proceed."""
    runtime.autostart = False
    with patch.object(MoneyPrinterRuntime, "is_service_running", return_value=False), patch(
        "autopilot.core.moneyprinter_runtime.subprocess.Popen"
    ) as mock_popen:
        result = runtime.ensure_running()

    assert result["running"] is False
    assert result["mode"] == "autostart_disabled"
    mock_popen.assert_not_called()


def test_ensure_running_reports_missing_interpreter(runtime):
    """Clone present but no usable interpreter -> start_failed, no silent fallback."""
    with patch.object(MoneyPrinterRuntime, "is_service_running", return_value=False), patch(
        "autopilot.core.moneyprinter_runtime.find_moneyprinter_home", return_value=_FAKE_HOME
    ), patch("autopilot.core.moneyprinter_runtime.resolve_mpt_python", return_value=None):
        result = runtime.ensure_running()

    assert result["running"] is False
    assert result["mode"] == "start_failed"
    assert "Python interpreter" in result["error"]


def test_ensure_running_concurrent_calls_do_not_double_spawn(runtime):
    """Serialized by the lock: two concurrent ensures yield one child."""
    proc = _fake_proc()
    spawns = []

    def fake_probe():
        return len(spawns) > 0

    def fake_popen(cmd, **kw):
        spawns.append(cmd)
        return proc

    with patch.object(MoneyPrinterRuntime, "is_service_running", side_effect=fake_probe), patch(
        "autopilot.core.moneyprinter_runtime.subprocess.Popen", side_effect=fake_popen
    ), patch("autopilot.core.moneyprinter_runtime.resolve_mpt_python", return_value=_FAKE_PYTHON), patch(
        "autopilot.core.moneyprinter_runtime.find_moneyprinter_home", return_value=_FAKE_HOME
    ), patch.object(
        runtime, "_service_log_path", return_value=Path("nul")
    ):
        import threading

        threads = [threading.Thread(target=runtime.ensure_running) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert len(spawns) == 1
    runtime.shutdown()
