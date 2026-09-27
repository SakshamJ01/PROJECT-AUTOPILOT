"""Tests for Cross-Phase 0.5 — Deterministic Timeline Compiler & 10 Hard Render Invariants.

Tests:
    - Successful compilation of valid MaterializedTimeline -> sealed RenderPlan
    - Independent verification of all 10 Hard Invariants
    - Simultaneous cross-track timestamps allowed under Monotonicity
    - Strict MPT handoff contract (match_materials_to_script=True, random_material_selection=False)
    - Deterministic hashing & input sensitivity
    - Stale artifact detection
"""
import pytest
from pathlib import Path
import tempfile

from autopilot.core.timeline import (
    IntentTimeline,
    IntentScene,
    IntentNarration,
    VisualRequirements,
    CaptionIntent,
    AudioIntent,
    TransitionIntent,
    QAExpectations,
    TargetTiming,
    MaterializedTimeline,
    MaterializedScene,
    MaterializedTiming,
    MaterializedNarration,
    SelectedAsset,
    TrimRange,
    CropFraming,
    WordTimestamp,
    CaptionPhrase,
    MaterializedCaptionPlan,
    MaterializedAudioPlan,
    MaterializedTransitionPlan,
    SFXEvent,
    RenderPlan,
    NarrativeRole,
    ShotType,
    VisualAssertionLevel,
    VisualCoverageClass,
    PlatformSafeZone,
    ReproducibilityClass,
    StaleDefectCode,
)
from autopilot.core.timeline_compiler import (
    TimelineCompiler,
    HardInvariantCode,
    TimelineValidationError,
    TimelineCompilationError,
)


def create_mock_scene(
    scene_id: str = "scene-01",
    order: int = 1,
    start: float = 0.0,
    end: float = 4.0,
    duration: float = 4.0,
    voice_dur: float = 3.8,
    asset_path: str = "/tmp/asset_01.mp4",
    voice_path: str = "/tmp/voice_01.wav",
    license_str: str = "Pexels Commercial",
    provider: str = "pexels",
    transition_dur: float = 0.0,
    transition_type: str = "cut",
    trim_in: float = 0.0,
    trim_out: float = 5.0,
    words: list = None,
    phrases: list = None,
    sfx_events: list = None,
) -> MaterializedScene:
    if words is None:
        words = [
            WordTimestamp(word="Hello", start=start + 0.1, end=start + 0.5),
            WordTimestamp(word="world", start=start + 0.5, end=start + 1.2),
        ]
    if phrases is None:
        phrases = [
            CaptionPhrase(
                phrase_text="Hello world",
                start_sec=start + 0.1,
                end_sec=start + 1.2,
                words=words,
            )
        ]
    if sfx_events is None:
        sfx_events = [SFXEvent(cue="whoosh", time_sec=start + 0.1)]

    asset = SelectedAsset(
        asset_id=f"asset-{scene_id}",
        asset_path=asset_path,
        media_type="video",
        provenance={"provider": provider, "license": license_str},
        trim_range=TrimRange(in_sec=trim_in, out_sec=trim_out),
        crop_framing=CropFraming(saliency_x=0.5, saliency_y=0.5),
        duration_sec=10.0,
    )

    return MaterializedScene(
        scene_id=scene_id,
        scene_version=1,
        order=order,
        narrative_role=NarrativeRole.HOOK if order == 1 else NarrativeRole.CONTENT,
        timing=MaterializedTiming(
            start_time_sec=start,
            end_time_sec=end,
            duration_sec=duration,
        ),
        narration=MaterializedNarration(
            text="Hello world",
            audio_artifact_path=voice_path,
            word_timestamps=words,
        ),
        visual_requirements=VisualRequirements(visual_concept="Concept"),
        selected_assets=[asset],
        transition_plan=MaterializedTransitionPlan(type=transition_type, duration_sec=transition_dur),
        caption_plan=MaterializedCaptionPlan(phrases=phrases),
        audio_plan=MaterializedAudioPlan(
            voice_path=voice_path,
            voice_duration_sec=voice_dur,
            bgm_intensity=0.8,
            sfx_events=sfx_events,
        ),
    )


def create_valid_timeline() -> MaterializedTimeline:
    s1 = create_mock_scene("scene-01", order=1, start=0.0, end=4.0, duration=4.0, voice_dur=3.8)
    s2 = create_mock_scene("scene-02", order=2, start=4.0, end=8.0, duration=4.0, voice_dur=3.9)
    return MaterializedTimeline(
        timeline_id="tl-valid-01",
        timeline_version="v1.0",
        job_id="job-val-01",
        intent_timeline_id="tl-intent-01",
        parent_timeline_version="v1.0",
        profile_id="viral_creator",
        content_type="FACTS",
        total_measured_duration_sec=8.0,
        scenes=[s1, s2],
        audio_mix_manifest={"master_voice_path": "/tmp/master.wav", "bgm_file": "/tmp/bgm.mp3"},
    )


def test_valid_timeline_compilation_and_sealing():
    """Verify that a valid MaterializedTimeline compiles into a sealed RenderPlan."""
    tl = create_valid_timeline()
    compiler = TimelineCompiler(check_physical_files=False)
    plan = compiler.compile(tl)

    assert isinstance(plan, RenderPlan)
    assert plan.timeline_id == "tl-valid-01"
    assert plan.render_plan_sha256 is not None
    assert len(plan.render_plan_sha256) == 64
    assert plan.timeline_sha256 == tl.compute_sha256()
    assert len(plan.scenes) == 2
    assert plan.mpt_handoff.match_materials_to_script is True
    assert plan.mpt_handoff.random_material_selection is False
    assert len(plan.mpt_handoff.video_materials) == 2


def test_invariant_01_timeline_continuity_start_time():
    """Verify Invariant 01 fails if timeline does not start at 0.0s."""
    s1 = create_mock_scene("scene-01", order=1, start=1.0, end=5.0, duration=4.0)
    tl = MaterializedTimeline(
        timeline_id="tl-bad",
        timeline_version="v1.0",
        job_id="j1",
        intent_timeline_id="it1",
        total_measured_duration_sec=5.0,
        scenes=[s1],
    )
    compiler = TimelineCompiler(check_physical_files=False)
    errors = compiler.validate_hard_invariants(tl)
    assert any(e.invariant_code == HardInvariantCode.INVARIANT_01_TIMELINE_CONTINUITY for e in errors)


def test_invariant_01_timeline_continuity_gap():
    """Verify Invariant 01 fails if there is a gap between consecutive scenes."""
    s1 = create_mock_scene("scene-01", order=1, start=0.0, end=4.0, duration=4.0)
    s2 = create_mock_scene("scene-02", order=2, start=4.5, end=8.5, duration=4.0)
    tl = MaterializedTimeline(
        timeline_id="tl-bad",
        timeline_version="v1.0",
        job_id="j1",
        intent_timeline_id="it1",
        total_measured_duration_sec=8.5,
        scenes=[s1, s2],
    )
    compiler = TimelineCompiler(check_physical_files=False)
    errors = compiler.validate_hard_invariants(tl)
    assert any(e.invariant_code == HardInvariantCode.INVARIANT_01_TIMELINE_CONTINUITY for e in errors)


def test_invariant_02_physical_asset_existence():
    """Verify Invariant 02 fails if physical asset is missing on disk when check is enabled."""
    s1 = create_mock_scene("scene-01", asset_path="/nonexistent/missing_clip_xyz.mp4")
    tl = MaterializedTimeline(
        timeline_id="tl-bad",
        timeline_version="v1.0",
        job_id="j1",
        intent_timeline_id="it1",
        total_measured_duration_sec=4.0,
        scenes=[s1],
    )
    compiler = TimelineCompiler(check_physical_files=True)
    errors = compiler.validate_hard_invariants(tl)
    assert any(e.invariant_code == HardInvariantCode.INVARIANT_02_PHYSICAL_ASSET_EXISTENCE for e in errors)


def test_invariant_03_audio_coverage():
    """Verify Invariant 03 fails if visual duration is shorter than voice duration."""
    s1 = create_mock_scene("scene-01", start=0.0, end=3.0, duration=3.0, voice_dur=4.5)
    tl = MaterializedTimeline(
        timeline_id="tl-bad",
        timeline_version="v1.0",
        job_id="j1",
        intent_timeline_id="it1",
        total_measured_duration_sec=3.0,
        scenes=[s1],
    )
    compiler = TimelineCompiler(check_physical_files=False)
    errors = compiler.validate_hard_invariants(tl)
    assert any(e.invariant_code == HardInvariantCode.INVARIANT_03_AUDIO_COVERAGE for e in errors)


def test_invariant_04_caption_enclosure():
    """Verify Invariant 04 fails if caption phrase or word timestamp exceeds scene bounds."""
    # Caption phrase ends at 5.0s, but scene ends at 4.0s
    phrase = CaptionPhrase(phrase_text="Late subtitle", start_sec=1.0, end_sec=5.0)
    s1 = create_mock_scene("scene-01", start=0.0, end=4.0, duration=4.0, phrases=[phrase])
    tl = MaterializedTimeline(
        timeline_id="tl-bad",
        timeline_version="v1.0",
        job_id="j1",
        intent_timeline_id="it1",
        total_measured_duration_sec=4.0,
        scenes=[s1],
    )
    compiler = TimelineCompiler(check_physical_files=False)
    errors = compiler.validate_hard_invariants(tl)
    assert any(e.invariant_code == HardInvariantCode.INVARIANT_04_CAPTION_ENCLOSURE for e in errors)


def test_invariant_05_transition_guard():
    """Verify Invariant 05 fails if transition duration exceeds 50% of adjacent scene duration."""
    # Scene 1 is 4.0s, Scene 2 is 2.0s -> min duration = 2.0s -> max transition allowed = 1.0s
    # Setting transition to 1.5s must violate Invariant 05
    s1 = create_mock_scene("scene-01", order=1, start=0.0, end=4.0, duration=4.0, transition_dur=1.5, transition_type="wipe")
    s2 = create_mock_scene("scene-02", order=2, start=4.0, end=6.0, duration=2.0)
    tl = MaterializedTimeline(
        timeline_id="tl-bad",
        timeline_version="v1.0",
        job_id="j1",
        intent_timeline_id="it1",
        total_measured_duration_sec=6.0,
        scenes=[s1, s2],
    )
    compiler = TimelineCompiler(check_physical_files=False)
    errors = compiler.validate_hard_invariants(tl)
    assert any(e.invariant_code == HardInvariantCode.INVARIANT_05_TRANSITION_GUARD for e in errors)


def test_invariant_06_trim_validity():
    """Verify Invariant 06 fails if trim range is invalid or shorter than required duration."""
    # Scene duration is 4.0s, but trim range is only 2.0s (in=1.0, out=3.0)
    s1 = create_mock_scene("scene-01", start=0.0, end=4.0, duration=4.0, trim_in=1.0, trim_out=3.0)
    tl = MaterializedTimeline(
        timeline_id="tl-bad",
        timeline_version="v1.0",
        job_id="j1",
        intent_timeline_id="it1",
        total_measured_duration_sec=4.0,
        scenes=[s1],
    )
    compiler = TimelineCompiler(check_physical_files=False)
    errors = compiler.validate_hard_invariants(tl)
    assert any(e.invariant_code == HardInvariantCode.INVARIANT_06_TRIM_VALIDITY for e in errors)


def test_invariant_07_audio_stem_integrity_missing_file():
    """Verify Invariant 07 fails if voice path file does not exist on disk."""
    s1 = create_mock_scene("scene-01", voice_path="/tmp/nonexistent_missing_voice_stem_xyz.wav")
    tl = MaterializedTimeline(
        timeline_id="tl-bad",
        timeline_version="v1.0",
        job_id="j1",
        intent_timeline_id="it1",
        total_measured_duration_sec=4.0,
        scenes=[s1],
    )
    compiler = TimelineCompiler(check_physical_files=True)
    errors = compiler.validate_hard_invariants(tl)
    assert any(e.invariant_code == HardInvariantCode.INVARIANT_07_AUDIO_STEM_INTEGRITY for e in errors)


def test_invariant_07_audio_stem_sample_rate_and_stereo(tmp_path):
    """Verify Invariant 07 enforces 44.1 kHz (44100 Hz) sample rate and stereo (2 channels)."""
    import wave
    
    # 1. Test model-level invalid sample rate (e.g., 22050 Hz)
    s1 = create_mock_scene("scene-01")
    s1.audio_plan.sample_rate = 22050
    tl = MaterializedTimeline(
        timeline_id="tl-bad-sr",
        timeline_version="v1.0",
        job_id="j1",
        intent_timeline_id="it1",
        total_measured_duration_sec=4.0,
        scenes=[s1],
    )
    compiler = TimelineCompiler(check_physical_files=False)
    errors = compiler.validate_hard_invariants(tl)
    assert any(
        e.invariant_code == HardInvariantCode.INVARIANT_07_AUDIO_STEM_INTEGRITY
        and "sample rate 22050" in e.message
        for e in errors
    )

    # 2. Test model-level invalid channel count (e.g., mono / 1 channel)
    s2 = create_mock_scene("scene-01")
    s2.audio_plan.channels = 1
    tl2 = MaterializedTimeline(
        timeline_id="tl-bad-ch",
        timeline_version="v1.0",
        job_id="j1",
        intent_timeline_id="it1",
        total_measured_duration_sec=4.0,
        scenes=[s2],
    )
    errors2 = compiler.validate_hard_invariants(tl2)
    assert any(
        e.invariant_code == HardInvariantCode.INVARIANT_07_AUDIO_STEM_INTEGRITY
        and "channel count 1" in e.message
        for e in errors2
    )

    # 3. Test physical WAV header validation (e.g. 16000 Hz WAV on disk)
    bad_wav = tmp_path / "bad_16k.wav"
    with wave.open(str(bad_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 1600)
    
    dummy_asset = tmp_path / "dummy_asset.mp4"
    dummy_asset.write_bytes(b"dummy")

    s3 = create_mock_scene("scene-01", voice_path=str(bad_wav), asset_path=str(dummy_asset))
    tl3 = MaterializedTimeline(
        timeline_id="tl-bad-phys",
        timeline_version="v1.0",
        job_id="j1",
        intent_timeline_id="it1",
        total_measured_duration_sec=4.0,
        scenes=[s3],
    )
    phys_compiler = TimelineCompiler(check_physical_files=True)
    errors3 = phys_compiler.validate_hard_invariants(tl3)
    assert any(
        e.invariant_code == HardInvariantCode.INVARIANT_07_AUDIO_STEM_INTEGRITY
        and "sample rate 16000" in e.message
        for e in errors3
    )
    assert any(
        e.invariant_code == HardInvariantCode.INVARIANT_07_AUDIO_STEM_INTEGRITY
        and "channel count 1" in e.message
        for e in errors3
    )



def test_invariant_08_monotonicity_stream_and_concurrent_tracks():
    """Verify Invariant 08 requires non-decreasing stream timestamps but allows simultaneous cross-track events."""
    # Simultaneous cross-track events: word at 0.5s and SFX cue at 0.5s -> VALID!
    words = [
        WordTimestamp(word="First", start=0.2, end=0.5),
        WordTimestamp(word="Second", start=0.5, end=1.0),
    ]
    sfx = [SFXEvent(cue="whoosh", time_sec=0.5)] # simultaneous with word "Second"
    s1 = create_mock_scene("scene-01", start=0.0, end=4.0, duration=4.0, words=words, sfx_events=sfx)
    tl = MaterializedTimeline(
        timeline_id="tl-ok",
        timeline_version="v1.0",
        job_id="j1",
        intent_timeline_id="it1",
        total_measured_duration_sec=4.0,
        scenes=[s1],
    )
    compiler = TimelineCompiler(check_physical_files=False)
    errors = compiler.validate_hard_invariants(tl)
    assert not any(e.invariant_code == HardInvariantCode.INVARIANT_08_MONOTONICITY for e in errors)

    # Broken stream monotonicity: word 2 starts before word 1 -> INVALID!
    bad_words = [
        WordTimestamp(word="First", start=0.8, end=1.2),
        WordTimestamp(word="Second", start=0.4, end=0.7),
    ]
    s1_bad = create_mock_scene("scene-01", start=0.0, end=4.0, duration=4.0, words=bad_words)
    tl_bad = MaterializedTimeline(
        timeline_id="tl-bad",
        timeline_version="v1.0",
        job_id="j1",
        intent_timeline_id="it1",
        total_measured_duration_sec=4.0,
        scenes=[s1_bad],
    )
    errors_bad = compiler.validate_hard_invariants(tl_bad)
    assert any(e.invariant_code == HardInvariantCode.INVARIANT_08_MONOTONICITY for e in errors_bad)


def test_invariant_09_license_integrity():
    """Verify Invariant 09 flags non-commercial or unlicensed media."""
    s1 = create_mock_scene("scene-01", license_str="Non-Commercial Editorial", provider="unknown_scraper")
    tl = MaterializedTimeline(
        timeline_id="tl-bad",
        timeline_version="v1.0",
        job_id="j1",
        intent_timeline_id="it1",
        total_measured_duration_sec=4.0,
        scenes=[s1],
    )
    compiler = TimelineCompiler(check_physical_files=False)
    errors = compiler.validate_hard_invariants(tl)
    assert any(e.invariant_code == HardInvariantCode.INVARIANT_09_LICENSE_INTEGRITY for e in errors)


def test_invariant_10_zero_stale_artifacts():
    """Verify Invariant 10 catches mismatched parent intent timeline versions or scene versions."""
    intent_scene = IntentScene(
        scene_id="scene-01",
        scene_version=2, # Intent is v2
        order=1,
        timing=TargetTiming(target_duration_sec=4.0),
        narration=IntentNarration(text="New intent"),
        visual_requirements=VisualRequirements(visual_concept="New visual"),
    )
    parent_intent = IntentTimeline(
        timeline_id="tl-intent-01",
        timeline_version="v2.0", # Intent version v2
        job_id="job-val-01",
        total_target_duration_sec=4.0,
        scenes=[intent_scene],
    )

    # Materialized scene is still version 1 (stale artifact)
    mat_scene = create_mock_scene("scene-01", start=0.0, end=4.0, duration=4.0)
    mat_scene = mat_scene.model_copy(update={"scene_version": 1})
    tl = MaterializedTimeline(
        timeline_id="tl-mat-01",
        timeline_version="v1.0",
        job_id="job-val-01",
        intent_timeline_id="tl-intent-01",
        parent_timeline_version="v1.0", # Stale parent version
        total_measured_duration_sec=4.0,
        scenes=[mat_scene],
    )

    compiler = TimelineCompiler(check_physical_files=False)
    errors = compiler.validate_hard_invariants(tl, parent_intent_timeline=parent_intent)
    assert any(e.invariant_code == HardInvariantCode.INVARIANT_10_ZERO_STALE_ARTIFACTS for e in errors)


def test_compilation_determinism_and_sensitivity():
    """Verify compilation hash is deterministic for identical inputs and sensitive to changes."""
    tl1 = create_valid_timeline()
    tl2 = create_valid_timeline()

    compiler = TimelineCompiler(check_physical_files=False)
    plan1 = compiler.compile(tl1)
    plan2 = compiler.compile(tl2)

    assert plan1.render_plan_sha256 == plan2.render_plan_sha256

    # Modify scene 1 duration in tl2
    s1_mod = tl2.scenes[0].model_copy(update={"timing": MaterializedTiming(start_time_sec=0.0, end_time_sec=4.1, duration_sec=4.1)})
    s2_mod = tl2.scenes[1].model_copy(update={"timing": MaterializedTiming(start_time_sec=4.1, end_time_sec=8.1, duration_sec=4.0)})
    tl2_mod = tl2.model_copy(update={"scenes": [s1_mod, s2_mod], "total_measured_duration_sec": 8.1})

    plan3 = compiler.compile(tl2_mod)
    assert plan1.render_plan_sha256 != plan3.render_plan_sha256


def test_mpt_handoff_contract():
    """Verify that MPT handoff parameters conform strictly to renderer-only contract."""
    tl = create_valid_timeline()
    compiler = TimelineCompiler(check_physical_files=False)
    plan = compiler.compile(tl)

    handoff = plan.mpt_handoff
    assert handoff is not None
    assert handoff.match_materials_to_script is True
    assert handoff.random_material_selection is False
    assert handoff.video_fit_mode == "crop"
    assert handoff.video_materials == [s.selected_assets[0].asset_path for s in tl.scenes]
    assert handoff.custom_audio_file == "/tmp/master.wav"
