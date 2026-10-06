"""Plan 3.1 — per-topic procedural BGM mood.

The bed stays procedural (no licensing risk) but is deterministic per topic:
keyword resolution first, stable SHA-256 hash fallback, and the resolved
mood is recorded in the audio mix manifest for observability.
"""
from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

from autopilot.core.audio_scene_graph import (
    BGM_MOODS,
    AudioSceneGraphEngine,
    generate_ambient_bgm_track,
    resolve_bgm_mood,
)


def test_different_topics_yield_different_mood_labels() -> None:
    dramatic = resolve_bgm_mood("war in the pacific")
    contemplative = resolve_bgm_mood("morning yoga routine")

    assert dramatic != contemplative
    assert dramatic == "dramatic"
    assert contemplative == "contemplative"


def test_same_topic_yields_same_mood() -> None:
    topic = "the mysteries of the deep ocean"
    assert resolve_bgm_mood(topic) == resolve_bgm_mood(topic)
    assert resolve_bgm_mood(topic) == "mysterious"


def test_hash_fallback_is_deterministic_and_valid() -> None:
    topic = "quantum flux capacitor analysis"
    first = resolve_bgm_mood(topic)
    assert first == resolve_bgm_mood(topic)
    assert first in BGM_MOODS


def test_empty_topic_defaults_to_contemplative() -> None:
    assert resolve_bgm_mood("") == "contemplative"
    assert resolve_bgm_mood(None) == "contemplative"


def test_mood_beds_are_distinct_and_cached(tmp_path: Path) -> None:
    contemplative = generate_ambient_bgm_track(tmp_path / "warm.wav", 5.0, mood="contemplative")
    dramatic = generate_ambient_bgm_track(tmp_path / "tense.wav", 5.0, mood="dramatic")

    assert contemplative.read_bytes() != dramatic.read_bytes()
    # Existing bed is served from cache, never regenerated.
    again = generate_ambient_bgm_track(tmp_path / "warm.wav", 5.0, mood="contemplative")
    assert again == contemplative


def _write_voice_wav(path: Path, duration_sec: float = 2.0) -> None:
    sr = 44100
    frames = bytearray()
    for i in range(int(sr * duration_sec)):
        # 0.5s tone / 0.5s silence slots so ducking measurement has structure.
        val = 0.0
        if (i % sr) < sr // 2:
            val = 0.4 * math.sin(2.0 * math.pi * 440.0 * i / sr)
        pcm = int(val * 32767)
        frames.extend(struct.pack("<hh", pcm, pcm))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(frames))


def test_mood_is_recorded_in_graph_and_mix_manifest(tmp_path: Path) -> None:
    voice = tmp_path / "voice.wav"
    _write_voice_wav(voice)

    engine = AudioSceneGraphEngine()
    rows = [
        {
            "scene_id": "scene_1",
            "start_sec": 0.0,
            "end_sec": 2.0,
            "duration_sec": 2.0,
            "voice_path": str(voice),
        }
    ]
    graph = engine.build_scene_graph_from_rows(rows, total_duration_sec=2.0, topic="war documentary")
    assert graph.bgm_config.get("mood") == "dramatic"

    mix = engine.mix_and_master(graph, tmp_path / "master.wav")
    assert mix.success, mix.error_message
    assert mix.mix_manifest.get("mood") == "dramatic"
