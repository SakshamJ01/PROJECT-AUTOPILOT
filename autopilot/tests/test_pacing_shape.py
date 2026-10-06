"""Plan 2.3 — pacing rhythm variation at timeline materialization.

``apply_pacing_shape`` nudges target durations into a hook / middle / payoff
rhythm while never going below the measured voice duration (voice is the
floor — speech is never compressed) and never exceeding the pacing ceiling
for a nudge.
"""
from __future__ import annotations

import wave
from pathlib import Path
from types import SimpleNamespace

from autopilot.core.timeline import NarrativeRole
from autopilot.core.timeline_builder import (
    PACING_MAX_SCENE_SECONDS,
    apply_pacing_shape,
    build_materialized_timeline,
)

VOICE_DUR = 3.2


def test_seven_scene_shape_rhythm() -> None:
    """Spec: 7 scenes — scene-1 <= middle, last >= middle, all within [voice, 4.5]."""
    voices = [VOICE_DUR] * 7
    shaped = apply_pacing_shape(voices)

    assert len(shaped) == 7
    hook, middles, payoff = shaped[0], shaped[1:-1], shaped[-1]
    assert hook <= min(middles)
    assert payoff >= max(middles)
    for voice, dur in zip(voices, shaped):
        assert voice <= dur <= PACING_MAX_SCENE_SECONDS


def test_short_hook_pads_into_band_and_middle_stays_natural() -> None:
    voices = [2.4] + [3.0] * 5 + [3.0]
    shaped = apply_pacing_shape(voices)

    assert shaped[0] == 2.8  # padded up to the 2.8s band floor
    assert 2.8 <= shaped[0] <= 3.2
    assert shaped[1:-1] == [3.0] * 5  # speech-natural: no nudge at all
    assert shaped[-1] == 4.0  # payoff target


def test_voice_is_never_compressed_even_above_ceiling() -> None:
    voices = [5.0, 5.0, 4.7]
    shaped = apply_pacing_shape(voices)

    assert shaped == [5.0, 5.0, 4.7]  # voice floor outranks band and ceiling


def test_ceiling_bounds_every_nudge() -> None:
    voices = [4.4, 4.4, 3.0]
    shaped = apply_pacing_shape(voices)

    for dur in shaped:
        assert dur <= PACING_MAX_SCENE_SECONDS
    assert shaped[0] == 4.4  # hook voice above the band is left alone
    assert shaped[-1] == 4.4  # payoff at least matches the middle (4.4)


def test_empty_and_single_scene_inputs() -> None:
    assert apply_pacing_shape([]) == []
    assert apply_pacing_shape([3.1]) == [3.1]  # single CONTENT scene: no shape


def test_explicit_payoff_role_gets_the_long_hold() -> None:
    roles = [NarrativeRole.HOOK, NarrativeRole.CONTENT, NarrativeRole.PAYOFF]
    shaped = apply_pacing_shape([3.0, 3.0, 3.0], roles)

    assert shaped[0] <= shaped[1]
    assert shaped[-1] >= shaped[1]
    assert shaped[-1] == 4.0


def _write_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 1600)


def test_build_materialized_timeline_applies_shape(tmp_path: Path) -> None:
    """Wiring: timing/cursor use the shaped duration, audio keeps true voice."""
    voices = {"scene_1": 2.4, "scene_2": 3.0, "scene_3": 3.0}
    script_scenes = []
    plan_scenes = []
    for scene_id, voice_dur in voices.items():
        asset = tmp_path / f"{scene_id}.png"
        asset.write_bytes(b"\x89PNG\r\n\x1a\n fake")
        voice = tmp_path / f"{scene_id}.wav"
        _write_wav(voice)
        script_scenes.append(
            SimpleNamespace(
                scene_id=scene_id,
                narration=f"Narration for {scene_id}.",
                visual_intent=f"visual for {scene_id}",
                asset_query=None,
                transition_hint=None,
                word_timestamps=None,
            )
        )
        plan_scenes.append(
            {
                "scene_id": scene_id,
                "asset_path": str(asset),
                "audio_path": str(voice),
                "duration_sec": voice_dur,
                "asset_type": "image",
            }
        )

    timeline = build_materialized_timeline(
        job_id="job-pacing",
        script=SimpleNamespace(scenes=script_scenes),
        plan_scenes=plan_scenes,
        topic="deep sea mysteries",
    )

    timing = [s.timing.duration_sec for s in timeline.scenes]
    assert timing == [2.8, 3.0, 4.0]  # hook padded, middle natural, payoff held

    # Audio plan keeps the TRUE measured voice duration (voice is the floor).
    audio = [s.audio_plan.voice_duration_sec for s in timeline.scenes]
    assert audio == [2.4, 3.0, 3.0]

    # Cursor math stays contiguous and totals the shaped durations.
    assert timeline.scenes[0].timing.start_time_sec == 0.0
    assert timeline.scenes[1].timing.start_time_sec == timeline.scenes[0].timing.end_time_sec
    assert timeline.scenes[2].timing.start_time_sec == timeline.scenes[1].timing.end_time_sec
    assert timeline.total_measured_duration_sec == 9.8

    # Hard invariant INVARIANT_03_AUDIO_COVERAGE: visual >= voice everywhere.
    for scene in timeline.scenes:
        assert scene.timing.duration_sec >= scene.audio_plan.voice_duration_sec - 0.05
