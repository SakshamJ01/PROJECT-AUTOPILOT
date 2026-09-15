"""Regression tests — local_only policy / provider resolution.

Verifies:
  1. local_only without explicit provider flags → real providers substituted.
  2. local_only with real explicit providers → those values are preserved.
  3. local_only with explicitly-requested mock providers → ValueError raised (fail-closed).
  4. Unknown/custom policy → providers left unchanged (permissive).
  5. run_produce() fail-closed guard: reaching it with mock under local_only raises RuntimeError.
  6. cheap_first / quality_first also substitute mocks with real providers.
  7. Policy map completeness and no-mock invariants.
"""
from __future__ import annotations

import pytest

from autopilot.cli.main import (
    resolve_providers_for_policy,
    run_produce,
    _MOCK_PROVIDER_VALUES,
    _POLICY_PROVIDER_MAP,
)


# ---------------------------------------------------------------------------
# resolve_providers_for_policy — local_only default substitution
# ---------------------------------------------------------------------------

def test_local_only_substitutes_mock_llm():
    """Default mock LLM must be replaced with openai_compatible under local_only."""
    llm, _, _, _ = resolve_providers_for_policy(
        "local_only", "mock", "wikipedia", "kokoro", "moneyprinterturbo",
    )
    assert llm == "openai_compatible"


def test_local_only_substitutes_mock_search():
    """Default mock_search research must be replaced with wikipedia under local_only."""
    _, research, _, _ = resolve_providers_for_policy(
        "local_only", "openai_compatible", "mock_search", "kokoro", "moneyprinterturbo",
    )
    assert research == "wikipedia"


def test_local_only_substitutes_none_tts():
    """'none' TTS provider must be replaced with kokoro under local_only."""
    _, _, tts, _ = resolve_providers_for_policy(
        "local_only", "openai_compatible", "wikipedia", "none", "moneyprinterturbo",
    )
    assert tts == "kokoro"


def test_local_only_all_defaults_resolved():
    """All three mock defaults resolved together via local_only."""
    llm, research, tts, engine = resolve_providers_for_policy(
        "local_only", "mock", "mock_search", "none", "moneyprinterturbo",
    )
    assert llm == "openai_compatible"
    assert research == "wikipedia"
    assert tts == "kokoro"
    assert engine == "moneyprinterturbo"


# ---------------------------------------------------------------------------
# resolve_providers_for_policy — explicit real values are preserved
# ---------------------------------------------------------------------------

def test_local_only_preserves_explicit_real_llm():
    """Explicit openai_compatible LLM must not be changed."""
    llm, _, _, _ = resolve_providers_for_policy(
        "local_only", "openai_compatible", "wikipedia", "kokoro", "moneyprinterturbo",
        llm_explicit=True,
    )
    assert llm == "openai_compatible"


def test_local_only_preserves_explicit_combined_research():
    """Explicitly provided 'combined' research provider must be preserved."""
    _, research, _, _ = resolve_providers_for_policy(
        "local_only", "openai_compatible", "combined", "kokoro", "moneyprinterturbo",
        research_explicit=True,
    )
    assert research == "combined"


def test_local_only_preserves_explicit_crawl4ai():
    """crawl4ai is a real provider and must pass through unchanged."""
    _, research, _, _ = resolve_providers_for_policy(
        "local_only", "openai_compatible", "crawl4ai", "kokoro", "moneyprinterturbo",
        research_explicit=True,
    )
    assert research == "crawl4ai"


# ---------------------------------------------------------------------------
# resolve_providers_for_policy — explicit mock under local_only → ValueError
# ---------------------------------------------------------------------------

def test_local_only_rejects_explicit_mock_llm():
    """local_only must raise ValueError when the user explicitly requests mock LLM."""
    with pytest.raises(ValueError, match="forbids mock LLM"):
        resolve_providers_for_policy(
            "local_only", "mock", "wikipedia", "kokoro", "moneyprinterturbo",
            llm_explicit=True,
        )


def test_local_only_rejects_explicit_mock_search():
    """local_only must raise ValueError when the user explicitly requests mock_search."""
    with pytest.raises(ValueError, match="forbids mock research"):
        resolve_providers_for_policy(
            "local_only", "openai_compatible", "mock_search", "kokoro", "moneyprinterturbo",
            research_explicit=True,
        )


def test_local_only_rejects_explicit_none_tts():
    """local_only must raise ValueError when the user explicitly requests 'none' TTS."""
    with pytest.raises(ValueError, match="forbids mock/no-op TTS"):
        resolve_providers_for_policy(
            "local_only", "openai_compatible", "wikipedia", "none", "moneyprinterturbo",
            tts_explicit=True,
        )


def test_local_only_rejects_explicit_mock_tts():
    """local_only must raise ValueError for --tts-provider mock."""
    with pytest.raises(ValueError, match="forbids mock/no-op TTS"):
        resolve_providers_for_policy(
            "local_only", "openai_compatible", "wikipedia", "mock", "moneyprinterturbo",
            tts_explicit=True,
        )


# ---------------------------------------------------------------------------
# Unknown / custom policy — permissive (no substitution)
# ---------------------------------------------------------------------------

def test_unknown_policy_is_permissive():
    """Unknown policy names must not touch provider values."""
    llm, research, tts, engine = resolve_providers_for_policy(
        "my_custom_policy", "mock", "mock_search", "none", "ffmpeg",
    )
    assert llm == "mock"
    assert research == "mock_search"
    assert tts == "none"
    assert engine == "ffmpeg"


# ---------------------------------------------------------------------------
# cheap_first and quality_first also substitute mocks
# ---------------------------------------------------------------------------

def test_cheap_first_substitutes_mocks():
    llm, research, tts, _ = resolve_providers_for_policy(
        "cheap_first", "mock", "mock_search", "none", "moneyprinterturbo",
    )
    assert llm not in _MOCK_PROVIDER_VALUES
    assert research not in _MOCK_PROVIDER_VALUES
    assert tts not in _MOCK_PROVIDER_VALUES


def test_quality_first_substitutes_mocks():
    llm, research, tts, _ = resolve_providers_for_policy(
        "quality_first", "mock", "mock_search", "mock", "moneyprinterturbo",
    )
    assert llm not in _MOCK_PROVIDER_VALUES
    assert research not in _MOCK_PROVIDER_VALUES
    assert tts not in _MOCK_PROVIDER_VALUES


# ---------------------------------------------------------------------------
# run_produce() fail-closed guard (double-check inside produce function)
# ---------------------------------------------------------------------------

def test_run_produce_fail_closed_mock_llm_under_local_only():
    """run_produce must raise RuntimeError if a mock LLM slips through to local_only."""
    with pytest.raises(RuntimeError, match="local_only.*incompatible.*LLM provider"):
        run_produce(
            topic="Test topic",
            policy="local_only",
            llm_provider="mock",
            research_provider="wikipedia",
            tts_provider="kokoro",
        )


def test_run_produce_fail_closed_mock_search_under_local_only():
    """run_produce must raise RuntimeError if mock_search slips through under local_only."""
    with pytest.raises(RuntimeError, match="local_only.*incompatible.*research provider"):
        run_produce(
            topic="Test topic",
            policy="local_only",
            llm_provider="openai_compatible",
            research_provider="mock_search",
            tts_provider="kokoro",
        )


# ---------------------------------------------------------------------------
# Policy map invariants
# ---------------------------------------------------------------------------

def test_policy_map_completeness():
    """Every entry in _POLICY_PROVIDER_MAP must define llm, research, tts, production_engine."""
    required_keys = {"llm", "research", "tts", "production_engine"}
    for tier_name, tier in _POLICY_PROVIDER_MAP.items():
        missing = required_keys - tier.keys()
        assert not missing, f"Tier {tier_name!r} is missing keys: {missing}"


def test_policy_map_no_mock_values():
    """The canonical provider names in _POLICY_PROVIDER_MAP must not be mock providers."""
    for tier_name, tier in _POLICY_PROVIDER_MAP.items():
        for role, value in tier.items():
            assert value not in _MOCK_PROVIDER_VALUES, (
                f"Tier {tier_name!r} maps {role!r} -> {value!r} which is a mock/no-op value"
            )

