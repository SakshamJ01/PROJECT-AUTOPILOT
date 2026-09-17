"""PROJECT-AUTOPILOT desktop bridge — thin stdio JSON-RPC 2.0 control surface.

The bridge is a child process of the Tauri desktop shell. It exposes read-only
snapshots and safe controls over the EXISTING Python backend. It introduces no
business logic, no HTTP, no TCP, and never returns credentials.
"""

BRIDGE_VERSION = "0.1.0"

__all__ = ["BRIDGE_VERSION"]