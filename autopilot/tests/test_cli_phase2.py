"""Phase 2 CLI and Stale Profile Invalidation Tests.
Covers:
- autopilot run --topic ... --channel ...
- autopilot batch --topics ... --channel ...
- production policy options
- Stale profile / config invalidation
"""
import sys
import json
import subprocess
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from autopilot.cli.main import build_parser
from autopilot.core.channel import ChannelManager
from autopilot.core.contracts import ChannelProfile, ProductionPolicyTier
from autopilot.core.pipeline import PipelineOrchestrator


def test_cli_parser_phase2_commands():
    """Verify run, batch --topics, --channel, and --policy argument parsing."""
    parser = build_parser()

    # 1. Test 'run' subcommand
    args = parser.parse_args(["run", "--topic", "James Webb Space Telescope", "--channel", "science_shorts", "--policy", "quality_first"])
    assert args.command == "run"
    assert args.topic == "James Webb Space Telescope"
    assert args.channel == "science_shorts"
    assert args.policy == "quality_first"

    # 2. Test 'batch' subcommand with --topics
    args_batch = parser.parse_args(["batch", "--topics", "topics.txt", "--channel", "history_shorts"])
    assert args_batch.command == "batch"
    assert args_batch.topics == "topics.txt"
    assert args_batch.channel == "history_shorts"

    # 3. Test 'produce' with channel and policy
    args_prod = parser.parse_args(["produce", "--topic", "AI", "--channel", "tech_shorts", "--policy", "local_only"])
    assert args_prod.channel == "tech_shorts"
    assert args_prod.policy == "local_only"


def test_stale_profile_invalidation(tmp_path):
    """Verify that changing a channel's profile (e.g. tone or target duration) invalidates cached script."""
    from autopilot.core.config import Config
    from autopilot.db.manager import DBManager

    db_path = tmp_path / "test_stale.db"
    db = DBManager(db_path)
    db.init_schema()
    cfg = Config(artifacts_dir=tmp_path, db_path=db_path)
    orch = PipelineOrchestrator(config=cfg, db=db)

    # 1. Run with science_shorts
    res1 = orch.run_pipeline(
        job_id="job-stale-01",
        topic="Supernovae",
        channel_id="science_shorts",
        production_engine="ffmpeg",
        tts_provider="none",
        asset_provider="local",
    )
    assert res1["status"] in ("APPROVED", "QA", "RENDERED", "success")

    script_path = tmp_path / "jobs" / "job-stale-01" / "script" / "script.json"
    assert script_path.exists()
    s1 = json.loads(script_path.read_text(encoding="utf-8"))
    assert s1.get("generation_metadata", {}).get("channel_id") == "science_shorts"

    # Verify that if we run with history_shorts on the same job, the script is invalidated and regenerated
    res2 = orch.run_pipeline(
        job_id="job-stale-01",
        topic="Supernovae",
        channel_id="history_shorts",
        production_engine="ffmpeg",
        tts_provider="none",
        asset_provider="local",
    )
    assert res2["status"] in ("APPROVED", "QA", "RENDERED", "success")

    s2 = json.loads(script_path.read_text(encoding="utf-8"))
    assert s2.get("generation_metadata", {}).get("channel_id") == "history_shorts"
