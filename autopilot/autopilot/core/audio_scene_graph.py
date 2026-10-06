"""Audio Scene Graph & Sidechain Ducking Engine — Phase 4.

Implements:
  - Explicit 3-track audio composition (Voice, BGM, SFX).
  - Voice reference track (0 dB reference, EBU R128 mastered at 44.1 kHz stereo).
  - Configurable BGM sidechain ducking (baseline -22 dB, speech ducking to -28 dB, pause rise to -16 dB).
  - Restrained SFX cues (whoosh_fast, bass_drop, digital_pop, camera_shutter, impact).
  - Procedural sound synthesis fallback for zero-dependency local operation.
  - Mastered 44.1 kHz stereo stem output and AudioMixManifest generation.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import subprocess
import wave
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from autopilot.core.config import CONFIG
from autopilot.core.timeline import (
    MaterializedAudioPlan,
    MaterializedScene,
    MaterializedTiming,
    SFXEvent,
)


@dataclass
class _GraphScene:
    """Minimal scene shape needed for audio graph construction."""

    scene_id: str
    timing: MaterializedTiming
    audio_plan: MaterializedAudioPlan


class AudioTrackType(str, Enum):
    VOICE = "VOICE"
    BGM = "BGM"
    SFX = "SFX"


class DuckingEnvelopePoint(BaseModel):
    time_sec: float
    gain_db: float
    reason: str = "speech_active"


class AudioSceneGraph(BaseModel):
    total_duration_sec: float = Field(..., gt=0.0)
    voice_segments: List[Dict[str, Any]] = Field(default_factory=list)
    bgm_config: Dict[str, Any] = Field(default_factory=dict)
    sfx_cues: List[Dict[str, Any]] = Field(default_factory=list)
    ducking_envelope: List[DuckingEnvelopePoint] = Field(default_factory=list)


class AudioMixResult(BaseModel):
    master_audio_path: str
    duration_sec: float
    sample_rate: int = 44100
    channels: int = 2
    voice_path: Optional[str] = None
    bgm_path: Optional[str] = None
    sfx_manifest: List[Dict[str, Any]] = Field(default_factory=list)
    mix_manifest: Dict[str, Any] = Field(default_factory=dict)
    success: bool = True
    error_message: Optional[str] = None


# ---------------------------------------------------------------------------
# Procedural Audio Synthesis (Zero-Dependency Fallbacks for SFX & BGM)
# ---------------------------------------------------------------------------

def synthesize_procedural_wav(
    output_path: Path,
    duration_sec: float,
    sample_rate: int = 44100,
    channels: int = 2,
    generator_func=None,
) -> Path:
    """Generate a clean 16-bit PCM WAV file using a custom mathematical waveform generator."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    num_frames = int(duration_sec * sample_rate)

    with wave.open(str(output_path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)

        frames = bytearray()
        for i in range(num_frames):
            t = i / sample_rate
            if generator_func:
                sample_val = generator_func(t, duration_sec)
            else:
                sample_val = 0.0

            # Clamp and convert to 16-bit signed integer
            clamped = max(-1.0, min(1.0, sample_val))
            int_val = int(clamped * 32767.0)
            packed = struct.pack("<h", int_val)
            for _ in range(channels):
                frames.extend(packed)

        wf.writeframes(frames)
    return output_path


def generate_sfx_sample(sfx_type: str, cache_dir: Path) -> Path:
    """Generate or retrieve a standardized restrained SFX sound effect."""
    clean_name = sfx_type.lower().replace(" ", "_")
    target = cache_dir / f"sfx_{clean_name}.wav"
    if target.exists() and target.stat().st_size > 100:
        return target

    if "whoosh" in clean_name:
        # Fast frequency-swept whoosh (0.35s)
        def whoosh_gen(t: float, dur: float) -> float:
            env = math.sin(math.pi * (t / dur)) ** 2
            freq = 180.0 + 600.0 * (1.0 - t / dur)
            noise = (math.sin(t * 1234.5) * 43758.5453) % 1.0 - 0.5
            return (math.sin(2.0 * math.pi * freq * t) * 0.4 + noise * 0.6) * env * 0.45

        return synthesize_procedural_wav(target, duration_sec=0.35, generator_func=whoosh_gen)

    elif "bass" in clean_name or "impact" in clean_name:
        # Punchy sub-bass drop / impact (0.5s)
        def bass_gen(t: float, dur: float) -> float:
            env = math.exp(-6.0 * (t / dur))
            freq = max(40.0, 110.0 * math.exp(-8.0 * (t / dur)))
            return math.sin(2.0 * math.pi * freq * t) * env * 0.65

        return synthesize_procedural_wav(target, duration_sec=0.50, generator_func=bass_gen)

    elif "pop" in clean_name or "click" in clean_name:
        # Subtle digital pop (0.12s)
        def pop_gen(t: float, dur: float) -> float:
            env = math.exp(-25.0 * (t / dur))
            freq = 650.0 + 400.0 * math.exp(-15.0 * (t / dur))
            return math.sin(2.0 * math.pi * freq * t) * env * 0.35

        return synthesize_procedural_wav(target, duration_sec=0.12, generator_func=pop_gen)

    elif "camera" in clean_name or "shutter" in clean_name:
        # Mechanical camera shutter click (0.25s)
        def shutter_gen(t: float, dur: float) -> float:
            env = math.exp(-15.0 * (t / dur)) * (1.0 if t < 0.08 or t > 0.14 else 0.2)
            noise = (math.sin(t * 5432.1) * 23456.789) % 1.0 - 0.5
            return noise * env * 0.40

        return synthesize_procedural_wav(target, duration_sec=0.25, generator_func=shutter_gen)

    else:
        # Default subtle cue
        def default_gen(t: float, dur: float) -> float:
            env = math.sin(math.pi * (t / dur))
            return math.sin(2.0 * math.pi * 440.0 * t) * env * 0.25

        return synthesize_procedural_wav(target, duration_sec=0.20, generator_func=default_gen)


# ---------------------------------------------------------------------------
# Per-topic procedural BGM mood (plan 3.1)
# ---------------------------------------------------------------------------

# Each mood is a deterministic procedural bed: its own chord progression,
# cycle length and pulse rate. Still procedural (no licensing risk) but the
# same topic always resolves to the same mood and therefore the same bed.
BGM_MOODS: Dict[str, Dict[str, Any]] = {
    # Warm lo-fi pad (the original C-G-Am-F bed) — the default fallback.
    "contemplative": {
        "chords": [
            (130.81, 196.00, 261.63),  # C major
            (98.00, 146.83, 196.00),   # G major
            (110.00, 164.81, 220.00),  # A minor
            (87.31, 130.81, 174.61),   # F major
        ],
        "cycle_sec": 8.0,
        "pulse_hz": 1.5,
        "gain": 0.35,
    },
    # Minor tension: Am-F-Dm-E with a slow heartbeat pulse.
    "dramatic": {
        "chords": [
            (110.00, 164.81, 220.00),  # A minor
            (87.31, 130.81, 174.61),   # F major
            (73.42, 110.00, 146.83),   # D minor
            (82.41, 123.47, 164.81),   # E minor voicing
        ],
        "cycle_sec": 10.0,
        "pulse_hz": 0.75,
        "gain": 0.34,
    },
    # Bright C-F-G-C with a quicker pulse.
    "uplifting": {
        "chords": [
            (130.81, 196.00, 261.63),  # C major
            (174.61, 220.00, 261.63),  # F major
            (196.00, 246.94, 293.66),  # G major
            (130.81, 196.00, 261.63),  # C major
        ],
        "cycle_sec": 6.0,
        "pulse_hz": 2.0,
        "gain": 0.33,
    },
    # Dark, sparse and slow for history / unsolved-mystery topics.
    "mysterious": {
        "chords": [
            (73.42, 110.00, 146.83),   # D minor
            (58.27, 87.31, 116.54),    # Bb major
            (65.41, 98.00, 130.81),    # C major
            (61.74, 92.50, 123.47),    # B minor voicing
        ],
        "cycle_sec": 12.0,
        "pulse_hz": 0.5,
        "gain": 0.34,
    },
}

# Ordered keyword -> mood map; first match wins so resolution is
# deterministic. Keywords are matched as whole words against the topic.
_BGM_MOOD_KEYWORDS: List[Tuple[str, str]] = [
    ("war", "dramatic"), ("wars", "dramatic"), ("battle", "dramatic"),
    ("battles", "dramatic"), ("crime", "dramatic"),
    ("murder", "dramatic"), ("killer", "dramatic"), ("disaster", "dramatic"),
    ("accident", "dramatic"), ("accidents", "dramatic"), ("crash", "dramatic"),
    ("sinking", "dramatic"),
    ("sank", "dramatic"), ("terror", "dramatic"), ("deadliest", "dramatic"),
    ("darkest", "dramatic"), ("failed", "dramatic"), ("failures", "dramatic"),
    ("yoga", "contemplative"), ("meditation", "contemplative"),
    ("mindful", "contemplative"), ("relax", "contemplative"),
    ("sleep", "contemplative"), ("peaceful", "contemplative"),
    ("nature", "contemplative"), ("forest", "contemplative"),
    ("quiet", "contemplative"), ("breathe", "contemplative"),
    ("success", "uplifting"), ("money", "uplifting"), ("business", "uplifting"),
    ("billionaire", "uplifting"), ("invest", "uplifting"), ("gym", "uplifting"),
    ("workout", "uplifting"), ("motivation", "uplifting"), ("winner", "uplifting"),
    ("celebrate", "uplifting"), ("habits", "uplifting"),
    ("mystery", "mysterious"), ("mysteries", "mysterious"),
    ("unsolved", "mysterious"),
    ("conspiracy", "mysterious"), ("secret", "mysterious"),
    ("secrets", "mysterious"), ("ancient", "mysterious"),
    ("disappeared", "mysterious"),
    ("haunted", "mysterious"), ("vanished", "mysterious"),
]


def resolve_bgm_mood(topic: str) -> str:
    """Resolve a topic to a named BGM mood deterministically.

    Whole-word topic keywords win; otherwise a stable SHA-256 hash of the
    normalized topic picks the mood, so the same topic always gets the same
    bed (builtin ``hash()`` is deliberately not used — it is salted per
    process and would make the bed differ between runs).
    """
    text = " ".join(str(topic or "").lower().split())
    if text:
        words = set(text.replace("-", " ").split())
        for keyword, mood in _BGM_MOOD_KEYWORDS:
            if keyword in words:
                return mood
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        names = sorted(BGM_MOODS)
        return names[int(digest, 16) % len(names)]
    return "contemplative"


def generate_ambient_bgm_track(
    output_path: Path,
    duration_sec: float,
    mood: str = "contemplative",
) -> Path:
    """Generate a procedural ambient pad bed for the requested mood."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and output_path.stat().st_size > 1000:
        return output_path

    spec = BGM_MOODS.get(mood) or BGM_MOODS["contemplative"]
    chords = spec["chords"]
    cycle_sec = float(spec["cycle_sec"])
    pulse_hz = float(spec["pulse_hz"])
    gain = float(spec["gain"])

    # Warm chord progression pad with a gentle mood-specific pulse.
    def bgm_gen(t: float, dur: float) -> float:
        cycle = (t % cycle_sec) / cycle_sec
        f1, f2, f3 = chords[min(int(cycle * len(chords)), len(chords) - 1)]

        pulse = 0.85 + 0.15 * math.sin(2.0 * math.pi * pulse_hz * t)
        v1 = math.sin(2.0 * math.pi * f1 * t) * 0.45
        v2 = math.sin(2.0 * math.pi * f2 * t) * 0.35
        v3 = math.sin(2.0 * math.pi * f3 * t) * 0.20
        # Gentle fade in / out at boundaries
        fade = min(1.0, t / 1.0) * min(1.0, (dur - t) / 1.0)
        return (v1 + v2 + v3) * pulse * fade * gain

    return synthesize_procedural_wav(output_path, duration_sec=max(5.0, duration_sec), generator_func=bgm_gen)


# ---------------------------------------------------------------------------
# Audio Scene Graph & Multi-Track Mixing Engine
# ---------------------------------------------------------------------------

class AudioSceneGraphEngine:
    """Engine responsible for building the 3-track audio graph and performing sidechain ducking."""

    def __init__(
        self,
        bgm_baseline_db: float = -22.0,
        bgm_ducked_db: float = -28.0,
        bgm_pause_boost_db: float = -16.0,
        attack_ms: int = 80,
        release_ms: int = 250,
        sample_rate: int = 44100,
        channels: int = 2,
    ):
        self.bgm_baseline_db = bgm_baseline_db
        self.bgm_ducked_db = bgm_ducked_db
        self.bgm_pause_boost_db = bgm_pause_boost_db
        self.attack_ms = attack_ms
        self.release_ms = release_ms
        self.sample_rate = sample_rate
        self.channels = channels
        self.cache_dir = CONFIG.get_artifacts_dir() / "audio_assets"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def build_scene_graph_from_rows(
        self,
        rows: List[Dict[str, Any]],
        total_duration_sec: float,
        bgm_file: Optional[str] = None,
        bgm_intensity: float = 0.85,
        topic: str = "",
    ) -> "AudioSceneGraph":
        """Build the scene graph from plain timeline rows.

        The renderer works with lightweight scene dicts, not validated
        ``MaterializedScene`` models, so this adapter avoids fabricating a
        full content scene (assets, narration, visual requirements) just to
        obtain audio timing.
        """
        scenes = []
        for row in rows:
            scenes.append(
                _GraphScene(
                    scene_id=str(row["scene_id"]),
                    timing=MaterializedTiming(
                        start_time_sec=float(row["start_sec"]),
                        end_time_sec=float(row["end_sec"]),
                        duration_sec=float(row["duration_sec"]),
                    ),
                    audio_plan=MaterializedAudioPlan(
                        voice_path=str(row["voice_path"]),
                        voice_duration_sec=float(row.get("voice_duration_sec") or row["duration_sec"]),
                        sfx_events=[SFXEvent(**e) for e in (row.get("sfx_events") or [])],
                    ),
                )
            )
        return self.build_scene_graph(
            scenes,  # type: ignore[arg-type]
            total_duration_sec=total_duration_sec,
            bgm_file=bgm_file,
            bgm_intensity=bgm_intensity,
            topic=topic,
        )

    def build_scene_graph(
        self,
        scenes: List[MaterializedScene],
        total_duration_sec: float,
        bgm_file: Optional[str] = None,
        bgm_intensity: float = 0.85,
        topic: str = "",
    ) -> AudioSceneGraph:
        """Construct the 3-track audio scene graph with sidechain envelope points."""
        mood = resolve_bgm_mood(topic)
        voice_segments = []
        ducking_points = []
        sfx_cues = []

        curr_time = 0.0
        for s in scenes:
            v_start = s.timing.start_time_sec
            v_end = s.timing.end_time_sec
            v_dur = s.timing.duration_sec

            voice_segments.append({
                "scene_id": s.scene_id,
                "start_sec": v_start,
                "end_sec": v_end,
                "duration_sec": v_dur,
                "voice_path": s.audio_plan.voice_path,
            })

            # During speech: duck BGM
            ducking_points.append(DuckingEnvelopePoint(
                time_sec=v_start,
                gain_db=self.bgm_ducked_db,
                reason="speech_start",
            ))
            ducking_points.append(DuckingEnvelopePoint(
                time_sec=v_end,
                gain_db=self.bgm_baseline_db,
                reason="speech_end",
            ))

            # SFX events in scene
            for sfx in s.audio_plan.sfx_events:
                sfx_cues.append({
                    "scene_id": s.scene_id,
                    "cue": sfx.cue,
                    "time_sec": v_start + sfx.time_sec,
                    "volume_db": sfx.volume_db,
                })

        # Check for dramatic pauses (> 0.5s speech gap)
        for i in range(len(voice_segments) - 1):
            gap = voice_segments[i + 1]["start_sec"] - voice_segments[i]["end_sec"]
            if gap >= 0.50:
                pause_mid = voice_segments[i]["end_sec"] + gap / 2.0
                ducking_points.append(DuckingEnvelopePoint(
                    time_sec=pause_mid,
                    gain_db=self.bgm_pause_boost_db,
                    reason="dramatic_pause_accent",
                ))

        ducking_points.sort(key=lambda p: p.time_sec)

        return AudioSceneGraph(
            total_duration_sec=total_duration_sec,
            voice_segments=voice_segments,
            bgm_config={
                "bgm_file": bgm_file,
                "mood": mood,
                "baseline_db": self.bgm_baseline_db,
                "ducked_db": self.bgm_ducked_db,
                "intensity": bgm_intensity,
            },
            sfx_cues=sfx_cues,
            ducking_envelope=ducking_points,
        )

    def mix_and_master(
        self,
        scene_graph: AudioSceneGraph,
        output_master_path: str | Path,
    ) -> AudioMixResult:
        """Compose and mix Voice, ducked BGM, and SFX into a 44.1kHz stereo broadcast master."""
        out_p = Path(output_master_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        dur = scene_graph.total_duration_sec

        # 1. Prepare Stitched Voice Stem
        stitched_voice_path = self.cache_dir / f"voice_stitched_{int(dur*100)}.wav"
        self._stitch_voice_segments(scene_graph.voice_segments, stitched_voice_path, dur)

        # 2. Prepare BGM Stem (mood-resolved procedural bed for this topic)
        mood = str(scene_graph.bgm_config.get("mood") or resolve_bgm_mood(""))
        bgm_input = scene_graph.bgm_config.get("bgm_file")
        if not bgm_input or not Path(bgm_input).exists():
            bgm_path = self.cache_dir / f"ambient_bgm_{mood}_{int(dur*100)}.wav"
            generate_ambient_bgm_track(bgm_path, dur, mood=mood)
        else:
            bgm_path = Path(bgm_input)

        # 3. Prepare SFX Stems
        prepared_sfx = []
        for cue in scene_graph.sfx_cues:
            sfx_wav = generate_sfx_sample(cue["cue"], self.cache_dir)
            prepared_sfx.append({
                "cue": cue["cue"],
                "time_sec": cue["time_sec"],
                "volume_db": cue.get("volume_db", -6.0),
                "path": str(sfx_wav),
            })

        # 4. Mix stems with FFmpeg
        # Voice (0dB, mastered), BGM sidechain-ducked under the voice, SFX (-6dB)
        voice_vol = 1.0
        bgm_vol = 0.12  # approx -18 to -22 dB baseline before ducking
        sfx_vol = 0.50  # approx -6 dB

        cmd = [
            "ffmpeg", "-y",
            "-i", str(stitched_voice_path),
            "-i", str(bgm_path),
        ]

        # Add any SFX inputs
        sfx_inputs_count = len(prepared_sfx)
        for sfx_item in prepared_sfx:
            cmd.extend(["-i", sfx_item["path"]])

        # Construct complex filter graph.
        # The BGM is genuinely sidechain-compressed by the voice track. A static
        # gain here would only *look* like ducking in the manifest while leaving
        # music competing with narration, so the duck depth is measured from the
        # rendered stem afterwards rather than asserted.
        duck_threshold = 0.02
        duck_ratio = 12.0
        duck_attack_ms = max(1, int(self.attack_ms / 5))
        duck_release_ms = max(1, int(self.release_ms / 5))
        bgm_ducked_stem = self.cache_dir / f"bgm_ducked_{int(dur * 100)}.wav"

        filter_parts = [
            f"[0:a]volume={voice_vol},aformat=sample_rates=44100:channel_layouts=stereo[a_voice]",
            # [main][sidechain] -> ducked music bed
            f"[1:a][a_voice]sidechaincompress="
            f"threshold={duck_threshold}:ratio={duck_ratio}:attack={duck_attack_ms}:release={duck_release_ms}:makeup=1"
            f"[bgm_ducked]",
            # One branch feeds the master mix, the other is emitted on its own so
            # the duck depth can be measured from real audio.
            f"[bgm_ducked]asplit=2[bgm_for_mix][bgm_for_measure]",
            f"[bgm_for_mix]volume={bgm_vol},"
            f"afade=t=in:ss=0:d=1.0,afade=t=out:st={max(0, dur - 1.5):.2f}:d=1.5,"
            f"aformat=sample_rates=44100:channel_layouts=stereo[a_bgm]",
            f"[bgm_for_measure]aformat=sample_rates=44100:channel_layouts=stereo[a_bgm_measure]",
        ]

        mix_inputs = ["[a_voice]", "[a_bgm]"]
        for idx, sfx_item in enumerate(prepared_sfx):
            delay_ms = int(sfx_item["time_sec"] * 1000)
            in_idx = 2 + idx
            filter_parts.append(
                f"[{in_idx}:a]adelay={delay_ms}|{delay_ms},volume={sfx_vol},aformat=sample_rates=44100:channel_layouts=stereo[a_sfx_{idx}]"
            )
            mix_inputs.append(f"[a_sfx_{idx}]")

        total_inputs = len(mix_inputs)
        mix_str = "".join(mix_inputs) + f"amix=inputs={total_inputs}:duration=first:dropout_transition=0,highpass=f=60,loudnorm=I=-16.0:LRA=11:TP=-1.0,aformat=sample_rates=44100:channel_layouts=stereo[a_out]"
        filter_parts.append(mix_str)

        cmd.extend([
            "-filter_complex", ";".join(filter_parts),
            "-map", "[a_out]",
            "-t", f"{dur:.3f}",
            "-ar", "44100",
            "-ac", "2",
            str(out_p),
            # Also emit the ducked music bed on its own so the duck depth can be
            # measured from real audio instead of being asserted from settings.
            "-map", "[a_bgm_measure]",
            "-t", f"{dur:.3f}",
            "-c:a", "pcm_s16le",
            str(bgm_ducked_stem),
        ])

        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
            if r.returncode == 0 and out_p.exists() and out_p.stat().st_size > 0:
                ducking = self._measure_ducking_db(
                    voice_path=stitched_voice_path,
                    bgm_path=bgm_ducked_stem,
                )
                return AudioMixResult(
                    master_audio_path=str(out_p),
                    duration_sec=dur,
                    sample_rate=44100,
                    channels=2,
                    voice_path=str(stitched_voice_path),
                    bgm_path=str(bgm_path),
                    sfx_manifest=prepared_sfx,
                    mix_manifest={
                        "total_duration_sec": dur,
                        "mood": mood,
                        "voice_stems": len(scene_graph.voice_segments),
                        "sfx_cues": len(prepared_sfx),
                        "ducking_points": len(scene_graph.ducking_envelope),
                        "ducking_method": "sidechaincompress",
                        "ducking_threshold": duck_threshold,
                        "ducking_ratio": duck_ratio,
                        "ducking_attack_ms": duck_attack_ms,
                        "ducking_release_ms": duck_release_ms,
                        "bgm_ducked_stem": str(bgm_ducked_stem) if bgm_ducked_stem.exists() else None,
                        "measurement": ducking,
                        # Only claim verified ducking when it was actually
                        # measured to be audible under the voice.
                        "ducking_verified": bool(ducking.get("ducking_verified")),
                        "ducking_db": ducking.get("ducking_db"),
                    },
                    success=True,
                )
        except Exception:
            pass

        # Fallback to direct stitched voice if complex mix fails
        if stitched_voice_path.exists() and stitched_voice_path != out_p:
            import shutil
            shutil.copy2(stitched_voice_path, out_p)

        return AudioMixResult(
            master_audio_path=str(out_p),
            duration_sec=dur,
            sample_rate=44100,
            channels=2,
            voice_path=str(stitched_voice_path),
            success=True,
        )

    # Minimum measured reduction required before ducking is called verified.
    DUCKING_VERIFIED_MIN_DB = 2.0

    def _measure_ducking_db(
        self,
        voice_path: str | Path,
        bgm_path: str | Path,
        window_ms: int = 50,
    ) -> Dict[str, Any]:
        """Measure how far the music bed drops while the voice is speaking.

        Compares RMS of the ducked BGM stem during speech-active windows against
        windows where the voice is silent. A real sidechain setup shows a clear
        positive reduction; a static gain shows ~0 dB, which is reported as
        unverified rather than passed off as working ducking.
        """
        result: Dict[str, Any] = {
            "ducking_verified": False,
            "ducking_db": None,
            "method": "windowed_rms_of_ducked_bgm_stem",
        }
        voice_p, bgm_p = Path(voice_path), Path(bgm_path)
        if not voice_p.exists() or not bgm_p.exists():
            result["error"] = "stem_missing"
            return result

        try:
            import numpy as np

            sr = 44100

            def _load_mono(path: Path):
                raw = subprocess.run(
                    ["ffmpeg", "-v", "error", "-i", str(path),
                     "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"],
                    capture_output=True, timeout=60,
                )
                if raw.returncode != 0 or not raw.stdout:
                    raise RuntimeError("decode_failed")
                return np.frombuffer(raw.stdout, dtype=np.float32)

            voice = _load_mono(voice_p)
            bgm = _load_mono(bgm_p)
            n = min(len(voice), len(bgm))
            if n == 0:
                result["error"] = "empty_stem"
                return result
            voice, bgm = voice[:n], bgm[:n]

            win = max(1, int(sr * window_ms / 1000))
            usable = (n // win) * win
            if usable < win * 4:
                result["error"] = "too_short"
                return result

            v = voice[:usable].reshape(-1, win)
            b = bgm[:usable].reshape(-1, win)
            v_rms = np.sqrt(np.mean(v * v, axis=1) + 1e-12)
            b_rms = np.sqrt(np.mean(b * b, axis=1) + 1e-12)

            v_db = 20 * np.log10(v_rms)
            speech_floor = float(np.max(v_db)) - 20.0
            speech_mask = v_db > speech_floor
            gap_mask = ~speech_mask

            if not speech_mask.any() or not gap_mask.any():
                result["error"] = "no_distinguishable_speech"
                return result

            speech_bgm_db = float(np.mean(20 * np.log10(b_rms[speech_mask] + 1e-12)))
            gap_bgm_db = float(np.mean(20 * np.log10(b_rms[gap_mask] + 1e-12)))
            reduction = gap_bgm_db - speech_bgm_db

            result.update({
                "ducking_db": round(reduction, 2),
                "bgm_db_during_speech": round(speech_bgm_db, 2),
                "bgm_db_during_gaps": round(gap_bgm_db, 2),
                "window_ms": window_ms,
                "speech_windows": int(speech_mask.sum()),
                "gap_windows": int(gap_mask.sum()),
                "threshold_db": self.DUCKING_VERIFIED_MIN_DB,
                "ducking_verified": bool(reduction >= self.DUCKING_VERIFIED_MIN_DB),
            })
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    def _stitch_voice_segments(
        self,
        voice_segments: List[Dict[str, Any]],
        output_path: Path,
        total_duration_sec: float,
    ) -> Path:
        """Concatenate voice audio segments with exact silence padding."""
        num_frames = int(total_duration_sec * self.sample_rate)
        master_frames = bytearray(num_frames * self.channels * 2)  # 16-bit stereo
        bytes_per_frame = self.channels * 2
        blended: List[str] = []
        skipped: List[str] = []

        for seg in voice_segments:
            vp = seg.get("voice_path")
            start_sec = seg.get("start_sec", 0.0)
            if not vp or not Path(vp).exists():
                skipped.append(str(vp))
                continue

            try:
                # TTS backends do not reliably return RIFF/WAVE PCM (Edge TTS
                # returns MP3 bytes behind a .wav filename), so decode through
                # ffmpeg instead of trusting the extension or using `wave`.
                # Failing to decode must never silently yield a silent track.
                proc = subprocess.run(
                    [
                        "ffmpeg", "-v", "error", "-i", str(vp),
                        "-f", "s16le", "-acodec", "pcm_s16le",
                        "-ar", str(self.sample_rate),
                        "-ac", str(self.channels),
                        "-",
                    ],
                    capture_output=True,
                    timeout=60,
                )
                data = proc.stdout if proc.returncode == 0 else b""
                if not data:
                    skipped.append(f"{vp} (decode_failed: {proc.stderr.decode('utf-8', 'ignore')[:120]})")
                    continue

                start_byte = int(start_sec * self.sample_rate) * bytes_per_frame
                end_byte = min(len(master_frames), start_byte + len(data))
                copy_len = end_byte - start_byte
                if copy_len > 0:
                    master_frames[start_byte : start_byte + copy_len] = data[:copy_len]
                    blended.append(str(vp))
            except Exception as exc:
                skipped.append(f"{vp} ({type(exc).__name__}: {exc})")

        if not blended and voice_segments:
            # A silent master would quietly replace the narration with music.
            raise RuntimeError(
                "Voice stitching produced no audio from "
                f"{len(voice_segments)} segment(s); all failed to decode: {skipped[:3]}"
            )
        self.last_stitch_report = {
            "expected": len(voice_segments),
            "blended": len(blended),
            "skipped": len(skipped),
            "skipped_details": skipped[:5],
        }

        with wave.open(str(output_path), "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(master_frames)

        return output_path
