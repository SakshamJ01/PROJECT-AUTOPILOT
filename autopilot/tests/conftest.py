"""Shared pytest fixtures.

The suite must be hermetic: `CONFIG` loads the developer's real `.env` at import
time, so without this fixture provider tests inherit live credentials and issue
real network calls (OpenRouter/Gemini returned HTTP 401/404 mid-suite instead of
exercising the fail-closed guards they assert on).

Every test runs with provider credentials and models cleared, unless the test
opts back in via the ``use_real_provider_credentials`` marker.
"""
from __future__ import annotations

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

    try:
        yield
    finally:
        for field, value in saved.items():
            setattr(CONFIG, field, value)