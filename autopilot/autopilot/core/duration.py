"""Deterministic spoken duration estimation — Phase 1.
No external model required.
Configurable words-per-minute.
"""
from __future__ import annotations

DEFAULT_WPM = 150.0


def estimate_duration(text: str, wpm: float = DEFAULT_WPM) -> float:
    """Estimate spoken duration in seconds from word count.
    Deterministic; configurable rate.
    """
    if not text or not isinstance(text, str):
        return 0.0
    word_count = len(text.split())
    seconds = (word_count / wpm) * 60.0
    return max(round(seconds, 2), 0.5)  # minimum 0.5s for realism


def estimate_scene_durations(scenes, wpm: float = DEFAULT_WPM) -> float:
    """Total estimated duration for a list of scenes (each with narration)."""
    total = 0.0
    for s in scenes:
        narration = getattr(s, "narration", "") or ""
        total += estimate_duration(narration, wpm=wpm)
    return round(total, 2)
