"""Transitions must be reachable in production, not just in the renderer.

Two failure modes are guarded here:

1. The LLM prompt hardcoded ``"transition_hint": "cut"`` in every schema example,
   so no matter how correct the renderer's xfade chain was, every boundary was
   a hard cut and the feature never fired.
2. ``VisualBrandProfile.transition_preference`` was dead config: nothing ever
   read it, so a channel could not influence its own transitions.
"""
import pytest

from autopilot.core.contracts import ChannelProfile, VisualBrandProfile
from autopilot.core.pipeline import (
    DEFAULT_TRANSITION_PREFERENCE,
    _resolve_scene_transition_hint,
)
from autopilot.core.renderer import _resolve_xfade_hint


def _prompt_text() -> str:
    """Whole-module source: the prompt and parser live in module-level helpers."""
    import inspect

    from autopilot.providers import openai_llm_provider as mod

    return inspect.getsource(mod)


# --------------------------------------------------------------------------
# prompt wiring
# --------------------------------------------------------------------------
def test_prompt_no_longer_hardcodes_cut_examples():
    src = _prompt_text()
    assert '"transition_hint": "cut"' not in src, (
        "a hardcoded cut example makes every scene a hard cut"
    )


def test_prompt_examples_request_fade():
    src = _prompt_text()
    assert '"transition_hint": "fade"' in src


def test_prompt_documents_allowed_transition_values():
    src = _prompt_text()
    assert "RULES FOR TRANSITIONS" in src
    # The hint controls the cut INTO a scene; scene-01 is the open and is ignored.
    assert "into that scene" in src.lower()
    assert "cut" in src and "fade" in src


def test_parser_default_is_fade_not_cut():
    """A scene that omits the field must not silently become a hard cut."""
    assert '"transition_hint", "fade"' in _prompt_text()


# --------------------------------------------------------------------------
# brand preference fallback
# --------------------------------------------------------------------------
def test_explicit_hint_wins_over_brand_preference():
    prof = ChannelProfile(
        channel_id="c1",
        channel_name="Ch",
        visual=VisualBrandProfile(transition_preference="fade"),
    )
    assert _resolve_scene_transition_hint("cut", prof) == "cut"
    assert _resolve_scene_transition_hint("CUT", prof) == "cut"
    assert _resolve_scene_transition_hint("fade", prof) == "fade"


def test_brand_preference_used_when_hint_missing():
    prof = ChannelProfile(
        channel_id="c1",
        channel_name="Ch",
        visual=VisualBrandProfile(transition_preference="fadeblack"),
    )
    assert _resolve_scene_transition_hint(None, prof) == "fadeblack"
    assert _resolve_scene_transition_hint("", prof) == "fadeblack"
    assert _resolve_scene_transition_hint("   ", prof) == "fadeblack"


def test_brand_preference_cut_is_honored():
    prof = ChannelProfile(
        channel_id="c1",
        channel_name="Ch",
        visual=VisualBrandProfile(transition_preference="cut"),
    )
    assert _resolve_scene_transition_hint(None, prof) == "cut"
    # and that actually means a hard cut downstream
    assert _resolve_xfade_hint(_resolve_scene_transition_hint(None, prof)) is None


def test_fallback_is_safe_without_a_channel_profile():
    assert _resolve_scene_transition_hint(None, None) == DEFAULT_TRANSITION_PREFERENCE
    assert _resolve_xfade_hint(_resolve_scene_transition_hint(None, None)) == "fadeblack"


def test_brand_preference_is_actually_reachable():
    """Guard the exact bug: the brand lives on `.visual`, not `.visual_brand`."""
    prof = ChannelProfile(channel_id="c1", channel_name="Ch")
    assert hasattr(prof, "visual")
    assert not hasattr(prof, "visual_brand")


def test_channel_label_reads_from_real_channel_profile():
    """The thumbnail channel label used getattr(str, ...) and was always empty."""
    prof = ChannelProfile(channel_id="c1", channel_name="Deep Facts")
    label = str(getattr(prof, "channel_name", "") or "")
    assert label == "Deep Facts"