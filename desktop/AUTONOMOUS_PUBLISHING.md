# Autopilot Desktop — M5/M6 Settings & Autonomous Publishing Kill-Switch

## Status of the autonomous public publishing switch

**Backend-controlled. Defaults to OFF.** Since M6 the backend exposes a
controlled mutation surface: an operator can enable autonomous public publishing
(fail-closed, after every prerequisite verifies) and can disable it at any time
as an emergency kill switch.

### Backend evidence

The engine bridge (`autopilot/autopilot/bridge/handlers.py`) registers three
methods for the autonomous publishing switch:

| Method | Handler | Kind |
| --- | --- | --- |
| `autonomy.publish_status` | `on_autonomy_publish_status` | **read-only query** |
| `autonomy.publish_enable` | `on_autonomy_publish_enable` | **controlled mutation (fail-closed)** |
| `autonomy.publish_disable` | `on_autonomy_publish_disable` | **controlled mutation (kill switch)** |

The switch is owned by `autopilot/autopilot/core/auto_publish.py`:

- `effective_auto_publish(config, db)` is the single authoritative resolver used
  by the status surface, `health.get`, `publishing.status`, and the final
  publish boundary. A persisted `config` row (`autonomy_auto_publish` in the
  `config` table) wins; before any explicit action it falls back to
  `config.autonomy_auto_publish` (env `AUTOPILOT_AUTONOMY_AUTO_PUBLISH`). Default
  is OFF.
- `enable_auto_publish(config, db)` validates every publishing prerequisite
  (publishing provider, supported target, YouTube authorisation, approval
  architecture, QA gate, artifact/checksum gate, idempotency, duplicate
  prevention, daily/channel limits, cooldown, kill switch OFF, valid
  configuration). If any prerequisite fails the switch is left untouched and the
  failure names the failed check(s). Enabling while already enabled is an
  idempotent no-op.
- `disable_auto_publish(config, db)` is the kill switch: immediate, persistent,
  idempotent.
- `publish_job(...)` gained an `autonomous` flag and re-checks the effective
  switch immediately before the `APPROVED → PUBLISHING` transition. For
  autonomous PUBLIC publications only, a disabled switch blocks the publish with
  `PublishStatus.BLOCKED_AUTONOMY_SWITCH` (`AUTONOMY_SWITCH_OFF`); the approval
  record stays intact and the job remains APPROVED. Manual/bridge publishes,
  non-public autonomous publishes, and dry runs are untouched.

### Desktop behaviour

- `SettingsScreen` renders the real backend status through
  `useAutonomyPublishStatusQuery()` and provides the controlled enable switch and
  the disable kill-switch action. Every action requires an explicit two-step
  confirmation, makes **no optimistic flip** (the UI only reflects the backend
  response + refetch), and surfaces failed prerequisites exactly as the backend
  reports them.
- `PublishingScreen` renders the same backend status as its `KillSwitchCard`,
  which additionally exposes an inline kill-switch (disable) action.
- The header badge in `App.tsx` derives solely from `useAutonomyPublishStatusQuery()`;
  the local `autonomyEnabled` UI-store flag has been removed. An unreachable or
  unknown backend is displayed as OFF (fail-closed display).

**M6 does not weaken the default.** Autonomous public publishing remains OFF by
default. Enabling requires a valid backend operation that verifies every
publishing prerequisite and fails closed otherwise; disabling is an immediate
persistent kill switch that stops any further autonomous public publication at
the final publish boundary.

Covered by `autopilot/tests/test_bridge_m5.py` and the M4/M5 bridge suites.