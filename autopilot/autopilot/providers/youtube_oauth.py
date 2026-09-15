"""YouTube OAuth 2.0 Installed App Flow and Token Manager.

Implements official Google OAuth 2.0 flow using google_auth_oauthlib.flow.InstalledAppFlow
with offline access_type to acquire both access_token and refresh_token.
Provides automatic token refresh and atomic credential persistence.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import google.auth.exceptions
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from autopilot.core.config import Config, CONFIG
from autopilot.providers.youtube_publisher import redact_secrets

logger = logging.getLogger("autopilot.providers.youtube_oauth")

YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
YOUTUBE_READONLY_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
DEFAULT_SCOPES = [YOUTUBE_UPLOAD_SCOPE, YOUTUBE_ANALYTICS_SCOPE, YOUTUBE_READONLY_SCOPE]
REQUIRED_SCOPES = {YOUTUBE_UPLOAD_SCOPE, YOUTUBE_ANALYTICS_SCOPE, YOUTUBE_READONLY_SCOPE}




def resolve_youtube_access_token(config: Optional[Config] = None, transport: Optional[Any] = None) -> Optional[str]:
    """Single source of truth for resolving YouTube OAuth access tokens.

    P0-04 fix: Shared resolver replacing duplicated logic in youtube_publisher and youtube_analytics.
    Checks: config/env direct token → token file via load_and_refresh_youtube_credentials → raw fallback.
    """
    cfg = config or CONFIG

    # 1. Direct access token in config / env (highest priority)
    if cfg.youtube_access_token:
        return cfg.youtube_access_token

    # 2. Token file path — explicit config or default fallback
    token_path = cfg.youtube_token_path
    if not token_path:
        default_path = cfg.get_artifacts_dir().parent / "credentials" / "youtube_token.json"
        if default_path.exists():
            token_path = default_path
        else:
            proj_path = cfg.project_root / "credentials" / "youtube_token.json"
            if proj_path.exists():
                token_path = proj_path

    if not token_path or not Path(token_path).exists():
        return None

    # 3. Resolve client secrets path (needed for refresh metadata)
    secrets_path = cfg.youtube_client_secrets_path
    if not secrets_path:
        secrets_path = find_default_client_secrets_path(cfg)

    # 4. Delegate to shared OAuth loader (handles expiry check + refresh + persistence)
    try:
        token, _ = load_and_refresh_youtube_credentials(
            token_path=Path(token_path),
            client_secrets_path=Path(secrets_path) if secrets_path else None,
            transport=transport,
        )
        return token
    except Exception:
        # Fallback: raw read (no refresh) if oauth module unavailable
        try:
            data = json.loads(Path(token_path).read_text(encoding="utf-8"))
            return data.get("access_token") or data.get("token")
        except Exception:
            return None


def find_default_client_secrets_path(config: Optional[Config] = None) -> Optional[Path]:
    """Locate client_secret.json in config or default credentials directory."""
    cfg = config or CONFIG
    if cfg.youtube_client_secrets_path and Path(cfg.youtube_client_secrets_path).exists():
        return Path(cfg.youtube_client_secrets_path)

    project_root = cfg.project_root
    candidates = [
        project_root / "credentials" / "client_secret.json",
        project_root / "credentials" / "client_secret.json.json",
        cfg.get_artifacts_dir().parent / "credentials" / "client_secret.json",
        cfg.get_artifacts_dir().parent / "credentials" / "client_secret.json.json",
    ]
    for cand in candidates:
        if cand.exists() and cand.is_file():
            return cand
    return None


def find_default_token_path(config: Optional[Config] = None) -> Path:
    """Resolve destination path for youtube_token.json."""
    cfg = config or CONFIG
    if cfg.youtube_token_path:
        return Path(cfg.youtube_token_path)
    return cfg.project_root / "credentials" / "youtube_token.json"


def save_credentials_json(
    token_path: Path,
    access_token: Optional[str],
    refresh_token: Optional[str],
    token_uri: Optional[str] = "https://oauth2.googleapis.com/token",
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    scopes: Optional[list[str]] = None,
    expiry: Optional[datetime] = None,
) -> None:
    """Atomically save OAuth token data to target JSON path."""
    token_path.parent.mkdir(parents=True, exist_ok=True)
    expiry_iso = expiry.isoformat() if expiry else None

    data: Dict[str, Any] = {
        "access_token": access_token,
        "token": access_token,
        "refresh_token": refresh_token,
        "token_uri": token_uri or "https://oauth2.googleapis.com/token",
        "client_id": client_id,
        "client_secret": client_secret,
        "scopes": scopes or DEFAULT_SCOPES,
        "expiry": expiry_iso,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    # Clean None values for compactness
    cleaned = {k: v for k, v in data.items() if v is not None}

    # Atomic write via temp file
    temp_file = token_path.parent / f".tmp_{token_path.name}"
    temp_file.write_text(json.dumps(cleaned, indent=2), encoding="utf-8")
    temp_file.replace(token_path)


def run_youtube_oauth_flow(
    secrets_path: Optional[Path | str] = None,
    token_path: Optional[Path | str] = None,
    open_browser: bool = True,
    port: int = 0,
    config: Optional[Config] = None,
) -> Path:
    """Execute official Google InstalledAppFlow OAuth authorization and persist tokens."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    resolved_secrets = Path(secrets_path) if secrets_path else find_default_client_secrets_path(config)
    if not resolved_secrets or not resolved_secrets.exists():
        raise FileNotFoundError(
            f"Client secrets file not found at '{resolved_secrets}'. "
            "Please configure YOUTUBE_CLIENT_SECRETS_PATH or place client_secret.json in credentials/"
        )

    resolved_token_path = Path(token_path) if token_path else find_default_token_path(config)

    flow = InstalledAppFlow.from_client_secrets_file(
        str(resolved_secrets),
        scopes=DEFAULT_SCOPES,
    )

    creds = flow.run_local_server(
        port=port,
        open_browser=open_browser,
        prompt="consent",
        access_type="offline",
    )

    save_credentials_json(
        token_path=resolved_token_path,
        access_token=creds.token,
        refresh_token=creds.refresh_token,
        token_uri=getattr(creds, "token_uri", "https://oauth2.googleapis.com/token"),
        client_id=getattr(creds, "client_id", None),
        client_secret=getattr(creds, "client_secret", None),
        scopes=getattr(creds, "scopes", DEFAULT_SCOPES),
        expiry=getattr(creds, "expiry", None),
    )

    return resolved_token_path


def load_and_refresh_youtube_credentials(
    token_path: Path,
    client_secrets_path: Optional[Path] = None,
    transport: Optional[Any] = None,
    required_scopes: Optional[list[str]] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Load credentials from token JSON, refreshing expired access tokens if refresh_token exists.
    
    Returns: (access_token_str, error_code_or_none)
    """
    if not token_path or not token_path.exists():
        return None, "AUTH_TOKEN_FILE_MISSING"

    try:
        data = json.loads(token_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error(f"Failed to parse token file: {redact_secrets(str(exc))}")
        return None, "AUTH_TOKEN_PARSE_ERROR"

    access_token = data.get("access_token") or data.get("token")
    refresh_token = data.get("refresh_token")
    token_uri = data.get("token_uri") or "https://oauth2.googleapis.com/token"
    client_id = data.get("client_id")
    client_secret = data.get("client_secret")
    scopes = data.get("scopes")

    req_scopes = set(required_scopes) if required_scopes is not None else REQUIRED_SCOPES
    token_scopes = set(scopes) if isinstance(scopes, list) else set()

    if not req_scopes.issubset(token_scopes):
        missing = req_scopes - token_scopes
        logger.error(f"YouTube OAuth token at '{token_path}' lacks required scope(s): {missing}. Reauthorization required.")
        return None, "AUTH_SCOPE_INSUFFICIENT"


    # If client_id/client_secret not in token file, attempt to supplement from client_secret.json
    if (not client_id or not client_secret) and client_secrets_path and client_secrets_path.exists():
        try:
            sec_data = json.loads(client_secrets_path.read_text(encoding="utf-8"))
            installed = sec_data.get("installed") or sec_data.get("web") or {}
            client_id = client_id or installed.get("client_id")
            client_secret = client_secret or installed.get("client_secret")
        except Exception:
            pass

    expiry: Optional[datetime] = None
    if data.get("expiry"):
        try:
            expiry_str = data["expiry"]
            if expiry_str.endswith("Z"):
                expiry_str = expiry_str[:-1] + "+00:00"
            expiry = datetime.fromisoformat(expiry_str)
            if expiry.tzinfo is not None:
                # google.oauth2.credentials uses naive UTC datetimes
                expiry = expiry.astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            expiry = None

    # Check if access token is already valid
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    is_expired = expiry is not None and expiry <= now_utc

    if access_token and not is_expired:
        return access_token, None

    # If expired or missing access_token, attempt refresh using refresh_token
    if refresh_token:
        try:
            creds = Credentials(
                token=access_token,
                refresh_token=refresh_token,
                token_uri=token_uri,
                client_id=client_id,
                client_secret=client_secret,
                scopes=scopes,
                expiry=expiry,
            )
            req = transport if transport is not None else Request()
            creds.refresh(req)

            if creds.token:
                # Save refreshed credentials back to disk
                save_credentials_json(
                    token_path=token_path,
                    access_token=creds.token,
                    refresh_token=creds.refresh_token or refresh_token,
                    token_uri=creds.token_uri or token_uri,
                    client_id=creds.client_id or client_id,
                    client_secret=creds.client_secret or client_secret,
                    scopes=creds.scopes or scopes,
                    expiry=creds.expiry,
                )
                return creds.token, None
        except google.auth.exceptions.RefreshError as r_err:
            logger.error(f"YouTube OAuth token refresh rejected: {redact_secrets(str(r_err))}")
            return None, f"AUTH_REFRESH_REJECTED: {redact_secrets(str(r_err))}"
        except Exception as exc:
            logger.error(f"YouTube OAuth token refresh failed: {redact_secrets(str(exc))}")
            return None, f"AUTH_REFRESH_FAILED: {redact_secrets(str(exc))}"

    # Fallback: if access_token exists without expiry info and no refresh was possible, return it
    if access_token and expiry is None:
        return access_token, None

    return None, "AUTH_EXPIRED_NO_REFRESH"
