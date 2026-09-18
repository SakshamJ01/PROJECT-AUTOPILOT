# Autopilot Desktop — M5 Settings & Autonomous Publishing Kill-Switch

## Status of the autonomous public publishing switch

**Read-only. The backend is authoritative and exposes no mutation surface.**

### Backend evidence

The engine bridge (`autopilot/autopilot/bridge/handlers.py`) registers exactly
one method for the autonomous publishing switch:

| Method | Handler | Kind |
| --- | --- | --- |
| `autonomy.publish_status` | `on_autonomy_publish_status` | **read-only query** |

That handler (handlers.py:1513) returns `enabled` / `state` / `label` /
`guardrails` / `boundary` derived from `config.autonomy_auto_publish` and states
in its own docstring:

> "The backend is authoritative. There is no desktop mutation path: the switch
> reflects the backend configuration only, and defaults to OFF."

There is **no** `autonomy.publish_enable`, `autonomy.publish_disable`,
`autonomy.publish_status_set`, or kill-switch mutation method anywhere in
`BridgeHandlers.METHOD_NAMES`. The only `disable` mutations on the bridge are
`scheduler.disable` / `scheduler.delete`, which act on schedules, not on the
publishing switch.

Related desktop entry points are likewise hard-guarded:

- `on_production_start` hardcodes `auto_publish = False`
  ("M2 never allows publishing from desktop") and always calls the worker with
  `force_auto_publish=False`.
- The switch can only be flipped by editing the backend configuration
  (`AUTOPILOT_AUTONOMY_AUTO_PUBLISH`, see `autopilot/core/config.py`), never from
  the desktop.

This is asserted by the backend contract test
`autopilot/tests/test_bridge_m4.py::test_autonomy_publish_status_read_only`,
which fails if any `publish_status_set` / `publish_enable` method is ever added.

### Desktop behaviour

Because no safe mutation exists, the desktop performs **no** write against the
switch:

- `SettingsScreen` renders the real backend status through
  `useAutonomyPublishStatusQuery()` (`autonomy.publish_status`) and displays an
  explicit read-only banner. There is no enable/disable/kill-switch control.
- `PublishingScreen` renders the same read-only status as its `KillSwitchCard`.
- The header badge in `App.tsx` is a display-only indicator driven by the local
  UI store flag `autonomyEnabled` (defaults to `false`); it issues no backend
  call and cannot enable publishing.

**M5 does not enable autonomous public publishing.** Enabling it would first
require a backend mutation method (with its own guardrails and tests); none is
present, so none is wired up here.
