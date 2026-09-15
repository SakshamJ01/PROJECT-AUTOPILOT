import json
from pathlib import Path
from unittest.mock import patch
import pytest

from autopilot.cli.main import (
    run_channel_list,
    run_channel_show,
    run_channel_create,
    run_channel_enable,
    run_channel_disable,
    run_channel_validate,
    run_channel_history,
    run_channel_compare,
)
from autopilot.core.channel import ChannelManager
from autopilot.db.manager import DatabaseManager
from tests.test_channel_profiles import create_sample_profile


def test_cli_channel_list_empty(tmp_path: Path, capsys):
    db = DatabaseManager(tmp_path / "test.db")
    db.init_schema()

    with patch("autopilot.core.channel.DatabaseManager", return_value=db):
        code = run_channel_list(output_json=False)
        assert code == 0
        captured = capsys.readouterr().out
        assert "No channel profiles configured." in captured

        code_json = run_channel_list(output_json=True)
        assert code_json == 0
        captured_json = capsys.readouterr().out
        assert json.loads(captured_json) == []


def test_cli_channel_create_and_show(tmp_path: Path, capsys):
    db = DatabaseManager(tmp_path / "test.db")
    db.init_schema()

    profile = create_sample_profile("chan-cli", "CLI Channel")
    profile_file = tmp_path / "profile.json"
    profile_file.write_text(profile.model_dump_json(indent=2), encoding="utf-8")

    with patch("autopilot.core.channel.DatabaseManager", return_value=db):
        # Create
        code = run_channel_create(str(profile_file), output_json=False)
        assert code == 0
        out = capsys.readouterr().out
        assert "created successfully (version v1)" in out

        # Show
        code_show = run_channel_show("chan-cli", output_json=False)
        assert code_show == 0
        show_out = capsys.readouterr().out
        assert "=== CHANNEL PROFILE: CLI Channel [chan-cli] ===" in show_out
        assert "Status:             active" in show_out

        # Show non-existent
        code_missing = run_channel_show("non-existent-xyz", output_json=False)
        assert code_missing == 1


def test_cli_channel_enable_disable(tmp_path: Path, capsys):
    db = DatabaseManager(tmp_path / "test.db")
    db.init_schema()
    mgr = ChannelManager(db)
    profile = create_sample_profile("chan-toggle", "Toggle Channel")
    mgr.save_profile(profile)

    with patch("autopilot.core.channel.DatabaseManager", return_value=db):
        # Disable
        code = run_channel_disable("chan-toggle")
        assert code == 0
        out = capsys.readouterr().out
        assert "disabled (DISABLED)" in out

        # Enable
        code = run_channel_enable("chan-toggle")
        assert code == 0
        out = capsys.readouterr().out
        assert "enabled (ACTIVE)" in out


def test_cli_channel_validate(tmp_path: Path, capsys):
    db = DatabaseManager(tmp_path / "test.db")
    db.init_schema()
    mgr = ChannelManager(db)
    profile = create_sample_profile("chan-valid", "Valid Channel")
    mgr.save_profile(profile)

    with patch("autopilot.core.channel.DatabaseManager", return_value=db):
        code = run_channel_validate(channel_id="chan-valid", output_json=False)
        assert code == 0
        out = capsys.readouterr().out
        assert "Status: VALID" in out


def test_cli_channel_history(tmp_path: Path, capsys):
    db = DatabaseManager(tmp_path / "test.db")
    db.init_schema()
    mgr = ChannelManager(db)
    profile = create_sample_profile("chan-hist", "History Channel")
    mgr.save_profile(profile, comment="Initial setup")
    profile.persona.tone = "dramatic"
    mgr.save_profile(profile, comment="Switched tone to dramatic")

    with patch("autopilot.core.channel.DatabaseManager", return_value=db):
        code = run_channel_history("chan-hist", output_json=False)
        assert code == 0
        out = capsys.readouterr().out
        assert "=== CHANNEL VERSION HISTORY: chan-hist (2 versions) ===" in out
        assert "v1" in out
        assert "v2" in out


def test_cli_channel_compare(tmp_path: Path, capsys):
    db = DatabaseManager(tmp_path / "test.db")
    db.init_schema()
    mgr = ChannelManager(db)
    p1 = create_sample_profile("ch-1", "Channel 1")
    p2 = create_sample_profile("ch-2", "Channel 2")
    mgr.save_profile(p1)
    mgr.save_profile(p2)

    with patch("autopilot.core.channel.DatabaseManager", return_value=db):
        code = run_channel_compare(output_json=False)
        assert code == 0
        out = capsys.readouterr().out
        assert "=== CHANNEL PERFORMANCE COMPARISON (2 channels) ===" in out
        assert "ch-1" in out
        assert "ch-2" in out
