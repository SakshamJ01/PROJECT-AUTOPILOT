"""Phase 3 Tests — Final CLI UX & Command Flows."""
import pytest
from unittest.mock import patch, MagicMock
from autopilot.cli.main import main, build_parser


def test_cli_parser_phase3_commands():
    """Verify build_parser supports all Phase 3 syntax variations."""
    parser = build_parser()

    # 1. Positional publish: autopilot publish job-123
    args1 = parser.parse_args(["publish", "job-123"])
    assert args1.positional_job == "job-123"

    # 2. Scheduled publish: autopilot publish job-123 --schedule 2026-10-01T18:00:00Z
    args2 = parser.parse_args(["publish", "job-123", "--schedule", "2026-10-01T18:00:00Z"])
    assert args2.schedule == "2026-10-01T18:00:00Z"

    # 3. Top-level inspect: autopilot inspect job-123
    args3 = parser.parse_args(["inspect", "job-123"])
    assert args3.command == "inspect"
    assert args3.job == "job-123"

    # 4. Analytics with channel: autopilot analytics --channel tech_shorts
    args4 = parser.parse_args(["analytics", "--channel", "tech_shorts"])
    assert args4.command == "analytics"
    assert args4.channel == "tech_shorts"

    # 5. Autonomy with channel: autopilot autonomy --channel tech_shorts
    args5 = parser.parse_args(["autonomy", "--channel", "tech_shorts"])
    assert args5.command == "autonomy"
    assert args5.channel == "tech_shorts"


def test_cli_publish_output_formatting(capsys, monkeypatch):
    """Verify run_publish outputs the required Phase 3 fields: JOB, STATUS, VIDEO, QA, TARGET PLATFORM, VISIBILITY, SCHEDULE, PUBLICATION STATUS, REMOTE URL."""
    from autopilot.cli import main as cli_main

    # Mock PublishingEngine
    with patch("autopilot.core.publisher.PublishingEngine.publish_job") as mock_pub:
        mock_res = MagicMock()
        mock_res.success = True
        mock_res.status.value = "PUBLISHED"
        mock_res.receipt.visibility.value = "private"
        mock_res.receipt.scheduled_time = None
        mock_res.receipt.remote_video_id = "yt-abc"
        mock_res.receipt.remote_url = "https://youtube.com/watch?v=yt-abc"
        mock_res.receipt.idempotency_key = "1234567890abcdef1234567890abcdef"
        mock_res.dry_run_preview = {"media_path": "artifacts/final.mp4"}
        mock_res.error = None
        mock_pub.return_value = mock_res

        monkeypatch.setattr("sys.argv", ["autopilot", "publish", "job-xyz", "--platform", "youtube"])
        ret = cli_main.main()
        assert ret == 0

        out = capsys.readouterr().out
        assert "JOB:                 job-xyz" in out
        assert "STATUS:              SUCCESS" in out
        assert "VIDEO:" in out
        assert "QA:" in out
        assert "TARGET PLATFORM:     youtube" in out
        assert "VISIBILITY:          private" in out
        assert "SCHEDULE:" in out
        assert "PUBLICATION STATUS:  PUBLISHED" in out
        assert "REMOTE URL:          https://youtube.com/watch?v=yt-abc" in out
