"""Unit tests for Milestone 9 Autonomy CLI commands."""
import pytest
import io
import json
from unittest.mock import patch

from autopilot.cli.main import (
    run_autonomy_run,
    run_autonomy_proposals,
    run_autonomy_show,
    run_autonomy_approve,
    run_autonomy_reject,
    run_autonomy_policy,
    run_autonomy_strategy,
)


def parse_cli_json(output: str):
    """Filter out StructuredLogger json lines and parse the remaining CLI output."""
    content_lines = []
    for line in output.strip().splitlines():
        try:
            d = json.loads(line)
            if isinstance(d, dict) and "stage" in d and "event" in d:
                continue
        except Exception:
            pass
        content_lines.append(line)
    return json.loads("\n".join(content_lines))


def test_cli_autonomy_policy(capsys):
    ret = run_autonomy_policy(output_json=False)
    assert ret == 0
    captured = capsys.readouterr().out
    assert "=== AUTONOMY POLICY CONFIGURATION ===" in captured
    assert "Max Ideas Per Cycle" in captured

    ret_json = run_autonomy_policy(output_json=True)
    assert ret_json == 0
    captured_json = capsys.readouterr().out
    data = parse_cli_json(captured_json)
    assert "policy_id" in data


def test_cli_autonomy_strategy(capsys):
    ret = run_autonomy_strategy(output_json=False)
    assert ret == 0
    captured = capsys.readouterr().out
    assert "=== AUTONOMY STRATEGY VERSIONS ===" in captured
    assert "strat-v1" in captured

    ret_json = run_autonomy_strategy(output_json=True)
    assert ret_json == 0
    captured_json = capsys.readouterr().out
    data = parse_cli_json(captured_json)
    assert "active_strategy" in data
    assert data["active_strategy"]["version_id"] == "strat-v1"


def test_cli_autonomy_run_dry_run(capsys):
    ret = run_autonomy_run(level=0, dry_run=True, output_json=False)
    assert ret == 0
    captured = capsys.readouterr().out
    assert "LEVEL_0_MANUAL" in captured

    ret_l2 = run_autonomy_run(level=2, dry_run=True, limit=2, output_json=True)
    assert ret_l2 == 0
    captured_json = capsys.readouterr().out
    data = parse_cli_json(captured_json)
    assert data["autonomy_level"] == 2
    assert data["dry_run"] is True


def test_cli_autonomy_proposals_and_show(capsys):
    # Run a real proposal cycle
    ret_run = run_autonomy_run(level=2, dry_run=False, limit=2, output_json=False)
    assert ret_run == 0
    capsys.readouterr()  # Drain run output

    ret_prop = run_autonomy_proposals(output_json=True)
    assert ret_prop == 0
    captured_json = capsys.readouterr().out
    proposals = parse_cli_json(captured_json)
    assert len(proposals) > 0

    target_prop = proposals[0]
    pid = target_prop["proposal_id"]

    # Show proposal
    ret_show = run_autonomy_show(proposal_id=pid, output_json=False)
    assert ret_show == 0
    captured_show = capsys.readouterr().out
    assert f"=== IDEA PROPOSAL [{pid}] ===" in captured_show

    # Approve proposal
    ret_app = run_autonomy_approve(proposal_id=pid, output_json=False)
    assert ret_app == 0
    captured_app = capsys.readouterr().out
    assert "APPROVED successfully" in captured_app

    # Reject another proposal if present
    if len(proposals) > 1:
        pid2 = proposals[1]["proposal_id"]
        ret_rej = run_autonomy_reject(proposal_id=pid2, reason="Test rejection", output_json=False)
        assert ret_rej == 0
        captured_rej = capsys.readouterr().out
        assert "REJECTED" in captured_rej
