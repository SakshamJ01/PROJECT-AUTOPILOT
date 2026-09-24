"""JSON-RPC 2.0 framing for the PROJECT-AUTOPILOT desktop bridge.

Newline-delimited over stdin/stdout — no HTTP, no sockets.
"""

from __future__ import annotations

import datetime
import json
import re
from typing import Any

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# JSON-RPC error labels that should never appear in any response payload.
_SENSITIVE_KEYWORDS = frozenset(
    {
        "access_token",
        "token_path",
        "client_secrets_path",
        "youtube_access_token",
        "youtube_token_path",
        "gemini_api_key",
        "openrouter_api_key",
        "postiz_api_key",
        "authorization_code",
        "refresh_token",
    }
)

_SENSITIVE_KEY_PATTERN = re.compile(
    r"(token|secret|password|api[_-]?key|credential|auth|client_secrets)",
    re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(r"Bearer\s+[A-Za-z0-9_\-\.~+/]+=*", re.IGNORECASE)


# Keys that explicitly describe presence or boolean state and never contain secrets
_SAFE_STATUS_KEYS = frozenset(
    {
        "authenticated",
        "secrets_present",
        "token_present",
        "auth_status",
        "oauth_configured",
        "status",
        "guidance",
    }
)


def redact_sensitive(data: Any) -> Any:
    """Recursively scrub sensitive tokens, credentials, and API keys."""
    if isinstance(data, dict):
        cleaned: dict[str, Any] = {}
        for k, v in data.items():
            k_str = str(k).lower()
            if k_str in _SAFE_STATUS_KEYS or isinstance(v, bool):
                cleaned[k] = redact_sensitive(v)
            elif k_str in _SENSITIVE_KEYWORDS or _SENSITIVE_KEY_PATTERN.search(k_str):
                cleaned[k] = "[REDACTED]"
            else:
                cleaned[k] = redact_sensitive(v)
        return cleaned
    elif isinstance(data, (list, tuple)):
        return [redact_sensitive(item) for item in data]
    elif isinstance(data, set):
        return {redact_sensitive(item) for item in data}
    elif isinstance(data, str):
        return _BEARER_PATTERN.sub("Bearer [REDACTED]", data)
    return data


class ProtocolError(Exception):
    """Non-recoverable framing / validation error."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)


# ---------------------------------------------------------------------------
# Outbound
# ---------------------------------------------------------------------------

def make_request(request_id: Any, method: str, params: dict | None = None) -> str:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    return json.dumps(payload, default=str)


def make_result(request_id: Any, result: Any) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "result": redact_sensitive(result)}, default=str)


def make_error(request_id: Any, code: int, message: str, data: Any = None) -> str:
    err: dict[str, Any] = {
        "code": code,
        "message": redact_sensitive(message),
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if data is not None:
        err["data"] = redact_sensitive(data)
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "error": err}, default=str)


def make_notification(method: str, params: Any = None) -> str:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        payload["params"] = redact_sensitive(params)
    return json.dumps(payload, default=str)


# ---------------------------------------------------------------------------
# Inbound validation
# ---------------------------------------------------------------------------

def parse_request(raw: str) -> tuple[Any, str, dict | None]:
    """Return ``(id, method, params)`` or raise :class:`ProtocolError`."""
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError(PARSE_ERROR, f"Parse error: {exc}") from exc

    if not isinstance(obj, dict):
        raise ProtocolError(INVALID_REQUEST, "Request must be a JSON object")

    method = obj.get("method")
    if not isinstance(method, str) or not method.strip():
        raise ProtocolError(
            INVALID_REQUEST,
            "Invalid Request: 'method' must be a non-empty string",
        )

    params = obj.get("params")
    if params is not None and not isinstance(params, dict):
        raise ProtocolError(INVALID_PARAMS, "'params' must be a JSON object or omitted")

    return obj.get("id"), method, params


def assert_params_keys(params: dict | None, required: tuple[str, ...] = (), optional: tuple[str, ...] = ()) -> dict:
    params = params or {}
    missing = [k for k in required if k not in params]
    if missing:
        raise ProtocolError(INVALID_PARAMS, f"Missing required params: {', '.join(missing)}")
    if optional:
        return {k: params[k] for k in required + optional if k in params}
    return params