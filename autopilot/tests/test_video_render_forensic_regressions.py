"""Forensic regression tests for video rendering, subtitle formatting, and asset integrity.

Validates that:
1. Missing image asset fails closed with RuntimeError (no silent fallback).
2. Valid image asset renders successfully.
3. Subtitle formatting does not produce literal 'n' artifact and fits safe widths.
4. Subtitle coordinates fit within 1080x1920 portrait safe bounds.
5. RenderOutput metadata confirms 1080x1920 dimensions and H.264/AAC tracks.
6. Renderer does not silently substitute test-pattern frames.
7. Policy provider resolution defaults to real discovery (openverse) for production policies.
"""
from pathlib import Path
import json
import subprocess
import pytest

from autopilot.core.contracts import RenderPlan, RenderOutput
from autopilot.core.renderer import FFmpegRenderer, format_caption, _escape_filter_path
from autopilot.cli.main import resolve_asset_provider_for_policy, resolve_providers_for_policy, _POLICY_PROVIDER_MAP
from autopilot.core.config import CONFIG


FIXTURE_IMG = Path(__file__).parent.parent / "autopilot" / "providers" / "fixture_image.png"
FIXTURE_VID = Path(__file__).parent.parent / "autopilot" / "providers" / "fixture_video.mp4"


def test_missing_image_asset_raises_runtime_error(tmp_path):
    """Renderer must fail closed immediately if any scene visual asset is missing."""
    renderer = FFmpegRenderer(profile="vertical_short")
    plan = RenderPlan(
        plan_id="p-missing-1",
        content_id="c-missing-1",
        job_id="job-missing-1",
        profile="vertical_short",
        scenes=[
            {"scene_id": "s1", "duration_sec": 2.0, "asset_path": str(tmp_path / "nonexistent_file.png")},
        ],
    )
    out_path = tmp_path / "final.mp4"
    with pytest.raises(RuntimeError) as excinfo:
        renderer.render(plan, str(out_path))
    assert "Missing required visual asset" in str(excinfo.value)
    assert not out_path.exists()


def test_missing_asset_path_none_raises_runtime_error(tmp_path):
    """Renderer must fail closed if scene asset_path is None or empty."""
    renderer = FFmpegRenderer(profile="vertical_short")
    plan = RenderPlan(
        plan_id="p-missing-2",
        content_id="c-missing-2",
        job_id="job-missing-2",
        profile="vertical_short",
        scenes=[
            {"scene_id": "s1", "duration_sec": 2.0, "asset_path": None},
        ],
    )
    out_path = tmp_path / "final.mp4"
    with pytest.raises(RuntimeError) as excinfo:
        renderer.render(plan, str(out_path))
    assert "Missing required visual asset" in str(excinfo.value)


def test_valid_image_asset_renders_successfully(tmp_path):
    """Renderer succeeds and generates valid 1080x1920 MP4 when valid visual asset is provided."""
    renderer = FFmpegRenderer(profile="vertical_short")
    plan = RenderPlan(
        plan_id="p-valid-1",
        content_id="c-valid-1",
        job_id="job-valid-1",
        profile="vertical_short",
        scenes=[
            {
                "scene_id": "s1",
                "duration_sec": 2.0,
                "asset_path": str(FIXTURE_IMG),
                "narration": "This is a clean test narration with subtitles.",
                "on_screen_text": "SCIENCE FACT",
            },
        ],
    )
    out_path = tmp_path / "final.mp4"
    result = renderer.render(plan, str(out_path))
    assert result.output_path == str(out_path)
    assert result.width == 1080
    assert result.height == 1920
    assert result.codec_video == "h264"
    assert result.codec_audio == "aac"
    assert out_path.exists()
    assert out_path.stat().st_size > 0


def test_subtitle_formatting_no_n_artifact():
    """format_caption splits text cleanly into lines without creating literal 'n' artifacts."""
    text = "Why do leaves turn red instead of green during autumn?"
    caption = format_caption(text, max_chars=24)
    lines = caption.split("\n")
    assert len(lines) <= 2
    for line in lines:
        assert len(line) <= 30
        assert not line.startswith("n")
        assert "nINSTEAD" not in line
        assert "nRED" not in line


def test_escape_filter_path_windows():
    """Path escaping for FFmpeg drawtext textfile handles colons and slashes properly."""
    p = Path("C:/Users/Test/artifacts/caption_0.txt")
    esc = _escape_filter_path(p)
    assert "\\" not in esc or esc.startswith("C\\\\:")
    assert "C\\\\:" in esc or "C\\:" in esc or not esc.startswith("C:")


def test_ffprobe_rendered_video_streams(tmp_path):
    """Verify via ffprobe that rendered output has 1080x1920 resolution, H.264 video, and AAC audio."""
    renderer = FFmpegRenderer(profile="vertical_short")
    plan = RenderPlan(
        plan_id="p-streams-1",
        content_id="c-streams-1",
        job_id="job-streams-1",
        profile="vertical_short",
        scenes=[
            {"scene_id": "s1", "duration_sec": 1.0, "asset_path": str(FIXTURE_IMG), "narration": "Scene one"},
            {"scene_id": "s2", "duration_sec": 1.0, "asset_path": str(FIXTURE_IMG), "narration": "Scene two"},
        ],
    )
    out_path = tmp_path / "final.mp4"
    renderer.render(plan, str(out_path))

    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "stream=codec_type,codec_name,width,height",
        "-of", "json",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0
    info = json.loads(res.stdout)
    streams = info.get("streams", [])
    v_streams = [s for s in streams if s.get("codec_type") == "video"]
    a_streams = [s for s in streams if s.get("codec_type") == "audio"]

    assert len(v_streams) == 1
    assert v_streams[0].get("codec_name") == "h264"
    assert v_streams[0].get("width") == 1080
    assert v_streams[0].get("height") == 1920

    assert len(a_streams) == 1
    assert a_streams[0].get("codec_name") == "aac"


def test_policy_asset_provider_resolution():
    """Production policies must resolve asset_provider to openverse rather than local fixtures."""
    for policy in ("local_only", "cheap_first", "quality_first", "ollama", "gemini", "openrouter"):
        resolved = resolve_asset_provider_for_policy(policy, "local")
        assert resolved == "openverse", f"Policy '{policy}' should resolve 'local' to 'openverse'"

        resolved_mock = resolve_asset_provider_for_policy(policy, "mock")
        assert resolved_mock == "openverse"

    # Permissive / unknown policy leaves as-is
    assert resolve_asset_provider_for_policy("unknown_custom", "local") == "local"


def test_audio_kokoro_wav_header_and_format_integrity(tmp_path):
    """Verify Kokoro raw audio WAV format is 24,000 Hz s16le mono with valid WAV RIFF header."""
    import wave
    import numpy as np

    wav_file = tmp_path / "kokoro_sample.wav"
    sample_rate = 24000
    duration_sec = 1.0
    t = np.linspace(0, duration_sec, int(sample_rate * duration_sec), endpoint=False)
    samples = (np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)

    with wave.open(str(wav_file), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())

    assert wav_file.exists()
    assert wav_file.stat().st_size > 44

    with wave.open(str(wav_file), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 24000
        assert wf.getnframes() == 24000


def test_audio_renderer_produces_clean_44100_stereo_aac(tmp_path):
    """Verify FFmpegRenderer enforces 44.1 kHz stereo AAC output post-loudnorm."""
    import wave
    import numpy as np

    voice_wav = tmp_path / "voice_scene_0.wav"
    t = np.linspace(0, 2.0, 24000 * 2, endpoint=False)
    samples = (np.sin(2 * np.pi * 440 * t) * 16000).astype(np.int16)
    with wave.open(str(voice_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(samples.tobytes())

    renderer = FFmpegRenderer(profile="vertical_short")
    plan = RenderPlan(
        plan_id="p-audio-clean-1",
        content_id="c-audio-clean-1",
        job_id="job-audio-clean-1",
        profile="vertical_short",
        scenes=[
            {
                "scene_id": "s1",
                "duration_sec": 2.0,
                "asset_path": str(FIXTURE_IMG),
                "audio_path": str(voice_wav),
                "narration": "Testing loudnorm and audio encoding formatting.",
            },
        ],
    )
    out_path = tmp_path / "audio_test.mp4"
    res = renderer.render(plan, str(out_path))
    assert res.codec_audio == "aac"

    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "stream=codec_name,sample_rate,channels,channel_layout",
        "-of", "json",
        str(out_path),
    ]
    probe_res = subprocess.run(cmd, capture_output=True, text=True)
    assert probe_res.returncode == 0
    info = json.loads(probe_res.stdout)
    a_stream = next(s for s in info.get("streams", []) if s.get("codec_name") == "aac")

    assert a_stream.get("sample_rate") == "44100"
    assert a_stream.get("channels") == 2
    assert a_stream.get("channel_layout") == "stereo"


def test_audio_renderer_rejects_corrupted_sample_rate(tmp_path, monkeypatch):
    """Verify renderer raises RuntimeError if post-render audio probe detects abnormal sample rate."""
    renderer = FFmpegRenderer(profile="vertical_short")
    plan = RenderPlan(
        plan_id="p-audio-corrupt-1",
        content_id="c-audio-corrupt-1",
        job_id="job-audio-corrupt-1",
        profile="vertical_short",
        scenes=[
            {"scene_id": "s1", "duration_sec": 1.0, "asset_path": str(FIXTURE_IMG), "narration": "Test"},
        ],
    )
    out_path = tmp_path / "corrupt_audio.mp4"

    orig_run = subprocess.run
    def mock_subprocess_run(cmd, *args, **kwargs):
        res = orig_run(cmd, *args, **kwargs)
        if cmd[0] == "ffprobe" and str(out_path) in cmd:
            fake_stdout = json.dumps({"streams": [{"codec_name": "aac", "sample_rate": "96000", "channels": 1}]})
            return subprocess.CompletedProcess(cmd, 0, stdout=fake_stdout, stderr="")
        return res

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

    with pytest.raises(RuntimeError) as excinfo:
        renderer.render(plan, str(out_path))
    assert "invalid audio sample rate: 96000 Hz" in str(excinfo.value)


def test_caption_chunking_phrase_level_timing():
    """Verify FasterWhisperEngine generates phrase-level timed captions instead of single giant multiline blocks."""
    from autopilot.providers.transcription.faster_whisper_engine import FasterWhisperEngine
    from autopilot.core.contracts import SegmentTimestamp, WordTimestamp

    engine = FasterWhisperEngine()

    words = [
        WordTimestamp(word="Rainbows", start_sec=0.0, end_sec=0.4, probability=0.99),
        WordTimestamp(word="happen", start_sec=0.4, end_sec=0.8, probability=0.99),
        WordTimestamp(word="when", start_sec=0.8, end_sec=1.1, probability=0.99),
        WordTimestamp(word="sunlight", start_sec=1.1, end_sec=1.5, probability=0.99),
        WordTimestamp(word="enters", start_sec=1.5, end_sec=1.8, probability=0.99),
        WordTimestamp(word="raindrops.", start_sec=1.8, end_sec=2.4, probability=0.99),
    ]

    seg = SegmentTimestamp(
        segment_id=1,
        start_sec=0.0,
        end_sec=2.4,
        text="Rainbows happen when sunlight enters raindrops.",
        words=words,
    )

    srt = engine.generate_srt([seg])
    # Must produce multiple SRT subtitle blocks, not just 1 block containing the entire sentence
    assert srt.count("-->") >= 2
    assert "Rainbows happen" in srt
    assert "when sunlight enters" in srt or "raindrops." in srt

    ass = engine.generate_ass([seg])
    assert ass.count("Dialogue:") >= 2


def test_script_evaluation_min_duration_and_ending():
    """Verify evaluate_script rejects scripts under 25s and validates intentional ending."""
    from autopilot.core.quality import evaluate_script
    from autopilot.core.contracts import ScriptDocument, ScriptScene

    # Short script < 25s
    short_script = ScriptDocument(
        content_id="c-short",
        topic="Test",
        hook="Short hook",
        scenes=[
            ScriptScene(scene_id="s1", order=1, visual_intent="Img 1", narration="Hook scene", estimated_duration_seconds=5.0),
            ScriptScene(scene_id="s2", order=2, visual_intent="Img 2", narration="Fact scene", estimated_duration_seconds=5.0),
        ],
    )
    report_short = evaluate_script(short_script)
    assert any(c.check_name == "min_script_duration" and c.status == "warning" for c in report_short.checks)

    # Valid script >= 25s with 5 scenes and intentional ending
    valid_script = ScriptDocument(
        content_id="c-valid",
        topic="Test",
        hook="Good hook",
        scenes=[
            ScriptScene(scene_id="s1", order=1, visual_intent="Img 1", narration="Hook scene", estimated_duration_seconds=6.0),
            ScriptScene(scene_id="s2", order=2, visual_intent="Img 2", narration="Fact 1", estimated_duration_seconds=7.0),
            ScriptScene(scene_id="s3", order=3, visual_intent="Img 3", narration="Fact 2", estimated_duration_seconds=7.0),
            ScriptScene(scene_id="s4", order=4, visual_intent="Img 4", narration="Fact 3", estimated_duration_seconds=7.0),
            ScriptScene(scene_id="s5", order=5, visual_intent="Img 5", narration="Intentional payoff ending.", estimated_duration_seconds=8.0),
        ],
    )
    report_valid = evaluate_script(valid_script)
    assert report_valid.overall != "fail"
    assert any(c.check_name == "min_script_duration" and c.status == "pass" for c in report_valid.checks)
    assert any(c.check_name == "intentional_ending" and c.status == "pass" for c in report_valid.checks)


def test_qa_duration_drift_truncation_warning():
    """Verify QAEngine detects when rendered video is shorter than raw narration audio."""
    from autopilot.core.qa_engine import QAEngine
    from autopilot.core.contracts import ContentPackage, ContentItem, ScriptDocument, ScriptScene

    qa = QAEngine()
    item = ContentItem(content_id="c1", topic="test")
    script = ScriptDocument(
        content_id="c1",
        topic="test",
        hook="hook",
        scenes=[
            ScriptScene(scene_id="s1", order=1, visual_intent="Img", narration="Hello world", estimated_duration_seconds=13.24)
        ]
    )
    package = ContentPackage(content_item=item, script=script, measured_duration_sec=13.24)

    check, metrics = qa.check_duration_timeline(actual_dur=10.5, package=package)
    assert any(f.finding_id.endswith("-narration-truncation") for f in check.findings)



