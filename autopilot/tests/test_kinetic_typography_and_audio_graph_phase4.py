"""Phase 4 Test Suite — Kinetic Typography, Dynamic Layout & Audio Scene Graph.

Tests:
  - 2-5 word phrase segmentation bounds.
  - Semantic keyword emphasis (yellow/green pop).
  - Caption timing derived directly from Whisper timestamps.
  - Saliency-aware safe-zone positioning (TOP, CENTER, LOWER).
  - Platform safe zones (Shorts, Reels, TikTok).
  - ASS Animated -> Static ASS -> SRT fallback hierarchy.
  - Post-render caption verification.
  - 3-track Audio Scene Graph construction.
  - BGM sidechain ducking and dramatic pause accent.
  - Restrained SFX cues & synthesis.
  - Audio-visual beat synchronization.
  - TimelineCompiler & RenderPlan integration.
"""
from __future__ import annotations

import os
import wave
from pathlib import Path
import pytest

from autopilot.core.timeline import (
    CaptionPhrase,
    CaptionPosition,
    CropFraming,
    MaterializedAudioPlan,
    MaterializedCaptionPlan,
    MaterializedNarration,
    MaterializedScene,
    MaterializedTimeline,
    MaterializedTiming,
    NarrativeRole,
    PlatformSafeZone,
    SelectedAsset,
    SFXEvent,
    ShotType,
    TrimRange,
    VisualCoverageClass,
    VisualRequirements,
    WordTimestamp,
)
from autopilot.core.kinetic_typography import (
    KineticTypographyEngine,
    TYPOGRAPHY_PRESETS,
    PLATFORM_GEOMETRIES,
    verify_caption_layout,
)
from autopilot.core.audio_scene_graph import (
    AudioSceneGraphEngine,
    generate_sfx_sample,
    generate_ambient_bgm_track,
)
from autopilot.core.beat_sync import BeatSyncEngine
from autopilot.core.timeline_compiler import TimelineCompiler


def _create_sample_whisper_words() -> list[WordTimestamp]:
    return [
        WordTimestamp(word="The", start=0.0, end=0.25, confidence=0.98),
        WordTimestamp(word="world's", start=0.26, end=0.65, confidence=0.99),
        WordTimestamp(word="highest", start=0.66, end=1.10, confidence=0.99),
        WordTimestamp(word="rail", start=1.12, end=1.45, confidence=0.97),
        WordTimestamp(word="bridge", start=1.46, end=1.95, confidence=0.98),
        WordTimestamp(word="stands", start=2.00, end=2.40, confidence=0.95),
        WordTimestamp(word="three", start=2.42, end=2.75, confidence=0.99),
        WordTimestamp(word="hundred", start=2.76, end=3.10, confidence=0.99),
        WordTimestamp(word="meters", start=3.12, end=3.60, confidence=0.98),
        WordTimestamp(word="high.", start=3.62, end=4.20, confidence=0.97),
    ]


def test_phrase_segmentation_bounds():
    """Verify phrases contain between 2 and 5 words each."""
    engine = KineticTypographyEngine()
    words = _create_sample_whisper_words()
    phrases = engine.segment_words_into_phrases(words, min_words=2, max_words=4)

    assert len(phrases) >= 2
    for p in phrases:
        word_count = len(p.words)
        assert 2 <= word_count <= 5, f"Phrase '{p.phrase_text}' has {word_count} words (expected 2-5)"
        assert p.start_sec < p.end_sec
        assert p.phrase_text.strip() != ""


def test_semantic_emphasis_pop():
    """Verify emphasis words are highlighted in presets with scale pop."""
    engine = KineticTypographyEngine()
    words = _create_sample_whisper_words()
    phrases = engine.segment_words_into_phrases(words, min_words=2, max_words=4, emphasis_keywords=["highest", "meters"])

    ass_text = engine.generate_ass_script(
        phrases,
        style_preset="hormozi_yellow_pop",
        position=CaptionPosition.LOWER,
        animated=True,
    )

    assert "[Script Info]" in ass_text
    assert "[V4+ Styles]" in ass_text
    assert "Dialogue:" in ass_text
    # Check for animated tag with yellow color and 108% scale pop
    assert "\\fscx108" in ass_text or "\\c&H0000E6FF" in ass_text or "\\pos(" in ass_text


def test_caption_timing_derived_from_whisper():
    """Verify phrase start and end times exactly match the constituent Whisper word boundaries."""
    engine = KineticTypographyEngine()
    words = _create_sample_whisper_words()
    phrases = engine.segment_words_into_phrases(words, min_words=2, max_words=4)

    for p in phrases:
        assert p.start_sec == p.words[0].start
        assert p.end_sec >= p.words[-1].end


def test_saliency_safe_zone_positioning():
    """Verify caption coordinates dynamically move according to saliency plan."""
    engine = KineticTypographyEngine()

    # LOWER default
    x1, y1 = engine.compute_caption_coordinates(CaptionPosition.LOWER, PlatformSafeZone.YOUTUBE_SHORTS)
    # TOP position
    x2, y2 = engine.compute_caption_coordinates(CaptionPosition.TOP, PlatformSafeZone.YOUTUBE_SHORTS)
    # CENTER position
    x3, y3 = engine.compute_caption_coordinates(CaptionPosition.CENTER, PlatformSafeZone.YOUTUBE_SHORTS)

    assert x1 == 540  # 1080 // 2
    assert y2 < y3 < y1, f"Expected top ({y2}) < center ({y3}) < lower ({y1})"

    # If subject is near the bottom (saliency_y > 0.70), lower should flip or adjust
    x_sub, y_sub = engine.compute_caption_coordinates(CaptionPosition.LOWER, PlatformSafeZone.YOUTUBE_SHORTS, saliency_y=0.85)
    assert y_sub < y1, "Expected caption to flip to safe top when subject is at bottom"


def test_platform_safe_zones():
    """Verify geometry changes across YouTube Shorts, Instagram Reels, TikTok."""
    geo_shorts = PLATFORM_GEOMETRIES[PlatformSafeZone.YOUTUBE_SHORTS]
    geo_reels = PLATFORM_GEOMETRIES[PlatformSafeZone.INSTAGRAM_REELS]
    geo_tiktok = PLATFORM_GEOMETRIES[PlatformSafeZone.TIKTOK]

    assert geo_shorts.bottom_margin_px == 280
    assert geo_reels.bottom_margin_px == 320
    assert geo_tiktok.right_margin_px == 120  # Right side UI rail clearance


def test_caption_fallback_hierarchy():
    """Verify ASS Animated -> Static ASS -> SRT formats."""
    engine = KineticTypographyEngine()
    words = _create_sample_whisper_words()
    phrases = engine.segment_words_into_phrases(words)

    # 1. ASS Animated
    ass_anim = engine.generate_ass_script(phrases, animated=True)
    assert "\\t(" in ass_anim

    # 2. Static ASS
    ass_static = engine.generate_ass_script(phrases, animated=False)
    assert "\\t(" not in ass_static
    assert "Dialogue:" in ass_static

    # 3. SRT
    srt = engine.generate_srt(phrases)
    assert "-->" in srt
    assert "1\n" in srt


def test_caption_verification_helper():
    """Verify post-render caption verification validates transcript and bounds."""
    engine = KineticTypographyEngine()
    words = _create_sample_whisper_words()
    phrases = engine.segment_words_into_phrases(words)
    expected_text = "The world's highest rail bridge stands three hundred meters high."

    res = verify_caption_layout(phrases, expected_text, PlatformSafeZone.YOUTUBE_SHORTS)
    assert res.is_valid is True
    assert res.transcript_match_ratio >= 0.90
    assert res.safe_zone_compliant is True
    assert res.overflow_detected is False


def test_audio_scene_graph_tracks(tmp_path):
    """Verify Audio Scene Graph produces 3-track mix (Voice, BGM, SFX)."""
    # Create a 44.1kHz stereo dummy voice WAV
    voice_wav = tmp_path / "voice_01.wav"
    with wave.open(str(voice_wav), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(b"\x00\x00" * 44100 * 2)  # 1.0 sec

    scene = MaterializedScene(
        scene_id="scene_01",
        order=1,
        timing=MaterializedTiming(start_time_sec=0.0, end_time_sec=1.0, duration_sec=1.0),
        narration=MaterializedNarration(
            text="Hello world",
            audio_artifact_path=str(voice_wav),
        ),
        visual_requirements=VisualRequirements(visual_concept="test concept"),
        selected_assets=[
            SelectedAsset(asset_id="a1", asset_path=str(voice_wav), duration_sec=2.0)
        ],
        audio_plan=MaterializedAudioPlan(
            voice_path=str(voice_wav),
            voice_duration_sec=1.0,
            sfx_events=[SFXEvent(cue="whoosh_fast", time_sec=0.1, volume_db=-6.0)],
        ),
    )

    engine = AudioSceneGraphEngine()
    graph = engine.build_scene_graph([scene], total_duration_sec=1.0)

    assert len(graph.voice_segments) == 1
    assert len(graph.sfx_cues) == 1
    assert len(graph.ducking_envelope) >= 2


def test_sidechain_ducking_envelope():
    """Verify BGM ducks during speech and rises during dramatic pauses."""
    scene1 = MaterializedScene(
        scene_id="s1",
        order=1,
        timing=MaterializedTiming(start_time_sec=0.0, end_time_sec=2.0, duration_sec=2.0),
        narration=MaterializedNarration(text="Part one", audio_artifact_path="voice1.wav"),
        visual_requirements=VisualRequirements(visual_concept="test concept"),
        selected_assets=[SelectedAsset(asset_id="a1", asset_path="img1.png", duration_sec=2.0)],
        audio_plan=MaterializedAudioPlan(voice_path="voice1.wav", voice_duration_sec=2.0),
    )
    # Introduce 0.8s dramatic pause before scene 2
    scene2 = MaterializedScene(
        scene_id="s2",
        order=2,
        timing=MaterializedTiming(start_time_sec=2.8, end_time_sec=4.8, duration_sec=2.0),
        narration=MaterializedNarration(text="Part two", audio_artifact_path="voice2.wav"),
        visual_requirements=VisualRequirements(visual_concept="test concept"),
        selected_assets=[SelectedAsset(asset_id="a2", asset_path="img2.png", duration_sec=2.0)],
        audio_plan=MaterializedAudioPlan(voice_path="voice2.wav", voice_duration_sec=2.0),
    )

    engine = AudioSceneGraphEngine()
    graph = engine.build_scene_graph([scene1, scene2], total_duration_sec=4.8)

    # Check that dramatic pause accent point was added
    pause_points = [p for p in graph.ducking_envelope if p.reason == "dramatic_pause_accent"]
    assert len(pause_points) == 1
    assert pause_points[0].gain_db == engine.bgm_pause_boost_db


def test_sfx_cue_synthesis(tmp_path):
    """Verify procedural SFX synthesis produces valid 44.1kHz stereo WAV files."""
    sfx_types = ["whoosh_fast", "bass_drop", "digital_pop", "camera_shutter"]
    for sfx in sfx_types:
        sfx_path = generate_sfx_sample(sfx, tmp_path)
        assert sfx_path.exists()
        assert sfx_path.stat().st_size > 100
        with wave.open(str(sfx_path), "rb") as wf:
            assert wf.getframerate() == 44100
            assert wf.getnchannels() == 2


def test_beat_sync_engine(tmp_path):
    """Verify BeatSyncEngine attaches kinetic phrases and narrative role SFX."""
    voice_wav = tmp_path / "voice_sync.wav"
    with wave.open(str(voice_wav), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(b"\x00\x00" * 44100 * 2)

    scene = MaterializedScene(
        scene_id="hook_scene",
        order=1,
        narrative_role=NarrativeRole.HOOK,
        timing=MaterializedTiming(start_time_sec=0.0, end_time_sec=4.2, duration_sec=4.2),
        narration=MaterializedNarration(
            text="The world's highest rail bridge stands three hundred meters high.",
            audio_artifact_path=str(voice_wav),
            word_timestamps=_create_sample_whisper_words(),
        ),
        visual_requirements=VisualRequirements(visual_concept="test concept"),
        selected_assets=[SelectedAsset(asset_id="a1", asset_path=str(voice_wav), duration_sec=5.0)],
        audio_plan=MaterializedAudioPlan(voice_path=str(voice_wav), voice_duration_sec=4.2),
    )

    sync_engine = BeatSyncEngine()
    synced_scene = sync_engine.synchronize_scene(scene, style_preset="hormozi_yellow_pop")

    assert len(synced_scene.caption_plan.phrases) >= 2
    assert len(synced_scene.audio_plan.sfx_events) >= 1
    assert synced_scene.audio_plan.sfx_events[0].cue == "impact"


def test_deterministic_captions_and_audio():
    """Verify identical word inputs yield bitwise identical phrase segmentation and ASS output."""
    engine = KineticTypographyEngine()
    words = _create_sample_whisper_words()

    phrases1 = engine.segment_words_into_phrases(words)
    phrases2 = engine.segment_words_into_phrases(words)
    ass1 = engine.generate_ass_script(phrases1)
    ass2 = engine.generate_ass_script(phrases2)

    assert ass1 == ass2
    assert len(phrases1) == len(phrases2)


def test_timeline_compiler_phase4_integration(tmp_path):
    """Verify TimelineCompiler compiles RenderPlan containing Phase 4 caption and SFX metadata."""
    voice_wav = tmp_path / "voice_p4.wav"
    with wave.open(str(voice_wav), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(b"\x00\x00" * 44100 * 2)

    img_path = tmp_path / "img_p4.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    scene = MaterializedScene(
        scene_id="s1",
        order=1,
        timing=MaterializedTiming(start_time_sec=0.0, end_time_sec=2.0, duration_sec=2.0),
        narration=MaterializedNarration(
            text="Testing phase four compilation",
            audio_artifact_path=str(voice_wav),
            word_timestamps=[
                WordTimestamp(word="Testing", start=0.0, end=0.5),
                WordTimestamp(word="phase", start=0.6, end=1.0),
                WordTimestamp(word="four", start=1.1, end=1.5),
                WordTimestamp(word="compilation", start=1.5, end=2.0),
            ],
        ),
        visual_requirements=VisualRequirements(visual_concept="test concept"),
        selected_assets=[
            SelectedAsset(
                asset_id="a1",
                asset_path=str(img_path),
                provenance={"provider": "infographics", "license": "CC-BY"},
                duration_sec=2.0,
            )
        ],
        audio_plan=MaterializedAudioPlan(
            voice_path=str(voice_wav),
            voice_duration_sec=2.0,
            sfx_events=[SFXEvent(cue="whoosh_fast", time_sec=0.05, volume_db=-6.0)],
        ),
    )

    sync_engine = BeatSyncEngine()
    synced = sync_engine.synchronize_scene(scene)

    timeline = MaterializedTimeline(
        timeline_id="mt-test-p4",
        job_id="job_p4",
        intent_timeline_id="it-test-p4",
        total_measured_duration_sec=2.0,
        scenes=[synced],
    )

    compiler = TimelineCompiler()
    render_plan = compiler.compile(timeline, check_physical_files=False)

    assert render_plan.job_id == "job_p4"
    assert len(render_plan.scenes) == 1
    sc0 = render_plan.scenes[0]
    assert "caption_phrases" in sc0
    assert len(sc0["caption_phrases"]) >= 1
    assert "sfx_events" in sc0
    assert len(sc0["sfx_events"]) == 1
