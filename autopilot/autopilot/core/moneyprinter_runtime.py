"""MoneyPrinterTurbo local API service lifecycle — desktop runtime.

Ensures the MoneyPrinterTurbo v1.3.6 FastAPI service is running before a
production render begins. It probes the API first (so it never duplicates an
already-running service), spawns the local service from the cloned
MoneyPrinterTurbo home when it is absent, waits for readiness with a bounded
timeout, and tracks the child process so it can be shut down cleanly with
Autopilot Desktop.

The resolution logic assumes nothing about the environment, so it works when
the desktop ``.exe`` is launched directly from File Explorer (no PATH, no
inherited env vars): the MoneyPrinterTurbo home and its interpreter are located
purely from the filesystem.
"""
from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

# The v1.3.6 API entrypoint and health endpoint this runtime manages.
_MPT_ENTRYPOINT = "main.py"
_MPT_ASGI_MODULE = Path("app") / "asgi.py"
_HEALTH_PATH = "/api/v1/tasks?page=1&page_size=1"


def _project_root() -> Path:
    try:
        from autopilot.core.config import CONFIG

        return Path(CONFIG.project_root)
    except Exception:  # noqa: BLE001 — keep the runtime usable standalone
        return Path(__file__).resolve().parent.parent.parent


def _is_valid_home(path: Path) -> bool:
    """A MoneyPrinterTurbo home must expose the API entrypoint and ASGI app."""
    return path.is_dir() and (path / _MPT_ENTRYPOINT).is_file() and (path / _MPT_ASGI_MODULE).is_file()


def _candidate_homes(root: Optional[Path]) -> list[Path]:
    """Filesystem locations where a MoneyPrinterTurbo clone may live.

    Ordered by preference. The sibling-of-repo layout is the documented one,
    so it comes right after the explicit overrides.
    """
    candidates: list[Path] = []
    home = Path.home()
    if root is not None:
        # <repo>/../MoneyPrinterTurbo — the standard sibling checkout.
        candidates.append(root.parent / "MoneyPrinterTurbo")
        # Any ancestor's sibling (monorepo / nested workspace layouts).
        parent = root.parent
        for _ in range(4):
            if parent.parent == parent:
                break
            candidates.append(parent.parent / "MoneyPrinterTurbo")
            parent = parent.parent
    candidates.extend(
        [
            home / "MoneyPrinterTurbo",
            home / "Documents" / "MoneyPrinterTurbo",
            home / "Projects" / "MoneyPrinterTurbo",
            Path("/opt/MoneyPrinterTurbo"),
            Path("/srv/MoneyPrinterTurbo"),
        ]
    )
    return candidates


def find_moneyprinter_home(root: Optional[Path] = None) -> Optional[Path]:
    """Locate the local MoneyPrinterTurbo clone, or ``None`` if not installed.

    Resolution order: ``MONEYPRINTER_HOME`` env, config field, then filesystem
    candidates. A candidate is only accepted if it really is an MPT checkout.
    """
    env_home = os.environ.get("MONEYPRINTER_HOME")
    if env_home:
        p = Path(env_home)
        if _is_valid_home(p):
            return p

    try:
        from autopilot.core.config import CONFIG

        cfg_home = getattr(CONFIG, "moneyprinter_home", None)
    except Exception:  # noqa: BLE001
        cfg_home = None
    if cfg_home:
        p = Path(cfg_home)
        if _is_valid_home(p):
            return p

    for candidate in _candidate_homes(root if root is not None else _project_root()):
        if _is_valid_home(candidate):
            return candidate

    # Last resort: a case-insensitive scan of the repo's parent directory
    # (Windows checkouts are often renamed, e.g. "moneyprinterturbo").
    search_dir = (root if root is not None else _project_root()).parent
    if search_dir.is_dir():
        try:
            for entry in search_dir.iterdir():
                if entry.is_dir() and entry.name.lower() == "moneyprinterturbo" and _is_valid_home(entry):
                    return entry
        except (OSError, PermissionError):
            pass

    return None


def resolve_mpt_python(home: Path) -> Optional[str]:
    """Resolve the interpreter that can run the MPT service.

    Prefers the clone's own virtualenv (which has fastapi/uvicorn/loguru and
    the ``app`` package on ``sys.path``). Falls back to the current interpreter
    only if it can actually import uvicorn — never silently picks an
    interpreter that cannot start the service.
    """
    if os.name == "nt":
        venv_python = home / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = home / ".venv" / "bin" / "python"
    if venv_python.is_file():
        return str(venv_python)

    current = sys.executable
    probe = subprocess.run(
        [current, "-c", "import uvicorn, fastapi"],
        capture_output=True,
        timeout=15,
    )
    if probe.returncode == 0:
        return current
    return None


def probe_mpt_api(endpoint: str, timeout: float = 3.0) -> bool:
    """Return True only if the MPT API answers the readiness endpoint."""
    url = f"{endpoint.rstrip('/')}{_HEALTH_PATH}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ProjectAutopilot/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or getattr(resp, "code", 200)
            if status != 200:
                return False
            body = json.loads(resp.read().decode("utf-8"))
            return isinstance(body, dict) and (
                body.get("status") == 200 or body.get("message") == "success" or "data" in body
            )
    except Exception:  # noqa: BLE001 — any failure means not ready
        return False


class MoneyPrinterRuntime:
    """Manages the local MoneyPrinterTurbo API service process."""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        home: Optional[Path] = None,
        autostart: bool = True,
        startup_timeout_seconds: float = 60.0,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        from autopilot.core.config import CONFIG

        self.endpoint = (
            endpoint
            or os.environ.get("MONEYPRINTER_ENDPOINT")
            or getattr(CONFIG, "moneyprinter_endpoint", None)
            or "http://127.0.0.1:8080"
        )
        self.autostart = autostart
        self.startup_timeout_seconds = startup_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self._home = home
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        try:
            from autopilot.core.logging import StructuredLogger

            self.logger = StructuredLogger(job_id="moneyprinter-runtime", stage="production_moneyprinter")
        except Exception:  # noqa: BLE001 — logging must never break the runtime
            self.logger = None

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------
    def resolved_home(self) -> Optional[Path]:
        if self._home is not None:
            return self._home if _is_valid_home(self._home) else None
        return find_moneyprinter_home()

    def is_service_running(self) -> bool:
        return probe_mpt_api(self.endpoint)

    def _child_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def status(self) -> Dict[str, Any]:
        """Cheap read-only snapshot: probes the API and reports the child."""
        running = self.is_service_running()
        result: Dict[str, Any] = {
            "engine": "moneyprinterturbo",
            "version": "v1.3.6",
            "running": running,
            "endpoint": self.endpoint,
            "managed": self._child_alive(),
            "mode": "http_api" if running else "stopped",
        }
        child_pid = self._proc.pid if self._child_alive() else None
        if child_pid is not None:
            result["pid"] = child_pid
        home = self.resolved_home()
        if home is not None:
            result["home"] = str(home)
        return result

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def ensure_running(self) -> Dict[str, Any]:
        """Guarantee the MPT API is answering, starting it if needed.

        Idempotent and concurrency-safe: the API is probed first, so an
        already-running service (started by us, by the WebUI, or by a
        developer) is never duplicated.
        """
        with self._lock:
            # 1. Already running — nothing to do (no duplicate process).
            if self.is_service_running():
                self._reap_dead_child()
                return {
                    "running": True,
                    "mode": "already_running",
                    "endpoint": self.endpoint,
                    "managed": self._child_alive(),
                    "pid": self._proc.pid if self._child_alive() else None,
                }

            # 2. We own a child that has not bound the port yet — wait for it.
            if self._child_alive():
                ready = self._wait_for_readiness()
                return self._ready_result(ready, "started")

            if not self.autostart:
                return {
                    "running": False,
                    "mode": "autostart_disabled",
                    "endpoint": self.endpoint,
                    "error": (
                        "MoneyPrinterTurbo is not running and autostart is disabled. "
                        "Start the local API service or enable moneyprinter_autostart."
                    ),
                }

            # 3. Locate the local clone.
            home = self.resolved_home()
            if home is None:
                return {
                    "running": False,
                    "mode": "not_installed",
                    "endpoint": self.endpoint,
                    "error": (
                        "MoneyPrinterTurbo v1.3.6 was not found on this machine. "
                        "Clone https://github.com/harry0703/MoneyPrinterTurbo next to the "
                        "project (or set MONEYPRINTER_HOME) and start its API service."
                    ),
                }

            # 4. Start the service from the clone.
            started = self._spawn_service(home)
            if not started:
                return {
                    "running": False,
                    "mode": "start_failed",
                    "endpoint": self.endpoint,
                    "home": str(home),
                    "error": (
                        "Could not start the MoneyPrinterTurbo API service: no usable "
                        f"Python interpreter found under {home}. Ensure the clone's "
                        "virtualenv is installed (its .venv must contain fastapi/uvicorn)."
                    ),
                }

            ready = self._wait_for_readiness()
            return self._ready_result(ready, "started")

    def _spawn_service(self, home: Path) -> bool:
        python = resolve_mpt_python(home)
        if python is None:
            return False
        try:
            log_path = self._service_log_path()
            log_file = open(log_path, "ab")  # noqa: SIM115 — closed when the child exits
            popen_kwargs: Dict[str, Any] = {
                "cwd": str(home),
                "stdout": log_file,
                "stderr": subprocess.STDOUT,
                "stdin": subprocess.DEVNULL,
            }
            if os.name == "nt":
                popen_kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
            self._proc = subprocess.Popen([python, "-u", _MPT_ENTRYPOINT], **popen_kwargs)
            self._log("moneyprinter_service_started", {"home": str(home), "pid": self._proc.pid})
            return True
        except Exception as exc:  # noqa: BLE001 — surface as a start failure
            self._log("moneyprinter_service_start_error", {"error": str(exc)})
            return False

    def _wait_for_readiness(self) -> bool:
        deadline = time.time() + self.startup_timeout_seconds
        while time.time() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                self._log("moneyprinter_service_exited", {"code": self._proc.returncode})
                return False
            if self.is_service_running():
                return True
            time.sleep(self.poll_interval_seconds)
        return self.is_service_running()

    def _ready_result(self, ready: bool, mode: str) -> Dict[str, Any]:
        if ready:
            return {
                "running": True,
                "mode": mode,
                "endpoint": self.endpoint,
                "managed": self._child_alive(),
                "pid": self._proc.pid if self._child_alive() else None,
            }
        # Service never became ready: stop the child so a later retry is clean.
        self._shutdown_locked()
        return {
            "running": False,
            "mode": "readiness_timeout",
            "endpoint": self.endpoint,
            "error": (
                f"MoneyPrinterTurbo API did not become ready at {self.endpoint} within "
                f"{self.startup_timeout_seconds:.0f}s. See artifacts/logs/moneyprinterturbo.log. "
                "The service may still be initialising — retry, or start it manually."
            ),
        }

    def _reap_dead_child(self) -> None:
        if self._proc is not None and self._proc.poll() is not None:
            self._proc = None

    def _service_log_path(self) -> Path:
        try:
            from autopilot.core.config import CONFIG

            d = CONFIG.get_artifacts_dir() / "logs"
        except Exception:  # noqa: BLE001
            d = Path.cwd()
        d.mkdir(parents=True, exist_ok=True)
        return d / "moneyprinterturbo.log"

    def shutdown(self) -> None:
        """Terminate a service process we started. Safe to call repeatedly."""
        with self._lock:
            self._shutdown_locked()

    def _shutdown_locked(self) -> None:
        """Terminate the child; caller must already hold ``self._lock``."""
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=10)
            self._log("moneyprinter_service_stopped", {"pid": proc.pid})
        except Exception:  # noqa: BLE001 — shutdown must never raise
            pass
        finally:
            self._proc = None

    def _log(self, event: str, details: Dict[str, Any]) -> None:
        if self.logger is None:
            return
        try:
            self.logger.info(event, details=details)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Process-tracked singleton
# ---------------------------------------------------------------------------

_RUNTIME: Optional[MoneyPrinterRuntime] = None
_RUNTIME_LOCK = threading.Lock()


def get_runtime() -> MoneyPrinterRuntime:
    """The process-wide MPT runtime; constructed lazily from config."""
    global _RUNTIME
    if _RUNTIME is None:
        with _RUNTIME_LOCK:
            if _RUNTIME is None:
                from autopilot.core.config import CONFIG

                _RUNTIME = MoneyPrinterRuntime(
                    autostart=bool(getattr(CONFIG, "moneyprinter_autostart", True)),
                    startup_timeout_seconds=float(
                        getattr(CONFIG, "moneyprinter_startup_timeout_seconds", 60.0)
                    ),
                )
                atexit.register(shutdown_moneyprinter)
    return _RUNTIME


def ensure_moneyprinter_running() -> Dict[str, Any]:
    """Ensure the MPT API is up; never raises — returns a status dict."""
    return get_runtime().ensure_running()


def shutdown_moneyprinter() -> None:
    """Best-effort shutdown of the MPT service we started (atexit hook)."""
    global _RUNTIME
    if _RUNTIME is None:
        return
    try:
        _RUNTIME.shutdown()
    except Exception:  # noqa: BLE001
        pass
    _RUNTIME = None
