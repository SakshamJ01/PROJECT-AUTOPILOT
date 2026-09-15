"""Tests for YouTubeAnalyticsProvider."""
import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from autopilot.providers.youtube_analytics import YouTubeAnalyticsProvider, parse_iso8601_duration
from autopilot.core.contracts import MetricType, PerformanceWindow
from autopilot.core.config import Config


def test_parse_iso8601_duration():
    assert parse_iso8601_duration("PT1M15S") == 75.0
    assert parse_iso8601_duration("PT45S") == 45.0
    assert parse_iso8601_duration("PT1H2M10S") == 3730.0
    assert parse_iso8601_duration("PT2H") == 7200.0
    assert parse_iso8601_duration("") == 0.0
    assert parse_iso8601_duration("INVALID") == 0.0


def test_youtube_analytics_mocked_http_success():
    def mock_http(url, headers):
        return {
            "items": [
                {
                    "id": "yt-test-vid",
                    "snippet": {
                        "title": "Quantum Physics Explained",
                        "publishedAt": "2026-09-10T10:00:00Z",
                    },
                    "statistics": {
                        "viewCount": "12500",
                        "likeCount": "980",
                        "commentCount": "115",
                    },
                    "contentDetails": {
                        "duration": "PT42S",
                    },
                }
            ]
        }

    cfg = Config()
    provider = YouTubeAnalyticsProvider(config=cfg, http_client=mock_http)
    snap = provider.fetch_snapshot(remote_id="yt-test-vid", window="lifetime")

    assert snap.remote_id == "yt-test-vid"
    assert snap.platform == "youtube"
    assert snap.is_synthetic is False
    assert snap.metrics["views"].normalized_value == 12500.0
    assert snap.metrics["likes"].normalized_value == 980.0
    assert snap.metrics["comments"].normalized_value == 115.0
    assert snap.metrics["video_duration_seconds"].normalized_value == 42.0
    assert snap.metrics["views"].metric_type == MetricType.MEASURED
    assert snap.provenance.provider == "youtube"
    assert snap.metadata["title"] == "Quantum Physics Explained"


def test_youtube_analytics_health_unconfigured(tmp_path, monkeypatch):
    cfg = Config(youtube_access_token=None, youtube_token_path=None)
    # Isolate from real credentials on disk by redirecting both search paths
    monkeypatch.setattr(cfg, "project_root", tmp_path)
    cfg.artifacts_dir = tmp_path / "artifacts"  # .parent == tmp_path (no credentials/)
    provider = YouTubeAnalyticsProvider(config=cfg)
    h = provider.health_check()
    assert h.healthy is False
    assert "not configured" in h.error


def test_youtube_analytics_missing_video_raises():
    def mock_http_empty(url, headers):
        return {"items": []}

    provider = YouTubeAnalyticsProvider(http_client=mock_http_empty)
    with pytest.raises(ValueError, match="Video not found on YouTube"):
        provider.fetch_snapshot(remote_id="nonexistent-vid")


def test_youtube_analytics_redacts_secrets_in_errors(tmp_path, monkeypatch):
    cfg = Config(youtube_access_token=None, youtube_token_path=None)
    # Isolate from real credentials on disk so no token is resolved
    monkeypatch.setattr(cfg, "project_root", tmp_path)
    cfg.artifacts_dir = tmp_path / "artifacts"  # .parent == tmp_path (no credentials/)
    provider = YouTubeAnalyticsProvider(config=cfg)

    # Without any token and without mock client, fetch raises with safe message
    with pytest.raises(ValueError, match="YouTube API credentials not configured"):
        provider.fetch_snapshot(remote_id="any-id")


# ---------------------------------------------------------------------------
# Token-resolution tests — verify the shared OAuth path is now used
# ---------------------------------------------------------------------------

def test_analytics_resolves_default_token_path(tmp_path, monkeypatch):
    """Analytics finds credentials/youtube_token.json without YOUTUBE_TOKEN_PATH set."""
    token_file = tmp_path / "credentials" / "youtube_token.json"
    token_file.parent.mkdir(parents=True)
    future_expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    scopes = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
        "https://www.googleapis.com/auth/youtube.readonly",
    ]
    token_file.write_text(
        json.dumps({"access_token": "default_path_token", "expiry": future_expiry, "scopes": scopes}),
        encoding="utf-8",
    )

    # Point BOTH project_root AND artifacts_dir at tmp_path so only tmp token is found
    cfg = Config(youtube_access_token=None, youtube_token_path=None)
    monkeypatch.setattr(cfg, "project_root", tmp_path)
    cfg.artifacts_dir = tmp_path / "artifacts"  # .parent == tmp_path

    provider = YouTubeAnalyticsProvider(config=cfg)
    token = provider._resolve_token()
    assert token == "default_path_token"


def test_analytics_resolves_explicit_youtube_token_path(tmp_path):
    """Analytics honours YOUTUBE_TOKEN_PATH when explicitly set."""
    token_file = tmp_path / "explicit_token.json"
    future_expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    scopes = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
        "https://www.googleapis.com/auth/youtube.readonly",
    ]
    token_file.write_text(
        json.dumps({"access_token": "explicit_path_token", "expiry": future_expiry, "scopes": scopes}),
        encoding="utf-8",
    )

    cfg = Config(youtube_access_token=None, youtube_token_path=token_file)
    provider = YouTubeAnalyticsProvider(config=cfg)
    token = provider._resolve_token()
    assert token == "explicit_path_token"


def test_analytics_refreshes_expired_token(tmp_path):
    """Analytics delegates to load_and_refresh_youtube_credentials for expired tokens."""
    token_file = tmp_path / "youtube_token.json"
    past_expiry = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    token_file.write_text(
        json.dumps({
            "access_token": "stale_token",
            "refresh_token": "rt_abc123",
            "expiry": past_expiry,
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "cid",
            "client_secret": "csec",
        }),
        encoding="utf-8",
    )

    cfg = Config(youtube_access_token=None, youtube_token_path=token_file)
    provider = YouTubeAnalyticsProvider(config=cfg)

    with patch(
        "autopilot.providers.youtube_oauth.load_and_refresh_youtube_credentials",
        return_value=("refreshed_token", None),
    ) as mock_refresh:
        token = provider._resolve_token()

    assert token == "refreshed_token"
    mock_refresh.assert_called_once()


def test_analytics_honors_access_token_precedence(tmp_path):
    """YOUTUBE_ACCESS_TOKEN overrides token file."""
    # Even if a token file exists, the direct token wins
    token_file = tmp_path / "youtube_token.json"
    token_file.write_text(json.dumps({"access_token": "file_token"}), encoding="utf-8")

    cfg = Config(youtube_access_token="direct_env_token", youtube_token_path=token_file)
    provider = YouTubeAnalyticsProvider(config=cfg)
    assert provider._resolve_token() == "direct_env_token"


def test_analytics_missing_credentials_fails_closed(tmp_path, monkeypatch):
    """Returns None and raises when no credentials are available."""
    cfg = Config(youtube_access_token=None, youtube_token_path=None)
    # Isolate both search paths from real credentials on disk
    monkeypatch.setattr(cfg, "project_root", tmp_path)
    cfg.artifacts_dir = tmp_path / "artifacts"  # .parent == tmp_path (no credentials/)

    provider = YouTubeAnalyticsProvider(config=cfg)
    assert provider._resolve_token() is None

    # fetch_snapshot should also raise
    with pytest.raises(ValueError, match="YouTube API credentials not configured"):
        provider.fetch_snapshot(remote_id="any-id")
