"""Executable entry point: ``python -m autopilot.bridge``.

Environment overrides:
  AUTOPILOT_DB_PATH       — SQLite database to serve (default: engine default)
  AUTOPILOT_ARTIFACTS_DIR — artifacts directory (default: engine default)
"""

from __future__ import annotations

import os


def main() -> int:
    from autopilot.bridge.handlers import BridgeHandlers
    from autopilot.bridge.server import BridgeServer
    from autopilot.core.config import Config
    from autopilot.db.manager import DBManager

    config = Config()
    db = DBManager(config.db_path)
    db.init_schema()

    server = BridgeServer(BridgeHandlers(config=config, db=db))
    return server.serve()


if __name__ == "__main__":
    raise SystemExit(main())