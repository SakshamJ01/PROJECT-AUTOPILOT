"""Shared pytest fixtures.

The suite must be hermetic: `CONFIG` loads the developer's real `.env` at import
time, so without this fixture provider tests inherit live credentials and issue
real network calls (OpenRouter/Gemini returned HTTP 401/404 mid-suite instead of
exercising the fail-closed guards they assert on).

Every test runs with provider credentials and models cleared, unless the test
opts back in via the ``use_real_provider_credentials`` marker.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest

from autopilot.core.config import CONFIG

# Environment variables that can supply an LLM credential or model.
_PROVIDER_ENV_VARS = (
    "GEMINI_API_KEY",
    "AUTOPILOT_GEMINI_API_KEY",
    "GEMINI_MODEL",
    "AUTOPILOT_GEMINI_MODEL",
    "OPENROUTER_API_KEY",
    "AUTOPILOT_OPENROUTER_API_KEY",
    "OPENROUTER_KEY",
    "OPENROUTER_MODEL",
    "AUTOPILOT_OPENROUTER_MODEL",
    "ATRIA_API_KEY",
    "AUTOPILOT_ATRIA_API_KEY",
    "ATRIA_MODEL",
    "AUTOPILOT_ATRIA_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "YOUTUBE_ACCESS_TOKEN",
    "YOUTUBE_TOKEN_PATH",
    "YOUTUBE_CLIENT_SECRETS_PATH",
)

# Matching CONFIG fields, which .env populates at import time.
_CONFIG_PROVIDER_FIELDS = (
    "gemini_api_key",
    "gemini_model",
    "openrouter_api_key",
    "openrouter_model",
    "atria_api_key",
    "atria_model",
    "openai_api_key",
    "openai_model",
    "ollama_model",
    "youtube_access_token",
    "youtube_token_path",
    "youtube_client_secrets_path",
)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "use_real_provider_credentials: opt in to real provider credentials from .env",
    )


@pytest.fixture(autouse=True)
def isolate_provider_credentials(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Strip real provider credentials/model config so tests stay offline and fail closed."""
    if request.node.get_closest_marker("use_real_provider_credentials"):
        yield
        return

    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    # Config forbids unknown attributes, so only neutralise fields that really exist.
    model_fields = getattr(type(CONFIG), "model_fields", {})
    fields = [f for f in _CONFIG_PROVIDER_FIELDS if f in model_fields]
    saved = {field: getattr(CONFIG, field) for field in fields}
    for field in fields:
        setattr(CONFIG, field, None)

    # Neutralise the YouTube default-path fallback so tests never silently resolve
    # a real token parked at project_root/credentials/youtube_token.json. Explicit
    # credentials (set on a test-built Config instance) keep working, and configs
    # whose default-path candidates are fully redirected to temp dirs also keep
    # working, so the OAuth/default-path tests are unaffected.
    import autopilot.providers.youtube_oauth as _youtube_oauth

    _orig_resolve = _youtube_oauth.resolve_youtube_access_token
    _real_root = Path(CONFIG.project_root)
    _real_artifacts_parent = Path(CONFIG.get_artifacts_dir()).parent

    def _isolated_resolve(config=None, transport=None):
        cfg = CONFIG if config is None else config
        if getattr(cfg, "youtube_access_token", None) or getattr(cfg, "youtube_token_path", None):
            return _orig_resolve(config, transport)
        cfg_root = Path(getattr(cfg, "project_root", CONFIG.project_root))
        cfg_art_parent = Path(cfg.get_artifacts_dir()).parent
        if cfg_root == _real_root or cfg_art_parent == _real_artifacts_parent:
            return None
        return _orig_resolve(config, transport)

    monkeypatch.setattr(_youtube_oauth, "resolve_youtube_access_token", _isolated_resolve)

    try:
        yield
    finally:
        for field, value in saved.items():
            setattr(CONFIG, field, value)