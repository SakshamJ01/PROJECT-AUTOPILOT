"""Regression tests for the VOICE + BGM + SFX master mix and its QA evidence.

Three real defects motivated this file:

* ``_stitch_voice_segments`` used ``wave.open`` and silently skipped anything
  that was not 44.1 kHz stereo RIFF/WAVE. Edge TTS returns MP3 bytes behind a
  ``.wav`` filename, so *every* voice segment was dropped and the "master mix"
  was music over pure digital silence. QA then reported "voice tracks present"
  because the timeline listed paths that were never actually mixed in.
* The BGM was mixed with a *static* gain while the manifest advertised
  sidechain ducking, so the claimed ducking was never applied or measured.
* The renderer discarded the mix result and its errors, so nothing recorded
  whether the mix ran, leaving QA unable to verify ducking at all.

Fixtures here are synthetic audio generated with ffmpeg. They are test inputs
only and are never treated as production evidence.
"""
import json
import subprocess
import wave
from pathlib import Path

import pytest

from autopilot.core.audio_scene_graph import AudioSceneGraphEngine
from autopilot.core.renderer import _apply_audio_scene_graph_mix

SR = 44100


def _ffmpeg(args, timeout=120):
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", *args], capture_output=True, text=True, timeout=timeout)
    assert r.returncode == 0, r.stderr
    return r


def _write_wav(path: Path, data):
    """Write float32 mono/stereo samples as 16-bit stereo WAV."""
    import numpy as np

    arr = np.asarray(data, dtype="float64")
    if arr.ndim == 1:
        arr = np.stack([arr, arr], axis=1)
    pcm = np.clip(arr, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())


def _speechy_voice(path: Path, dur=4.0, tone=440.0):
    """Voice-like: 0.5s tone bursts separated by true digital silence.

    Synthesized directly in numpy so the speech/gap structure is exact. Note
    that ffmpeg's ``volume=enable=...`` cannot build this: ``enable`` merely
    skips the filter (leaving the previous gain) instead of gating to zero.
    """
    import numpy as np

    t = np.arange(int(SR * dur)) / SR
    slot = (t % 1.0) < 0.5
    sig = np.where(slot, 0.5 * np.sin(2 * np.pi * tone * t), 0.0)
    _write_wav(path, sig)


def _tone_bgm(path: Path, dur=4.0, gate=False, speech_gain=0.02, gap_gain=0.2):
    """Constant BGM, or one attenuated exactly while the voice is speaking.

    The gate is aligned to the 0.5s speech/silence slots produced by
    ``_speechy_voice`` so the measurement sees a true sidechain-like bed.
    """
    import numpy as np

    t = np.arange(int(SR * dur)) / SR
    sig = gap_gain * np.sin(2 * np.pi * 120.0 * t)
    if gate:
        speech = (t % 1.0) < 0.5
        sig = np.where(speech, sig * (speech_gain / gap_gain), sig)
    _write_wav(path, sig)


def _write_wav_mp3_bytes(path: Path, dur=1.0):
    """A file named .wav that is actually MP3 - what Edge TTS hands us."""
    # -f mp3 is required: the .wav extension would otherwise select the WAV
    # muxer and quietly hand back RIFF/PCM, defeating the regression.
    _ffmpeg([
        "-f", "lavfi", "-i", f"sine=frequency=600:sample_rate=24000:duration={dur}",
        "-ac", "1", "-c:a", "libmp3lame", "-f", "mp3", str(path),
    ])
    raw = path.read_bytes()
    assert not raw.startswith(b"RIFF"), "fixture must not be RIFF/WAVE"


class TestVoiceStitching:
    def test_stitches_mp3_bytes_named_wav(self, tmp_path):
        """The silence bug: a non-RIFF .wav must still land in the master."""
        seg = tmp_path / "segment_01.wav"
        _write_wav_mp3_bytes(seg)
        out = tmp_path / "stitched.wav"

        eng = AudioSceneGraphEngine()
        eng._stitch_voice_segments(
            [{"scene_id": "s1", "voice_path": str(seg), "start_sec": 0.0}],
            out, 1.0,
        )

        report = eng.last_stitch_report
        assert report["expected"] == 1
        assert report["blended"] == 1, report
        assert report["skipped"] == 0, report

        # The stitched track must contain real signal, not digital silence.
        with wave.open(str(out), "rb") as wf:
            frames = wf.readframes(wf.getnframes())
        assert any(b != 0 for b in frames), "stitched master is silent"

    def test_raises_when_every_segment_fails_to_decode(self, tmp_path):
        """Never silently ship a silent master over real narration."""
        bad = tmp_path / "garbage.wav"
        bad.write_bytes(b"not audio at all")
        out = tmp_path / "stitched.wav"

        with pytest.raises(RuntimeError, match="no audio"):
            AudioSceneGraphEngine()._stitch_voice_segments(
                [{"scene_id": "s1", "voice_path": str(bad), "start_sec": 0.0}],
                out, 1.0,
            )


class TestDuckingMeasurement:
    def _segments(self, tmp_path, dur=4.0):
        voice = tmp_path / "voice.wav"
        _speechy_voice(voice, dur=dur)
        return [{
            "scene_id": "scene-01",
            "voice_path": str(voice),
            "start_sec": 0.0,
            "end_sec": dur,
            "duration_sec": dur,
        }]

    def _mixed_manifest(self, tmp_path, gate):
        voice = tmp_path / "voice.wav"
        bgm = tmp_path / "bgm.wav"
        _speechy_voice(voice, dur=4.0)
        _tone_bgm(bgm, dur=4.0, gate=gate)

        eng = AudioSceneGraphEngine()
        # Measure the ducked bed against the stitched voice the mixer produces.
        eng._stitch_voice_segments(
            [{"scene_id": "scene-01", "voice_path": str(voice), "start_sec": 0.0}],
            tmp_path / "v.wav", 4.0,
        )
        return eng._measure_ducking_db(
            voice_path=tmp_path / "v.wav", bgm_path=bgm
        )

    def test_flat_bgm_gain_is_reported_unverified(self, tmp_path):
        """A static gain is not ducking, and must not be reported as verified."""
        result = self._mixed_manifest(tmp_path, gate=False)
        assert result["ducking_db"] is not None
        assert result["ducking_db"] < AudioSceneGraphEngine.DUCKING_VERIFIED_MIN_DB
        assert result["ducking_verified"] is False

    def test_genuinely_ducked_bgm_is_measured_and_verified(self, tmp_path):
        result = self._mixed_manifest(tmp_path, gate=True)
        assert result["ducking_db"] >= AudioSceneGraphEngine.DUCKING_VERIFIED_MIN_DB
        assert result["ducking_verified"] is True
        assert result["bgm_db_during_speech"] < result["bgm_db_during_gaps"]

    def test_missing_stem_reports_error_not_verified(self, tmp_path):
        eng = AudioSceneGraphEngine()
        result = eng._measure_ducking_db(
            voice_path=tmp_path / "nope.wav", bgm_path=tmp_path / "nope2.wav"
        )
        assert result["ducking_verified"] is False
        assert result["ducking_db"] is None
        assert result["error"] == "stem_missing"


class TestRendererManifestPersistence:
    def test_mix_persists_manifest_with_measured_ducking(self, tmp_path):
        """The render must leave auditable evidence QA can actually read."""
        voice = tmp_path / "voice.wav"
        _speechy_voice(voice, dur=4.0)

        # <job>/render/final.mp4 layout drives where the manifest lands.
        job = tmp_path / "job"
        render_dir = job / "render"
        render_dir.mkdir(parents=True)
        out_video = render_dir / "final.mp4"
        _ffmpeg([
            "-f", "lavfi", "-i", "color=c=black:s=360x640:d=4:r=25",
            "-pix_fmt", "yuv420p", str(out_video),
        ])

        scenes = [{
            "scene_id": "scene-01",
            "duration_sec": 4.0,
            "audio_path": str(voice),
            "word_timestamps": [],
        }]
        work = tmp_path / "work"
        work.mkdir()

        manifest = _apply_audio_scene_graph_mix(
            scenes, [str(voice)], out_video, work
        )

        assert manifest.get("applied") is True
        assert manifest["ducking_verified"] is True
        assert manifest["ducking_db"] >= AudioSceneGraphEngine.DUCKING_VERIFIED_MIN_DB
        assert manifest["mix_manifest"]["ducking_method"] == "sidechaincompress"

        # Evidence is on disk where Creative QA looks for it.
        persisted = json.loads(
            (job / "audio" / "audio_mix_manifest.json").read_text(encoding="utf-8")
        )
        assert persisted["ducking_verified"] is True
        assert persisted["ducking_db"] == manifest["ducking_db"]

    def test_no_segments_yields_no_manifest(self, tmp_path):
        out_video = tmp_path / "final.mp4"
        out_video.write_bytes(b"x")
        assert _apply_audio_scene_graph_mix([], [], out_video, tmp_path) == {}


class TestSceneGraphFromRows:
    def test_rows_adapter_builds_graph_without_full_scene_model(self, tmp_path):
        """The renderer must not need a complete validated content scene."""
        rows = [{
            "scene_id": "scene-01",
            "start_sec": 0.0,
            "end_sec": 4.0,
            "duration_sec": 4.0,
            "voice_path": str(tmp_path / "v.wav"),
            "voice_duration_sec": 4.0,
            "sfx_events": [{"cue": "whoosh_fast", "time_sec": 0.0, "volume_db": -6.0}],
        }]
        graph = AudioSceneGraphEngine().build_scene_graph_from_rows(rows, 4.0)
        assert len(graph.voice_segments) == 1
        assert graph.voice_segments[0]["voice_path"].endswith("v.wav")
        assert len(graph.sfx_cues) == 1
        assert len(graph.ducking_envelope) == 2
