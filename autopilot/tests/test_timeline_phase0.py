"""Unit and integration tests for Cross-Phase 0 3-Tier Timeline Architecture.
Tests IntentTimeline -> MaterializedTimeline -> RenderPlan lineage, deterministic hashing,
stale detection, serialization, validation, and legacy adapter.
"""
import pytest
from pydantic import ValidationError

from autopilot.core.timeline import (
    IntentTimeline,
    IntentScene,
    IntentNarration,
    VisualRequirements,
    CaptionIntent,
    CaptionPosition,
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
    WordTimestamp,
    CaptionPhrase,
    MaterializedCaptionPlan,
    MaterializedAudioPlan,
    MaterializedTransitionPlan,
    RenderPlan,
    MPTHandoffConfig,
    NarrativeRole,
    ShotType,
    VisualAssertionLevel,
    VisualCoverageClass,
    PlatformSafeZone,
    ReproducibilityClass,
    StaleDefectCode,
    canonical_json_dumps,
    compute_payload_sha256,
    detect_stale_timeline,
    detect_stale_scene_version,
    script_document_to_intent_timeline,
)
from autopilot.core.contracts import ScriptDocument, ScriptScene


def test_valid_intent_timeline_creation():
    """Verify canonical IntentTimeline creates successfully with complete intent data."""
    scene1 = IntentScene(
        scene_id="scene-01",
        scene_version=1,
        order=1,
        narrative_role=NarrativeRole.HOOK,
        timing=TargetTiming(target_duration_sec=4.5),
        narration=IntentNarration(
            text="Did you know that octopuses have three hearts?",
            normalized_pronunciation="Did you know that octopuses have three hearts?",
            pronunciation_overrides={"octopuses": "AHK-tuh-puh-sez"},
            voice_id="en-US-GuyNeural",
            prosody_style="energetic",
        ),
        visual_requirements=VisualRequirements(
            visual_concept="Vibrant underwater macro shot of an octopus moving gracefully",
            required_shot_type=ShotType.MACRO,
            b_roll_search_query="octopus swimming coral reef macro",
            coverage_expectation=VisualCoverageClass.EXACT_MATCH,
            visual_assertion_level=VisualAssertionLevel.LITERAL,
        ),
        caption_intent=CaptionIntent(
            style_preset="hormozi_bold",
            position=CaptionPosition.LOWER,
            emphasis_words=["three", "hearts"],
            max_words_per_phrase=3,
            platform_safe_zone=PlatformSafeZone.YOUTUBE_SHORTS,
        ),
        audio_intent=AudioIntent(
            bgm_intensity=0.6,
            sfx_cues=[{"name": "whoosh", "offset": 0.0}],
        ),
        transition_intent=TransitionIntent(
            transition_type="dip_to_black",
            duration_sec=0.3,
        ),
        qa_expectations=QAExpectations(
            min_semantic_match=0.80,
            allow_contextual_broll=True,
        ),
    )

    timeline = IntentTimeline(
        timeline_id="tl-job-12345",
        timeline_version="v1.0",
        job_id="job-12345",
        profile_id="viral_creator",
        content_type="DID_YOU_KNOW",
        total_target_duration_sec=4.5,
        scenes=[scene1],
        director_metadata={"director": "autopilot_v4", "target_audience": "general"},
    )

    assert timeline.timeline_id == "tl-job-12345"
    assert timeline.timeline_version == "v1.0"
    assert len(timeline.scenes) == 1
    assert timeline.scenes[0].narrative_role == NarrativeRole.HOOK
    assert timeline.scenes[0].narration.pronunciation_overrides == {"octopuses": "AHK-tuh-puh-sez"}
    assert timeline.scenes[0].caption_intent.platform_safe_zone == PlatformSafeZone.YOUTUBE_SHORTS


def test_invalid_intent_timeline_rejection():
    """Verify validation errors on invalid inputs."""
    # Negative target duration should fail
    with pytest.raises(ValidationError):
        TargetTiming(target_duration_sec=-2.0)

    # Empty scene_id should fail
    with pytest.raises(ValidationError):
        IntentScene(
            scene_id="",
            order=1,
            timing=TargetTiming(target_duration_sec=5.0),
            narration=IntentNarration(text="Hello"),
            visual_requirements=VisualRequirements(visual_concept="Concept"),
        )

    # Negative total target duration on timeline
    with pytest.raises(ValidationError):
        IntentTimeline(
            timeline_id="tl-test",
            job_id="job-test",
            profile_id="test",
            content_type="TEST",
            total_target_duration_sec=-10.0,
            scenes=[],
        )


def test_materialized_timeline_creation_and_validation():
    """Verify post-acquisition truth representation creates and computes SHA-256."""
    words = [
        WordTimestamp(word="Did", start=0.0, end=0.2, confidence=0.98),
        WordTimestamp(word="you", start=0.2, end=0.35, confidence=0.99),
        WordTimestamp(word="know", start=0.35, end=0.7, confidence=0.99),
    ]

    selected_asset = SelectedAsset(
        asset_id="pex-1029384",
        asset_path="/var/autopilot/assets/pex-1029384.mp4",
        media_type="video",
        trim_range=TrimRange(in_sec=1.0, out_sec=4.2),
        shot_type=ShotType.MACRO,
    )

    mat_scene = MaterializedScene(
        scene_id="scene-01",
        scene_version=1,
        order=1,
        narrative_role=NarrativeRole.HOOK,
        timing=MaterializedTiming(
            start_time_sec=0.0,
            end_time_sec=3.2,
            duration_sec=3.2,
        ),
        narration=MaterializedNarration(
            text="Did you know",
            audio_artifact_path="/var/autopilot/audio/scene-01-tts.wav",
            word_timestamps=words,
        ),
        visual_requirements=VisualRequirements(visual_concept="Underwater octopus"),
        selected_assets=[selected_asset],
        caption_plan=MaterializedCaptionPlan(
            phrases=[
                CaptionPhrase(
                    phrase_text="Did you know",
                    start_sec=0.0,
                    end_sec=0.7,
                    words=words,
                    emphasis_words=["know"],
                )
            ]
        ),
        audio_plan=MaterializedAudioPlan(
            voice_path="/var/autopilot/audio/scene-01-tts.wav",
            voice_duration_sec=3.2,
            bgm_intensity=0.8,
        ),
    )

    mat_timeline = MaterializedTimeline(
        timeline_id="tl-job-12345",
        timeline_version="v1.0",
        job_id="job-12345",
        intent_timeline_id="tl-job-12345",
        parent_timeline_version="v1.0",
        profile_id="viral_creator",
        content_type="DID_YOU_KNOW",
        total_measured_duration_sec=3.2,
        scenes=[mat_scene],
    )

    assert mat_timeline.timeline_id == "tl-job-12345"
    assert mat_timeline.parent_timeline_version == "v1.0"
    assert len(mat_timeline.scenes) == 1
    assert mat_timeline.scenes[0].selected_assets[0].asset_path == "/var/autopilot/assets/pex-1029384.mp4"
    assert mat_timeline.scenes[0].narration.word_timestamps[0].word == "Did"

    # Compute hash
    sha = mat_timeline.compute_sha256()
    assert isinstance(sha, str)
    assert len(sha) == 64


def test_invalid_materialized_timing_rejection():
    """Verify invalid timing boundaries are rejected."""
    # end_time_sec <= start_time_sec
    with pytest.raises(ValidationError):
        MaterializedTiming(
            start_time_sec=5.0,
            end_time_sec=4.0,
            duration_sec=1.0,
        )

    # Negative start time
    with pytest.raises(ValidationError):
        MaterializedTiming(
            start_time_sec=-1.0,
            end_time_sec=5.0,
            duration_sec=5.0,
        )


def test_render_plan_creation_and_sealing():
    """Verify RenderPlan deterministic hashing and sealing."""
    plan = RenderPlan(
        plan_id="plan-job-12345",
        content_id="content-12345",
        job_id="job-12345",
        profile="vertical_short",
        target_resolution="1080x1920",
        timeline_id="tl-job-12345",
        timeline_version="v1.0",
        parent_timeline_version="v1.0",
        compiler_version="v4.0.0",
        timeline_sha256="abc123def456",
        mpt_handoff=MPTHandoffConfig(
            custom_audio_file="/var/audio/master.wav",
            video_materials=["/var/assets/clip1.mp4"],
            video_transition_mode="cut",
            video_fit_mode="crop",
            subtitle_display_mode="animated",
            bgm_volume=0.15,
        ),
    )

    assert plan.render_plan_sha256 is None
    sealed = plan.seal()
    assert sealed.render_plan_sha256 is not None
    assert len(sealed.render_plan_sha256) == 64

    # Re-sealing sealed object must give identical hash
    sealed_again = sealed.seal()
    assert sealed_again.render_plan_sha256 == sealed.render_plan_sha256


def test_deterministic_hashing_and_sensitivity():
    """Verify SHA-256 is strictly deterministic and sensitive to changes."""
    words1 = [WordTimestamp(word="Hello", start=0.0, end=0.5)]
    words2 = [WordTimestamp(word="Hello", start=0.0, end=0.5)]

    def make_scene(words):
        return MaterializedScene(
            scene_id="scene-01",
            scene_version=1,
            order=1,
            timing=MaterializedTiming(start_time_sec=0.0, end_time_sec=2.0, duration_sec=2.0),
            narration=MaterializedNarration(
                text="Hello",
                audio_artifact_path="/var/audio/h.wav",
                word_timestamps=words,
            ),
            visual_requirements=VisualRequirements(visual_concept="Concept"),
            selected_assets=[
                SelectedAsset(asset_id="a1", asset_path="/var/asset1.mp4")
            ],
            audio_plan=MaterializedAudioPlan(voice_path="/var/audio/h.wav", voice_duration_sec=2.0),
        )

    tl1 = MaterializedTimeline(
        timeline_id="tl-001",
        timeline_version="v1.0",
        job_id="job-001",
        intent_timeline_id="tl-001",
        profile_id="viral",
        content_type="FACTS",
        total_measured_duration_sec=2.0,
        scenes=[make_scene(words1)],
    )
    tl2 = MaterializedTimeline(
        timeline_id="tl-001",
        timeline_version="v1.0",
        job_id="job-001",
        intent_timeline_id="tl-001",
        profile_id="viral",
        content_type="FACTS",
        total_measured_duration_sec=2.0,
        scenes=[make_scene(words2)],
    )

    hash1 = tl1.compute_sha256()
    hash2 = tl2.compute_sha256()
    assert hash1 == hash2

    # Modify one field in tl2: duration from 2.0 to 2.1
    tl2_modified = tl2.model_copy(update={"total_measured_duration_sec": 2.1})
    hash3 = tl2_modified.compute_sha256()
    assert hash1 != hash3


def test_deterministic_serialization_round_trip():
    """Verify canonical serialization and deserialization retains all fields with zero loss."""
    orig = IntentTimeline(
        timeline_id="tl-roundtrip",
        timeline_version="v1.0",
        job_id="job-rt",
        profile_id="viral",
        content_type="STORY",
        total_target_duration_sec=10.0,
        scenes=[
            IntentScene(
                scene_id="scene-01",
                scene_version=1,
                order=1,
                narrative_role=NarrativeRole.HOOK,
                timing=TargetTiming(target_duration_sec=10.0),
                narration=IntentNarration(text="Roundtrip test narration"),
                visual_requirements=VisualRequirements(visual_concept="Testing concept"),
                caption_intent=CaptionIntent(
                    platform_safe_zone=PlatformSafeZone.TIKTOK,
                ),
            )
        ],
    )

    serialized = canonical_json_dumps(orig)
    assert isinstance(serialized, str)

    restored = IntentTimeline.model_validate_json(serialized)
    assert restored.timeline_id == orig.timeline_id
    assert restored.scenes[0].caption_intent.platform_safe_zone == PlatformSafeZone.TIKTOK
    assert restored.compute_sha256() == orig.compute_sha256()


def test_stale_detection():
    """Verify stale detection flags mismatched timeline ID, version, or SHA-256."""
    scene = MaterializedScene(
        scene_id="scene-01",
        scene_version=1,
        order=1,
        timing=MaterializedTiming(start_time_sec=0.0, end_time_sec=5.0, duration_sec=5.0),
        narration=MaterializedNarration(text="Hello", audio_artifact_path="/audio.wav"),
        visual_requirements=VisualRequirements(visual_concept="Concept"),
        selected_assets=[SelectedAsset(asset_id="a1", asset_path="/asset.mp4")],
        audio_plan=MaterializedAudioPlan(voice_path="/audio.wav", voice_duration_sec=5.0),
    )

    mat_tl = MaterializedTimeline(
        timeline_id="tl-test-100",
        timeline_version="v1.0",
        job_id="job-100",
        intent_timeline_id="tl-test-100",
        profile_id="viral",
        content_type="FACTS",
        total_measured_duration_sec=5.0,
        scenes=[scene],
    )
    correct_hash = mat_tl.compute_sha256()

    valid_plan = RenderPlan(
        plan_id="p-100",
        content_id="c-100",
        job_id="job-100",
        timeline_id="tl-test-100",
        timeline_version="v1.0",
        timeline_sha256=correct_hash,
    )

    is_stale, code, reason = detect_stale_timeline(valid_plan, mat_tl)
    assert not is_stale
    assert code is None

    # Stale due to mismatched timeline_id
    bad_id_plan = valid_plan.model_copy(update={"timeline_id": "tl-other"})
    is_stale, code, reason = detect_stale_timeline(bad_id_plan, mat_tl)
    assert is_stale
    assert code == StaleDefectCode.STALE_TIMELINE

    # Stale due to mismatched version
    bad_ver_plan = valid_plan.model_copy(update={"timeline_version": "v2.0"})
    is_stale, code, reason = detect_stale_timeline(bad_ver_plan, mat_tl)
    assert is_stale
    assert code == StaleDefectCode.STALE_TIMELINE

    # Stale due to mismatched sha256 (e.g. underlying assets modified)
    bad_hash_plan = valid_plan.model_copy(update={"timeline_sha256": "outdated_hash_12345"})
    is_stale, code, reason = detect_stale_timeline(bad_hash_plan, mat_tl)
    assert is_stale
    assert code == StaleDefectCode.STALE_TIMELINE


def test_scene_version_stale_detection():
    """Verify detect_stale_scene_version detects scene version increments."""
    scene = MaterializedScene(
        scene_id="scene-02",
        scene_version=1,
        order=2,
        timing=MaterializedTiming(start_time_sec=5.0, end_time_sec=10.0, duration_sec=5.0),
        narration=MaterializedNarration(text="Old scene version", audio_artifact_path="/audio.wav"),
        visual_requirements=VisualRequirements(visual_concept="Concept"),
        selected_assets=[SelectedAsset(asset_id="a2", asset_path="/asset2.mp4")],
        audio_plan=MaterializedAudioPlan(voice_path="/audio.wav", voice_duration_sec=5.0),
    )

    # Matching version
    is_stale, code, reason = detect_stale_scene_version(scene, expected_version=1)
    assert not is_stale

    # Mismatched version (e.g. regenerated to v2)
    is_stale, code, reason = detect_stale_scene_version(scene, expected_version=2)
    assert is_stale
    assert code == StaleDefectCode.STALE_ASSET


def test_legacy_script_document_adapter():
    """Verify script_document_to_intent_timeline converts existing ScriptDocument cleanly."""
    script_doc = ScriptDocument(
        content_id="content-leg-01",
        topic="3 Incredible Ocean Facts",
        working_title="Ocean Wonders",
        hook="Did you know this about the deep sea?",
        target_platform="youtube",
        scenes=[
            ScriptScene(
                scene_id="scene-01",
                order=1,
                narration="First fact about the Mariana Trench.",
                visual_intent="Deep sea exploration submarine light",
                asset_query="mariana trench submarine",
                estimated_duration_seconds=5.5,
                emphasis_words=["Mariana", "Trench"],
                transition_hint="cut",
            ),
            ScriptScene(
                scene_id="scene-02",
                order=2,
                narration="Second fact about glowing jellyfish.",
                visual_intent="Bioluminescent jellyfish dark ocean",
                asset_query="bioluminescent jellyfish",
                estimated_duration_seconds=6.0,
                emphasis_words=["glowing"],
                transition_hint="fade",
            ),
        ],
    )

    intent_tl = script_document_to_intent_timeline(
        script=script_doc,
        job_id="job-legacy-01",
        profile_id="viral_creator",
        content_type="LISTICLE",
    )

    assert intent_tl.job_id == "job-legacy-01"
    assert len(intent_tl.scenes) == 2
    assert intent_tl.scenes[0].scene_id == "scene-01"
    assert intent_tl.scenes[0].narrative_role == NarrativeRole.HOOK
    assert intent_tl.scenes[1].narrative_role == NarrativeRole.CTA
    assert intent_tl.scenes[0].narration.text == "First fact about the Mariana Trench."
    assert intent_tl.scenes[0].caption_intent.emphasis_words == ["Mariana", "Trench"]
    assert intent_tl.scenes[0].timing.target_duration_sec == 5.5
    assert intent_tl.total_target_duration_sec == 11.5
