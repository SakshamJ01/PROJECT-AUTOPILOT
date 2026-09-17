"""Bridge notification bus.

M1 is polling-based; this emitter exists so that future backend callbacks
(stage progress, worker events) can be forwarded to the Tauri host without
changing the transport. It is intentionally inert by default.
"""

from __future__ import annotations

import threading
from typing import Any, Callable


class EventBus:
    def __init__(self) -> None:
        self._listeners: list[Callable[[str, Any], None]] = []
        self._lock = threading.Lock()

    def subscribe(self, callback: Callable[[str, Any], None]) -> None:
        with self._lock:
            self._listeners.append(callback)

    def emit(self, method: str, params: Any = None) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(method, params)
            except Exception:  # noqa: BLE001 — never let a listener break the bridge
                pass

    def clear(self) -> None:
        with self._lock:
            self._listeners.clear()


EVENTS = EventBus()