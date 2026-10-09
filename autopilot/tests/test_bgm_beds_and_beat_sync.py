"""BGM beds + beat sync wiring (user-selected upgrade).

Covers:
  - ``resolve_bgm_source``: procedural fallback, mood-matched library
    selection, and topic-deterministic hash fallback.
  - ``_apply_audio_scene_graph_mix``: honours a configured library bed and
    persists ``bgm_source``/``bgm_file`` evidence for QA.
  - ``_resolve_scene_sfx``: beat-synced plan cues win; the positional
    fallback stays intact.
  - ``build_materialized_timeline``: the dormant BeatSyncEngine stamps
    narrative-role SFX and 2-4 word caption beats onto scenes with real word
    timestamps.
"""
import json
import subprocess
import wave
from pathlib import Path

from autopilot.core.audio_scene_graph import resolve_bgm_source
from autopilot.core.config import CONFIG
from autopilot.core.renderer import _apply_audio_scene_graph_mix, _resolve_scene_sfx


def _ffmpeg(args, timeout=120):
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", *args], capture_output=True, text=True, timeout=timeout)
    assert r.returncode == 0, r.stderr
    return r


def _write_wav_mono(path: Path, dur: float = 2.0, sr: int = 16000):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(b"\x00\x00" * int(sr * dur))


def _make_voice(path: Path, dur: float = 4.0):
    """Speech-like: 0.5s tone bursts separated by true digital silence."""
    import numpy as np

    sr = 44100
    t = np.arange(int(sr * dur)) / sr
    slot = (t % 1.0) < 0.5
    sig = np.where(slot, 0.5 * np.sin(2 * np.pi * 330 * t), 0.0)
    pcm = np.clip(np.stack([sig, sig], axis=1), -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def _render_frame(out_video: Path):
    _ffmpeg([
        "-f", "lavfi", "-i", "color=c=black:s=360x640:d=4:r=25",
        "-pix_fmt", "yuv420p", str(out_video),
    ])


class TestResolveBgmSource:
    def test_procedural_when_no_library_or_missing_dir(self, monkeypatch):
        monkeypatch.setattr(CONFIG, "bgm_library_dir", None)
        path, source = resolve_bgm_source("war stories")
        assert path is None
        assert source == "procedural"

        path, source = resolve_bgm_source("war stories", library_dir="/definitely/not/a/dir")
        assert path is None
        assert source == "procedural"

    def test_empty_library_falls_back_to_procedural(self, tmp_path, monkeypatch):
        empty = tmp_path / "empty_lib"
        empty.mkdir()
        monkeypatch.setattr(CONFIG, "bgm_library_dir", str(empty))
        path, source = resolve_bgm_source("mystery of the lost city")
        assert source == "procedural"
        assert path is None

    def test_mood_matched_library_file_selected(self, tmp_path, monkeypatch):
        for name in ("calm_lo-fi.wav", "bright_happy.m4a", "tense_motion.mp3"):
            (tmp_path / name).write_bytes(b"x")
        monkeypatch.setattr(CONFIG, "bgm_library_dir", str(tmp_path))

        path, source = resolve_bgm_source("the war of 1812, a brutal battle")
        assert source == "library"
        assert Path(path).name == "tense_motion.mp3"

        path2, _ = resolve_bgm_source("the war of 1812, a brutal battle")
        assert path == path2  # deterministic per topic

    def test_unknown_topic_hash_pick_is_deterministic(self, tmp_path, monkeypatch):
        for name in ("track_01.wav", "track_02.mp3", "track_03.flac"):
            (tmp_path / name).write_bytes(b"x")
        monkeypatch.setattr(CONFIG, "bgm_library_dir", str(tmp_path))

        p1, source1 = resolve_bgm_source("quantum coherent lattice states")
        p2, source2 = resolve_bgm_source("quantum coherent lattice states")
        assert source1 == source2 == "library"
        assert p1 == p2
        assert p1 is not None


class TestSceneSfxResolution:
    def test_plan_cues_win_over_positional(self):
        scene = {
            "scene_id": "s1",
            "sfx_events": [{"cue": "impact", "time_sec": 0.25, "volume_db": -4.0}],
        }
        assert _resolve_scene_sfx(scene, 0, 3) == [
            {"cue": "impact", "time_sec": 0.25, "volume_db": -4.0}
        ]

    def test_malformed_plan_cues_fall_back(self):
        scene = {"scene_id": "s1", "sfx_events": [{"cue": ""}, {"nope": 1}]}
        out = _resolve_scene_sfx(scene, 1, 3)
        assert out[0]["cue"] == "digital_pop"
        assert out[0]["volume_db"] == -9.0

    def test_positional_fallback_hook_middle_payoff(self):
        first = _resolve_scene_sfx({"word_timestamps": [{"start": 0.1}]}, 0, 3)
        assert first[0]["cue"] == "whoosh_fast"
        assert first[0]["time_sec"] == 0.1

        middle = _resolve_scene_sfx({}, 1, 3)
        assert middle[0]["cue"] == "digital_pop"
        assert middle[0]["volume_db"] == -9.0

        last = _resolve_scene_sfx({}, 2, 3)
        assert last[0]["cue"] == "bass_drop"
        assert last[0]["volume_db"] == -6.0


class TestRendererLibraryBgm:
    def test_mix_uses_library_bed_and_persists_evidence(self, tmp_path, monkeypatch):
        lib = tmp_path / "lib"
        lib.mkdir()
        bgm = lib / "calm_soft_mix.wav"
        _ffmpeg([
            "-f", "lavfi", "-i", "sine=frequency=180:sample_rate=44100:duration=6",
            "-ac", "2", "-c:a", "pcm_s16le", str(bgm),
        ])
        monkeypatch.setattr(CONFIG, "bgm_library_dir", str(lib))

        voice = tmp_path / "voice.wav"
        _make_voice(voice, dur=4.0)

        job = tmp_path / "job"
        (job / "render").mkdir(parents=True)
        out_video = job / "render" / "final.mp4"
        _render_frame(out_video)

        scenes = [{
            "scene_id": "scene-01",
            "duration_sec": 4.0,
            "audio_path": str(voice),
            "word_timestamps": [],
        }]
        work = tmp_path / "work"
        work.mkdir()

        manifest = _apply_audio_scene_graph_mix(scenes, [str(voice)], out_video, work, topic="quiet forest meditation")

        assert manifest.get("applied") is True
        assert manifest["bgm_source"] == "library"
        assert Path(manifest["bgm_file"]).name == "calm_soft_mix.wav"

        persisted = json.loads(
            (job / "audio" / "audio_mix_manifest.json").read_text(encoding="utf-8")
        )
        assert persisted["bgm_source"] == "library"
        assert persisted["bgm_file"].endswith("calm_soft_mix.wav")
        assert persisted.get("ducking_verified") is True


class TestBeatSyncWiredIntoTimeline:
    def _scenes_and_plan(self, tmp_path):
        assets = {}
        scripts = []
        plans = []
        for idx in range(1, 4):
            scene_id = f"scene_{idx}"
            asset = tmp_path / f"{scene_id}.png"
            asset.write_bytes(b"\x89PNG\r\n\x1a\n fake")
            voice = tmp_path / f"{scene_id}.wav"
            _write_wav_mono(voice)
            words = [
                {"word": w, "start": round(i * 0.3, 3), "end": round(i * 0.3 + 0.25, 3)}
                for i, w in enumerate("the ancient roman empire fell quickly".split())
            ]
            assets[scene_id] = words
            scripts.append(type("S", (), {
                "scene_id": scene_id,
                "narration": "the ancient roman empire fell quickly",
                "visual_intent": "roman ruins",
                "asset_query": "roman ruins",
                "transition_hint": None,
                "word_timestamps": words,
            })())
            plans.append({
                "scene_id": scene_id,
                "asset_path": str(asset),
                "audio_path": str(voice),
                "duration_sec": 3.0,
                "asset_type": "image",
            })
        return scripts, plans

    def test_beat_sync_stamps_role_sfx_and_caption_beats(self, tmp_path):
        from autopilot.core.timeline_builder import build_materialized_timeline

        scripts, plans = self._scenes_and_plan(tmp_path)
        timeline = build_materialized_timeline(
            job_id="job-beats",
            script=type("Script", (), {"scenes": scripts})(),
            plan_scenes=plans,
            topic="ancient romans",
        )

        # Narrative-role SFX (narrative role -> cue mapping in core.beat_sync).
        assert timeline.scenes[0].audio_plan.sfx_events[0].cue == "impact"  # HOOK
        assert timeline.scenes[1].audio_plan.sfx_events[0].cue == "digital_pop"  # CONTENT
        assert timeline.scenes[2].audio_plan.sfx_events[0].cue == "camera_shutter"  # CTA

        # Captions re-segmented into short 2-4 word beats.
        for scene in timeline.scenes:
            assert scene.caption_plan.phrases
            for phrase in scene.caption_plan.phrases:
                assert 0 < len(phrase.words) <= 4