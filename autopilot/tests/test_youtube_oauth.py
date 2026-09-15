"""Tests for YouTube OAuth flow, token persistence, and automatic token refresh."""
import json
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from autopilot.core.config import Config, CONFIG
from autopilot.providers.youtube_oauth import (
    save_credentials_json,
    load_and_refresh_youtube_credentials,
    find_default_client_secrets_path,
    find_default_token_path,
    run_youtube_oauth_flow,
    YOUTUBE_UPLOAD_SCOPE,
    YOUTUBE_ANALYTICS_SCOPE,
    YOUTUBE_READONLY_SCOPE,
    DEFAULT_SCOPES,
)
from autopilot.providers.youtube_publisher import YouTubePublisher, redact_secrets
from autopilot.cli.main import run_youtube_auth


def test_save_and_parse_credentials_json(tmp_path):
    """Test saving credentials to JSON and parsing them back."""
    token_file = tmp_path / "credentials" / "youtube_token.json"
    future_expiry = datetime.now(timezone.utc) + timedelta(hours=1)

    save_credentials_json(
        token_path=token_file,
        access_token="ya29.sample-access-token-123",
        refresh_token="1//0sample-refresh-token-456",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="sample-client-id.apps.googleusercontent.com",
        client_secret="sample-client-secret-xyz",
        scopes=[YOUTUBE_UPLOAD_SCOPE],
        expiry=future_expiry,
    )

    assert token_file.exists()
    data = json.loads(token_file.read_text(encoding="utf-8"))
    assert data["access_token"] == "ya29.sample-access-token-123"
    assert data["token"] == "ya29.sample-access-token-123"
    assert data["refresh_token"] == "1//0sample-refresh-token-456"
    assert data["client_id"] == "sample-client-id.apps.googleusercontent.com"
    assert data["client_secret"] == "sample-client-secret-xyz"
    assert data["scopes"] == [YOUTUBE_UPLOAD_SCOPE]
    assert "expiry" in data


def test_valid_token_reuse_without_refresh(tmp_path):
    """A valid, non-expired access token must be returned immediately without calling refresh."""
    token_file = tmp_path / "youtube_token.json"
    future_expiry = datetime.now(timezone.utc) + timedelta(hours=1)

    save_credentials_json(
        token_path=token_file,
        access_token="valid-active-token-999",
        refresh_token="valid-refresh-token-999",
        expiry=future_expiry,
    )

    # Transport that would fail if called
    failing_transport = MagicMock(side_effect=RuntimeError("Refresh should not be called!"))

    token, err = load_and_refresh_youtube_credentials(
        token_path=token_file,
        transport=failing_transport,
    )

    assert token == "valid-active-token-999"
    assert err is None
    failing_transport.assert_not_called()


def test_expired_access_token_refreshes_and_persists(tmp_path):
    """An expired access token is refreshed using refresh_token and saved back to disk."""
    token_file = tmp_path / "youtube_token.json"
    past_expiry = datetime.now(timezone.utc) - timedelta(hours=2)

    save_credentials_json(
        token_path=token_file,
        access_token="expired-old-token-000",
        refresh_token="1//0valid-refresh-token-111",
        client_id="my-client-id",
        client_secret="my-client-secret",
        expiry=past_expiry,
    )

    # Mock google auth Credentials.refresh to simulate token renewal
    def mock_refresh(self_creds, request):
        self_creds.token = "refreshed-fresh-access-token-222"
        self_creds.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)

    with patch("google.oauth2.credentials.Credentials.refresh", new=mock_refresh):
        token, err = load_and_refresh_youtube_credentials(token_path=token_file)

        assert token == "refreshed-fresh-access-token-222"
        assert err is None

        # Verify persisted file on disk contains refreshed token
        updated = json.loads(token_file.read_text(encoding="utf-8"))
        assert updated["access_token"] == "refreshed-fresh-access-token-222"
        assert updated["refresh_token"] == "1//0valid-refresh-token-111"


def test_refresh_failure_fails_closed(tmp_path):
    """When refresh fails, it fails closed returning clear AUTH_REFRESH error."""
    import google.auth.exceptions

    token_file = tmp_path / "youtube_token.json"
    past_expiry = datetime.now(timezone.utc) - timedelta(hours=2)

    save_credentials_json(
        token_path=token_file,
        access_token="expired-token",
        refresh_token="revoked-refresh-token",
        expiry=past_expiry,
    )

    with patch("google.oauth2.credentials.Credentials.refresh") as mock_refresh:
        mock_refresh.side_effect = google.auth.exceptions.RefreshError("Token has been expired or revoked.")

        token, err = load_and_refresh_youtube_credentials(token_path=token_file)

        assert token is None
        assert "AUTH_REFRESH_REJECTED" in err
        assert "revoked" in err


def test_youtube_publisher_resolves_access_token_env_precedence(tmp_path, monkeypatch):
    """YOUTUBE_ACCESS_TOKEN takes precedence over token file."""
    token_file = tmp_path / "youtube_token.json"
    save_credentials_json(
        token_path=token_file,
        access_token="file-access-token",
        refresh_token="file-refresh-token",
    )

    cfg = Config()
    cfg.youtube_access_token = "env-override-access-token"
    cfg.youtube_token_path = token_file

    publisher = YouTubePublisher(config=cfg)
    resolved = publisher._resolve_access_token()
    assert resolved == "env-override-access-token"


def test_youtube_publisher_resolves_from_token_path(tmp_path):
    """When no env token is set, publisher uses youtube_token_path."""
    token_file = tmp_path / "youtube_token.json"
    save_credentials_json(
        token_path=token_file,
        access_token="file-only-token",
        refresh_token="file-refresh-token",
    )

    cfg = Config()
    cfg.youtube_access_token = None
    cfg.youtube_token_path = token_file

    publisher = YouTubePublisher(config=cfg)
    resolved = publisher._resolve_access_token()
    assert resolved == "file-only-token"


def test_secrets_redaction_in_oauth_context():
    """Verify secrets are masked in logs and errors."""
    raw = "Error refreshing client_secret=very_secret_key_123 with refresh_token=1//0xyz"
    cleaned = redact_secrets(raw)
    assert "very_secret_key_123" not in cleaned
    assert "1//0xyz" not in cleaned
    assert "client_secret=[REDACTED]" in cleaned
    assert "refresh_token=[REDACTED]" in cleaned


def test_cli_youtube_auth_missing_secrets_error(tmp_path, capsys):
    """CLI youtube-auth fails gracefully if client_secret.json is not found."""
    nonexistent = tmp_path / "nonexistent_secrets.json"
    exit_code = run_youtube_auth(secrets_path=str(nonexistent), no_browser=True)
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Client secrets file not found" in captured.out


def test_cli_youtube_auth_mocked_success(tmp_path, capsys):
    """CLI youtube-auth completes flow and writes credentials file."""
    secrets_file = tmp_path / "client_secret.json"
    secrets_file.write_text(json.dumps({
        "installed": {
            "client_id": "mock-client-id.apps.googleusercontent.com",
            "client_secret": "mock-client-secret-123",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }), encoding="utf-8")

    token_file = tmp_path / "youtube_token.json"

    mock_creds = MagicMock()
    mock_creds.token = "mock-cli-access-token"
    mock_creds.refresh_token = "mock-cli-refresh-token"
    mock_creds.token_uri = "https://oauth2.googleapis.com/token"
    mock_creds.client_id = "mock-client-id.apps.googleusercontent.com"
    mock_creds.client_secret = "mock-client-secret-123"
    mock_creds.scopes = DEFAULT_SCOPES
    mock_creds.expiry = datetime.now(timezone.utc) + timedelta(hours=1)

    with patch("google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file") as mock_flow_init:
        mock_flow = MagicMock()
        mock_flow.run_local_server.return_value = mock_creds
        mock_flow_init.return_value = mock_flow

        exit_code = run_youtube_auth(
            secrets_path=str(secrets_file),
            token_path=str(token_file),
            no_browser=True,
            port=8080,
            output_json=True,
        )

        assert exit_code == 0
        assert token_file.exists()
        saved = json.loads(token_file.read_text(encoding="utf-8"))
        assert saved["access_token"] == "mock-cli-access-token"
        assert saved["refresh_token"] == "mock-cli-refresh-token"
        assert saved["scopes"] == DEFAULT_SCOPES


def test_oauth_scope_list_contains_all_required_scopes():
    """DEFAULT_SCOPES must contain publishing, analytics, and read-only scopes."""
    assert YOUTUBE_UPLOAD_SCOPE in DEFAULT_SCOPES
    assert YOUTUBE_ANALYTICS_SCOPE in DEFAULT_SCOPES
    assert YOUTUBE_READONLY_SCOPE in DEFAULT_SCOPES
    assert len(DEFAULT_SCOPES) >= 3


def test_token_lacking_readonly_scope_rejected(tmp_path):
    """Token lacking youtube.readonly scope is rejected with AUTH_SCOPE_INSUFFICIENT."""
    token_file = tmp_path / "youtube_token.json"
    future_expiry = datetime.now(timezone.utc) + timedelta(hours=1)

    save_credentials_json(
        token_path=token_file,
        access_token="partial-scope-token",
        refresh_token="legacy-refresh-token",
        scopes=[YOUTUBE_UPLOAD_SCOPE, YOUTUBE_ANALYTICS_SCOPE],
        expiry=future_expiry,
    )

    token, err = load_and_refresh_youtube_credentials(token_path=token_file)
    assert token is None
    assert err == "AUTH_SCOPE_INSUFFICIENT"


def test_token_with_all_scopes_accepted(tmp_path):
    """Token with all three required scopes is accepted."""
    token_file = tmp_path / "youtube_token.json"
    future_expiry = datetime.now(timezone.utc) + timedelta(hours=1)

    save_credentials_json(
        token_path=token_file,
        access_token="triple-scope-token",
        refresh_token="triple-refresh-token",
        scopes=[YOUTUBE_UPLOAD_SCOPE, YOUTUBE_ANALYTICS_SCOPE, YOUTUBE_READONLY_SCOPE],
        expiry=future_expiry,
    )

    token, err = load_and_refresh_youtube_credentials(token_path=token_file)
    assert token == "triple-scope-token"
    assert err is None


def test_refresh_preserves_all_scopes(tmp_path):
    """Token refresh preserves all three required scopes in persisted file."""
    token_file = tmp_path / "youtube_token.json"
    past_expiry = datetime.now(timezone.utc) - timedelta(hours=2)

    save_credentials_json(
        token_path=token_file,
        access_token="expired-triple-token",
        refresh_token="valid-refresh-token",
        scopes=[YOUTUBE_UPLOAD_SCOPE, YOUTUBE_ANALYTICS_SCOPE, YOUTUBE_READONLY_SCOPE],
        expiry=past_expiry,
    )

    def mock_refresh(self_creds, request):
        self_creds.token = "refreshed-triple-token"
        self_creds.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)

    with patch("google.oauth2.credentials.Credentials.refresh", new=mock_refresh):
        token, err = load_and_refresh_youtube_credentials(token_path=token_file)
        assert token == "refreshed-triple-token"
        assert err is None

        saved = json.loads(token_file.read_text(encoding="utf-8"))
        assert saved["access_token"] == "refreshed-triple-token"
        assert YOUTUBE_UPLOAD_SCOPE in saved["scopes"]
        assert YOUTUBE_ANALYTICS_SCOPE in saved["scopes"]
        assert YOUTUBE_READONLY_SCOPE in saved["scopes"]


