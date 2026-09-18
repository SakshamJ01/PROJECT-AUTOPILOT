"""Autonomous public publishing switch — Milestone 6.

Single authoritative place that owns the ENABLE/DISABLE semantics for the
autonomous public publishing kill switch:

* The persisted ``config`` table row ``autonomy_auto_publish`` is authoritative
  once an operator acts (survives reloads and process restarts).
* Before that, the value falls back to the backend configuration
  (``Config.autonomy_auto_publish``, e.g. the ``AUTOPILOT_AUTONOMY_AUTO_PUBLISH``
  env override). Default is OFF.
* ``enable`` is fail-closed: every existing publishing/QA/approval/idempotency/
  limits/cooldown prerequisite must verify first, otherwise the switch is left
  untouched and a structured failure names the failed prerequisite.
* ``disable`` is the kill switch: immediate, idempotent, persistent.
* ``effective_auto_publish`` is the single resolver used by the status surface
  AND the final publish boundary, so no earlier-captured value can matter.

Never returns tokens, OAuth paths, credentials, or secret material.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from autopilot.core.config import Config
from autopilot.db.manager import DBManager

CONFIG_KEY = "autonomy_auto_publish"

GUARDRAILS = [
    "QA PASS required",
    "artifact + checksum validation",
    "supported target",
    "authenticated account",
    "idempotency + duplicate prevention",
    "channel/daily limits + cooldown",
    "policy gate + visibility policy",
    "failure handling + kill switch",
]

BOUNDARY = (
    "Autonomous public publishing is disabled by default and can be enabled "
    "only by a valid backend operation. Enabling validates every publishing "
    "prerequisite and fails closed otherwise; disabling (the kill switch) is "
    "immediate, persistent and stops any further autonomous public publication "
    "at the final publish boundary. This surface never bypasses approval."
)

_TRUE_STRINGS = ("1", "true", "yes")


def effective_auto_publish(config: Config, db: Optional[DBManager]) -> bool:
    """Resolve the authoritative switch state.

    DB row (once written by enable/disable) wins; otherwise fall back to the
    backend configuration. Always OFF unless something explicitly turned it on.
    """
    if db is not None:
        persisted = db.get_config_value(CONFIG_KEY)
        if persisted is not None:
            return _to_bool(persisted)
    return bool(getattr(config, "autonomy_auto_publish", False))


def _to_bool(value: Any) -> bool:
    return str(value).strip().lower() in _TRUE_STRINGS


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_state(config: Config, db: DBManager, enabled: bool) -> None:
    """Persist the override and mirror it on the shared config instance."""
    db.set_config_value(CONFIG_KEY, "true" if enabled else "false")
    # The runtime config attribute drives any reader that has not switched to
    # the DB resolver; keeping it in sync makes the new state visible instantly.
    config.autonomy_auto_publish = enabled


def _controlled_at(db: DBManager) -> Optional[str]:
    try:
        with db._connect() as conn:
            row = conn.execute(
                "SELECT updated_at FROM config WHERE key = ?", (CONFIG_KEY,)
            ).fetchone()
            return str(row["updated_at"]) if row else None
    except Exception:  # noqa: BLE001 — status must never crash over a schema quirk
        return None


def _table_exists(db: DBManager, table: str) -> bool:
    try:
        with db._connect() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            return row is not None
    except Exception:  # noqa: BLE001
        return False


def _column_exists(db: DBManager, table: str, column: str) -> bool:
    try:
        with db._connect() as conn:
            cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
            return any(c["name"] == column for c in cols)
    except Exception:  # noqa: BLE001
        return False


def _youtube_authenticated(config: Config) -> bool:
    from autopilot.providers.youtube_oauth import resolve_youtube_access_token

    try:
        return bool(resolve_youtube_access_token(config))
    except Exception:  # noqa: BLE001 — readiness must never raise
        return False


def _prerequisite_checks(config: Config, db: DBManager) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []

    # 1. Publishing provider configured.
    try:
        from autopilot.providers.youtube_publisher import YouTubePublisher  # noqa: F401

        provider_ok = True
    except Exception:  # noqa: BLE001
        provider_ok = False
    checks.append(
        {
            "check": "publishing_provider",
            "ok": provider_ok,
            "message": (
                "Publishing provider is configured."
                if provider_ok
                else "No publishing provider is configured for autonomous output."
            ),
        }
    )

    # 2. Supported target platform.
    platform = str(getattr(config, "publish_default_platform", "youtube")).lower()
    platform_ok = platform in ("youtube", "postiz")
    checks.append(
        {
            "check": "target_platform_supported",
            "ok": platform_ok,
            "message": (
                f"Target platform '{platform}' is supported."
                if platform_ok
                else f"Unsupported target platform '{platform}'."
            ),
        }
    )

    # 3. YouTube authentication availability (no secrets exposed).
    auth_ok = _youtube_authenticated(config) and _table_exists(db, "config")
    checks.append(
        {
            "check": "youtube_auth",
            "ok": auth_ok,
            "message": (
                "YouTube upload authorisation is verified."
                if auth_ok
                else "YouTube upload authorisation is not available; run the backend "
                "authorisation flow before enabling autonomous public publishing."
            ),
        }
    )

    # 4. Approval / publication architecture valid.
    approval_ok = _table_exists(db, "publish_approvals") and _table_exists(db, "publish_records")
    checks.append(
        {
            "check": "approval_architecture",
            "ok": approval_ok,
            "message": (
                "Operator approval/publication architecture is present."
                if approval_ok
                else "Operator approval/publication records are unavailable."
            ),
        }
    )

    # 5. QA gate available.
    try:
        import autopilot.core.qa_engine  # noqa: F401

        qa_ok = True
    except Exception:  # noqa: BLE001
        qa_ok = False
    checks.append(
        {
            "check": "qa_gate",
            "ok": bool(qa_ok and _table_exists(db, "qa_runs")),
            "message": (
                "QA gate is available and publishes only gate-passed content."
                if qa_ok and _table_exists(db, "qa_runs")
                else "QA gate is not available; publishing is not safe."
            ),
        }
    )

    # 6. Artifact / checksum gate available.
    from autopilot.core.publisher import compute_file_sha256

    checksum_ok = callable(compute_file_sha256) and _table_exists(db, "artifacts")
    checks.append(
        {
            "check": "artifact_checksum_gate",
            "ok": checksum_ok,
            "message": (
                "Media artifact checksum gate is available."
                if checksum_ok
                else "Media artifact records are unavailable."
            ),
        }
    )

    # 7. Idempotency gate available.
    idem_ok = _table_exists(db, "publish_records") and _column_exists(db, "publish_records", "idempotency_key")
    checks.append(
        {
            "check": "idempotency_gate",
            "ok": idem_ok,
            "message": (
                "Deterministic publication idempotency is available."
                if idem_ok
                else "Idempotency store is unavailable."
            ),
        }
    )

    # 8. Duplicate prevention available.
    dup_ok = hasattr(db, "get_publication_by_idempotency")
    checks.append(
        {
            "check": "duplicate_prevention",
            "ok": bool(dup_ok and idem_ok),
            "message": (
                "Duplicate publication prevention is available."
                if dup_ok and idem_ok
                else "Duplicate publication prevention is unavailable."
            ),
        }
    )

    # 9. Daily / channel limits configured.
    daily_limit = getattr(config, "autonomy_max_daily_jobs", 0)
    max_queued = getattr(config, "queue_max_queued_jobs", 0)
    limits_ok = int(daily_limit or 0) > 0 and int(max_queued or 0) > 0 and _table_exists(db, "channel_daily_quotas")
    checks.append(
        {
            "check": "daily_channel_limits",
            "ok": limits_ok,
            "message": (
                "Daily and channel production limits are configured."
                if limits_ok
                else "Daily/channel production limits are not configured."
            ),
        }
    )

    # 10. Cooldown configured.
    cooldown = getattr(config, "autonomy_topic_cooldown_days", 0)
    cooldown_ok = cooldown is not None and int(cooldown) >= 0
    checks.append(
        {
            "check": "cooldown_configured",
            "ok": cooldown_ok,
            "message": (
                f"Topic cooldown is configured ({cooldown} days)."
                if cooldown_ok
                else "Topic cooldown is not configured."
            ),
        }
    )

    # 11. Kill switch currently OFF.
    switch_off = not effective_auto_publish(config, db)
    checks.append(
        {
            "check": "kill_switch_off",
            "ok": switch_off,
            "message": (
                "Switch is currently OFF and ready to enable."
                if switch_off
                else "Switch is currently ON; it must be OFF before enabling."
            ),
        }
    )

    # 12. Configuration internally valid.
    try:
        visibility = str(getattr(config, "publish_default_visibility", "private")).lower()
        cfg_messages: List[str] = []
        if visibility not in ("private", "unlisted", "public"):
            cfg_messages.append("publish_default_visibility is invalid")
        if int(getattr(config, "autonomy_max_daily_jobs", 0) or 0) < 0:
            cfg_messages.append("autonomy_max_daily_jobs is negative")
        if int(getattr(config, "autonomy_topic_cooldown_days", 0) or 0) < 0:
            cfg_messages.append("autonomy_topic_cooldown_days is negative")
        cfg_ok = not cfg_messages
    except Exception:  # noqa: BLE001
        cfg_ok = False
        cfg_messages = ["an internal configuration exception occurred"]
    checks.append(
        {
            "check": "config_valid",
            "ok": cfg_ok,
            "message": (
                "Backend configuration is internally valid."
                if cfg_ok
                else "Backend configuration is invalid: " + "; ".join(cfg_messages)
            ),
        }
    )

    return checks


def validate_auto_publish_prerequisites(config: Config, db: DBManager) -> Dict[str, Any]:
    """Structural readiness report. Never raises, never leaks secrets."""
    checks = _prerequisite_checks(config, db)
    passed = [c["check"] for c in checks if c["ok"]]
    failed = [c["check"] for c in checks if not c["ok"]]
    return {
        "ok": not failed,
        "passed": passed,
        "failed": failed,
        "checks": checks,
    }


def enable_auto_publish(config: Config, db: DBManager) -> Dict[str, Any]:
    """Enable autonomous public publishing, fail-closed.

    No-op success (idempotent) when already enabled. Otherwise every
    prerequisite must pass; if any fails the switch stays untouched and the
    failure names the failed prerequisite(s).
    """
    now = _now_iso()
    if effective_auto_publish(config, db):
        return {
            "ok": True,
            "changed": False,
            "enabled": True,
            "state": "ENABLED",
            "label": "AUTONOMOUS PUBLIC PUBLISHING ENABLED",
            "default": False,
            "controlled_by": "backend",
            "guardrails": list(GUARDRAILS),
            "boundary": BOUNDARY,
            "controlled_at": _controlled_at(db) or now,
            "timestamp": now,
        }

    prereqs = validate_auto_publish_prerequisites(config, db)
    if not prereqs["ok"]:
        failed = ", ".join(prereqs["failed"])
        return {
            "ok": False,
            "changed": False,
            "enabled": False,
            "state": "DISABLED",
            "label": "AUTONOMOUS PUBLIC PUBLISHING DISABLED",
            "default": False,
            "controlled_by": "backend",
            "guardrails": list(GUARDRAILS),
            "boundary": BOUNDARY,
            "reason": (
                "Cannot enable autonomous public publishing. Prerequisite(s) "
                f"not met: {failed}. The switch was not changed."
            ),
            "prerequisites": prereqs,
            "controlled_at": _controlled_at(db),
            "timestamp": now,
        }

    _set_state(config, db, True)
    return {
        "ok": True,
        "changed": True,
        "enabled": True,
        "state": "ENABLED",
        "label": "AUTONOMOUS PUBLIC PUBLISHING ENABLED",
        "default": False,
        "controlled_by": "backend",
        "guardrails": list(GUARDRAILS),
        "boundary": BOUNDARY,
        "prerequisites": prereqs,
        "controlled_at": now,
        "timestamp": now,
    }


def disable_auto_publish(config: Config, db: DBManager) -> Dict[str, Any]:
    """Kill switch: immediately and persistently disable autonomous publishing."""
    now = _now_iso()
    was_enabled = effective_auto_publish(config, db)
    _set_state(config, db, False)
    return {
        "ok": True,
        "changed": bool(was_enabled),
        "enabled": False,
        "state": "DISABLED",
        "label": "AUTONOMOUS PUBLIC PUBLISHING DISABLED",
        "default": False,
        "controlled_by": "backend",
        "guardrails": list(GUARDRAILS),
        "boundary": BOUNDARY,
        "controlled_at": now,
        "timestamp": now,
    }


def auto_publish_status(config: Config, db: DBManager) -> Dict[str, Any]:
    """Status payload. Kept compatible with the original read-only surface."""
    enabled = effective_auto_publish(config, db)
    return {
        "enabled": enabled,
        "state": "ENABLED" if enabled else "DISABLED",
        "label": (
            "AUTONOMOUS PUBLIC PUBLISHING ENABLED"
            if enabled
            else "AUTONOMOUS PUBLIC PUBLISHING DISABLED"
        ),
        "default": False,
        "controlled_by": "backend",
        "guardrails": list(GUARDRAILS),
        "boundary": BOUNDARY,
        "controlled_at": _controlled_at(db),
        "timestamp": _now_iso(),
    }