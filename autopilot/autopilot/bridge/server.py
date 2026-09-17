"""stdio JSON-RPC bridge server.

Reads newline-delimited JSON-RPC requests from stdin, dispatches them to the
handler registry on a worker pool, and writes responses to stdout. Long-lived
backend calls run on pool threads so the loop stays responsive.

No HTTP. No sockets. No listening anywhere.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from autopilot.bridge.events import EVENTS
from autopilot.bridge.handlers import BridgeHandlers
from autopilot.bridge.protocol import (
    INTERNAL_ERROR,
    INVALID_REQUEST,
    make_error,
    make_notification,
    make_result,
    parse_request,
    ProtocolError,
)

_SHUTDOWN_GRACE_SECONDS = 0.35


class BridgeServer:
    def __init__(self, handlers: BridgeHandlers, max_workers: int = 8) -> None:
        self.handlers = handlers
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="bridge")
        self._write_lock = threading.Lock()
        self._exiting = threading.Event()
        self._output = getattr(sys.stdout, "buffer", sys.stdout)
        EVENTS.subscribe(self._forward_notification)

    # ------------------------------------------------------------------
    # output
    # ------------------------------------------------------------------
    def _write_line(self, text: str) -> None:
        line = (text + "\n").encode("utf-8")
        try:
            with self._write_lock:
                self._output.write(line)
                self._output.flush()
        except (BrokenPipeError, OSError):
            self._exiting.set()

    def _forward_notification(self, method: str, params: Any) -> None:
        if not self._exiting.is_set():
            self._write_line(make_notification(method, params))

    # ------------------------------------------------------------------
    # request handling
    # ------------------------------------------------------------------
    def _execute(self, raw: str) -> None:
        try:
            request_id, method, params = parse_request(raw)
        except ProtocolError as exc:
            # A malformed frame cannot carry an id: answer with id None.
            self._write_line(make_error(exc.data if isinstance(exc.data, (int, str)) else None, exc.code, exc.message))
            return

        notification = request_id is None
        try:
            result = self.handlers.dispatch(method, params)
        except ProtocolError as exc:
            if not notification:
                self._write_line(make_error(request_id, exc.code, exc.message, exc.data))
            return
        except Exception as exc:  # noqa: BLE001 — never crash the loop on a handler bug
            if not notification:
                self._write_line(
                    make_error(request_id, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")
                )
            return

        if not notification:
            self._write_line(make_result(request_id, result))

        if method == "system.shutdown":
            self._request_exit("shutdown")
        elif method == "system.restart":
            self._request_exit("restart")

    def _request_exit(self, reason: str) -> None:
        self._exiting.set()
        threading.Thread(
            target=self._exit_after_grace,
            args=(reason,),
            name="bridge-exiter",
            daemon=True,
        ).start()

    def _exit_after_grace(self, reason: str) -> None:
        time.sleep(_SHUTDOWN_GRACE_SECONDS)
        os._exit(0)

    # ------------------------------------------------------------------
    # main loop
    # ------------------------------------------------------------------
    def serve(self) -> int:
        input_stream = getattr(sys.stdin, "buffer", sys.stdin)
        try:
            while not self._exiting.is_set():
                raw_line = input_stream.readline()
                if not raw_line:
                    break
                raw = raw_line.decode("utf-8", "replace").strip()
                if not raw:
                    continue
                self._pool.submit(self._execute, raw)
        except (KeyboardInterrupt, OSError):
            pass
        finally:
            self._exiting.set()
            self._pool.shutdown(wait=True)
        return 0