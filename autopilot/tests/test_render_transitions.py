"""Renderer transitions (silent-handle xfade).

Requirements per plan:
- Extend segments by T for non-final scenes (video looped, audio padded), chain xfades with cumulative offsets
- honor transition_hint ('cut' -> concat; others -> xfade)
- preserve total duration exactly
- keep narration-truncation guard unchanged
"""
import math
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from autopilot.core import renderer
from autopilot.core.renderer import FFmpegRenderer, _resolve_xfade_hint, TRANSITION_DURATION_SEC
from autopilot.core.contracts import RenderPlan, RenderScene


def _make_image(path: Path, color=(0, 0, 0), size=(1080, 1920)):
    img = Image.new("RGB", size, color=color)
    img.save(path)


def _make_tone_wav(path: Path, freq_hz: float = 440.0, duration_s: float = 2.0, sr: int = 44100):
    t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
    wav = 0.2 * np.sin(2 * math.pi * freq_hz * t)
    path.parent.mkdir(parents=True, exist_ok=True)
    import wave
    # simpler cross-platform fallback
    import struct
    wav_data = (wav * 32767.0).astype(np.int16)
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(wav_data.tobytes())


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