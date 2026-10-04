"""Renderer transitions: silent-handle xfade, verified against real ffmpeg.

The silent-handle design gives each non-final segment an extra ``T`` seconds of
tail (silent audio via ``apad``, continued video motion). ``xfade`` consumes
that tail at a cumulative offset equal to the sum of preceding scene durations,
so total duration must come out as exactly ``sum(scene_durations)`` -- the
handles are absorbed, not lost. These tests actually render, because the whole
point of the arithmetic is that ffmpeg agrees with it.

The hint lives on the INCOMING scene (``scenes[i]["transition_hint"]`` governs
the boundary between ``i-1`` and ``i``).
"""
import math
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from autopilot.core.contracts import RenderPlan
from autopilot.core.renderer import (
    FFmpegRenderer,
    TRANSITION_DURATION_SEC,
    _build_xfade_chain,
    _effective_handle,
    _resolve_xfade_hint,
)

pytestmark = pytest.mark.skipif(
    subprocess.run(["where", "ffmpeg"], capture_output=True).returncode != 0,
    reason="ffmpeg is required for renderer transition tests",
)

FIXTURE_IMG = Path(__file__).parent.parent / "autopilot" / "providers" / "fixture_image.png"
FIXTURE_VID = Path(__file__).parent.parent / "autopilot" / "providers" / "fixture_video.mp4"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _plan(scenes, job_id="job-trans", plan_id="plan-trans"):
    return RenderPlan(
        plan_id=plan_id,
        content_id=f"c-{job_id}",
        job_id=job_id,
        profile="vertical_short",
        scenes=scenes,
    )


def _scene(scene_id, dur, asset, hint=None, audio=None):
    s = {
        "scene_id": scene_id,
        "duration_sec": dur,
        "asset_path": str(asset),
        "narration": f"Narration for {scene_id} with several words.",
    }
    if hint is not None:
        s["transition_hint"] = hint
    if audio is not None:
        s["audio_path"] = str(audio)
    return s


def _probe_duration(path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def _tone_wav(path: Path, freq_hz: float, duration_s: float, sr: int = 44100, amp: float = 0.25):
    """Write a mono 16-bit sine WAV with the stdlib only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(sr * duration_s)) / sr
    data = (amp * np.sin(2 * math.pi * freq_hz * t) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(data.tobytes())
    return path


def _extract_mono_pcm(video: Path) -> np.ndarray:
    """Decode the output's audio to float32 mono via ffmpeg."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video),
         "-f", "s16le", "-ac", "1", "-ar", "44100", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def _tone_energy(samples: np.ndarray, freq_hz: float, sr: int = 44100) -> float:
    """Energy at a frequency via a Goertzel-style DFT dot product."""
    n = len(samples)
    if n == 0:
        return 0.0
    idx = np.arange(n)
    # Use an integer number of cycles for a clean single-bin measurement.
    cycles = max(1, int(round(freq_hz * (n / sr))))
    f0 = cycles * sr / n
    basis = np.cos(2 * np.pi * f0 * idx / sr) + 1j * np.sin(2 * np.pi * f0 * idx / sr)
    return float(np.abs(np.dot(samples, basis)) / n)


def _window(pcm: np.ndarray, start_s: float, end_s: float, sr: int = 44100) -> np.ndarray:
    return pcm[int(start_s * sr): int(end_s * sr)]


# --------------------------------------------------------------------------
# pure unit: hint resolution
# --------------------------------------------------------------------------
def test_xfade_resolve_helper():
    assert _resolve_xfade_hint("cut") is None
    assert _resolve_xfade_hint("CUT") is None
    assert _resolve_xfade_hint(None) is None
    assert _resolve_xfade_hint("") is None
    assert _resolve_xfade_hint("fade") == "fadeblack"
    assert _resolve_xfade_hint("fadeblack") == "fadeblack"
    assert _resolve_xfade_hint("fade_in") == "fadeblack"
    assert _resolve_xfade_hint("dissolve") == "fade"
    assert _resolve_xfade_hint("slideup") == "fade"
    assert _resolve_xfade_hint("glitch") == "fade"


def test_chain_offsets_are_cumulative_scene_durations():
    """Offsets must be sum(durs[:k]) with NO handle added."""
    graph, _v, _a = _build_xfade_chain(3, [1.5, 1.5, 1.5], ["fadeblack", "fadeblack"])
    offsets = [seg.split("offset=")[1].split("[")[0] for seg in graph.split(";") if "xfade" in seg]
    assert offsets == ["1.500", "3.000"]


def test_chain_mixes_cut_and_transition():
    graph, _v, _a = _build_xfade_chain(3, [2.0, 2.0, 2.0], ["fadeblack", None])
    assert "xfade" in graph
    assert "concat=n=2" in graph


def test_handle_clamped_by_short_adjacent_scene():
    # 0.45 * min(0.5, 2.0) = 0.225
    assert _effective_handle(1, [0.5, 2.0]) == pytest.approx(0.225)
    assert _effective_handle(1, [2.0, 2.0]) == pytest.approx(TRANSITION_DURATION_SEC)
    assert _effective_handle(1, [0.0, 0.0]) == pytest.approx(TRANSITION_DURATION_SEC)


# --------------------------------------------------------------------------
# real ffmpeg
# --------------------------------------------------------------------------
def test_xfade_preserves_total_duration(tmp_path):
    """The core premise: handles are absorbed, so total == sum(durations)."""
    out = tmp_path / "final.mp4"
    plan = _plan([
        _scene("s1", 2.0, FIXTURE_IMG),
        _scene("s2", 2.0, FIXTURE_IMG, hint="fade"),
    ])
    result = FFmpegRenderer(profile="vertical_short").render(plan, str(out))

    assert out.exists()
    probed = _probe_duration(out)
    assert result.duration_sec == pytest.approx(4.0, abs=0.25)
    assert probed == pytest.approx(4.0, abs=0.25)


def test_all_cut_hints_use_plain_concat_not_xfade(tmp_path, monkeypatch):
    """With every hint 'cut' the legacy concat path must be used verbatim."""
    from autopilot.core import renderer as renderer_mod

    seen = {}
    real_run = subprocess.run

    def spy(cmd, *a, **kw):
        if isinstance(cmd, list) and cmd and cmd[0] == "ffmpeg" and "-filter_complex" in cmd:
            seen["fc"] = cmd[cmd.index("-filter_complex") + 1]
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(renderer_mod.subprocess, "run", spy)

    out = tmp_path / "final.mp4"
    plan = _plan([
        _scene("s1", 1.5, FIXTURE_IMG, hint="cut"),
        _scene("s2", 1.5, FIXTURE_IMG, hint="cut"),
    ])
    FFmpegRenderer(profile="vertical_short").render(plan, str(out))

    assert "xfade" not in seen["fc"]
    assert "acrossfade" not in seen["fc"]
    assert "concat=n=2:v=1:a=1" in seen["fc"]


def test_three_scene_chain_renders_and_preserves_duration(tmp_path):
    out = tmp_path / "final.mp4"
    plan = _plan([
        _scene("s1", 1.5, FIXTURE_IMG),
        _scene("s2", 1.5, FIXTURE_IMG, hint="fade"),
        _scene("s3", 1.5, FIXTURE_IMG, hint="fade"),
    ])
    FFmpegRenderer(profile="vertical_short").render(plan, str(out))
    assert _probe_duration(out) == pytest.approx(4.5, abs=0.3)


def test_mixed_cut_and_transition_preserves_duration(tmp_path):
    """A cut boundary must NOT inflate duration (no handle is consumed there)."""
    out = tmp_path / "final.mp4"
    plan = _plan([
        _scene("s1", 2.0, FIXTURE_IMG, hint="fade"),
        _scene("s2", 2.0, FIXTURE_IMG, hint="cut"),
        _scene("s3", 2.0, FIXTURE_IMG, hint="cut"),
    ])
    FFmpegRenderer(profile="vertical_short").render(plan, str(out))
    assert _probe_duration(out) == pytest.approx(6.0, abs=0.3)


def test_narration_windows_survive_the_transition(tmp_path):
    """Each scene's tone must still dominate its own window, at full level.

    This is what catches a naive acrossfade (default triangular curves), which
    ramps the incoming scene's opening syllable in from silence.
    """
    a1 = _tone_wav(tmp_path / "a1.wav", 440.0, 2.0)
    a2 = _tone_wav(tmp_path / "a2.wav", 880.0, 2.0)

    out = tmp_path / "final.mp4"
    plan = _plan([
        _scene("s1", 2.0, FIXTURE_IMG, audio=a1),
        _scene("s2", 2.0, FIXTURE_IMG, hint="fade", audio=a2),
    ])
    FFmpegRenderer(profile="vertical_short").render(plan, str(out))

    pcm = _extract_mono_pcm(out)
    assert len(pcm) > 0

    # Sample the middle of each scene window, clear of the boundary transition.
    w0 = _window(pcm, 0.6, 1.6)
    w1 = _window(pcm, 2.6, 3.6)

    e0_440 = _tone_energy(w0, 440.0)
    e0_880 = _tone_energy(w0, 880.0)
    e1_440 = _tone_energy(w1, 440.0)
    e1_880 = _tone_energy(w1, 880.0)

    assert e0_440 > e0_880 * 5, "scene 1 window is not dominated by scene 1's tone"
    assert e1_880 > e1_440 * 5, "scene 2 window is not dominated by scene 2's tone"

    # Full level, not attenuated: compare each against its own solo reference.
    solo1 = _tone_energy(_window(_extract_mono_pcm_of_wav(a1), 0.6, 1.6), 440.0)
    solo2 = _tone_energy(_window(_extract_mono_pcm_of_wav(a2), 0.6, 1.6), 880.0)
    assert e0_440 > solo1 * 0.3, "scene 1 narration lost level"
    assert e1_880 > solo2 * 0.3, "scene 2 narration lost level"


def _extract_mono_pcm_of_wav(wav: Path) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(wav), "-f", "s16le", "-ac", "1", "-ar", "44100", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def test_short_scene_does_not_break_the_chain(tmp_path):
    """A 0.5s scene must clamp rather than emit an illegal xfade offset."""
    out = tmp_path / "final.mp4"
    plan = _plan([
        _scene("s1", 0.5, FIXTURE_IMG),
        _scene("s2", 2.0, FIXTURE_IMG, hint="fade"),
    ])
    FFmpegRenderer(profile="vertical_short").render(plan, str(out))
    assert out.exists()
    assert _probe_duration(out) == pytest.approx(2.5, abs=0.3)


def test_video_asset_scenes_transition(tmp_path):
    out = tmp_path / "final.mp4"
    plan = _plan([
        _scene("s1", 2.0, FIXTURE_VID),
        _scene("s2", 2.0, FIXTURE_VID, hint="fade"),
    ])
    FFmpegRenderer(profile="vertical_short").render(plan, str(out))
    assert _probe_duration(out) == pytest.approx(4.0, abs=0.3)


def test_single_scene_is_unaffected_by_transition_logic(tmp_path):
    out = tmp_path / "final.mp4"
    plan = _plan([_scene("s1", 2.0, FIXTURE_IMG, hint="fade")])
    FFmpegRenderer(profile="vertical_short").render(plan, str(out))
    assert _probe_duration(out) == pytest.approx(2.0, abs=0.2)