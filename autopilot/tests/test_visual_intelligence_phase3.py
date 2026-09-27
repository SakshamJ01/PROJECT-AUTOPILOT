"""Phase 3 Test Suite — Visual Intelligence & Semantic Asset Engine.

Validates:
  1. Multi-Tier Visual Sourcing (Pexels, Pixabay, Infographics, Openverse, Local) & Provider Fallback
  2. Hard Rights & Quality Gates (License, Corruption, Effective Resolution, Watermark, Blur, Duration)
  3. Semantic Visual Scoring & Explicit Coverage Classes (EXACT, STRONG, CONTEXTUAL, WEAK, MISMATCH)
  4. Visual Assertion Awareness (Literal, Schematic, Illustrative, Metaphorical)
  5. Saliency-Aware 9:16 Framing & Dynamic Caption Safe Zone Positioning (TOP, CENTER, LOWER)
  6. Visual Diversity & pHash Duplicate Protection
  7. Shot Continuity Progression & Cinematic Flow
  8. Visual Change Budgeting (> 4.5s multi-shot planning)
  9. Machine-Readable Provenance Preservation
  10. MaterializedTimeline Integration & TimelineCompiler compatibility
"""
from __future__ import annotations

import tempfile
from pathlib import Path
import pytest
import numpy as np
from PIL import Image, ImageDraw

from autopilot.core.contracts import (
    AssetCandidate, AssetLicense, AssetDimensions, AssetProvenance
)
from autopilot.core.timeline import (
    IntentTimeline, IntentScene, TargetTiming, IntentNarration,
    VisualRequirements, CaptionIntent, AudioIntent, TransitionIntent,
    QAExpectations, MaterializedTimeline, MaterializedScene,
    MaterializedTiming, MaterializedNarration, MaterializedAudioPlan,
    MaterializedCaptionPlan, MaterializedTransitionPlan,
    ShotType, VisualCoverageClass, VisualAssertionLevel,
    CaptionPosition, SelectedAsset, TrimRange, CropFraming
)
from autopilot.core.visual_intelligence import (
    VisualIntelligenceEngine,
    evaluate_hard_quality_gates,
    compute_semantic_visual_score,
    compute_laplacian_variance,
    compute_saliency_and_safe_zone,
    compute_phash_hamming_distance,
    VisualDiversityTracker,
    ShotContinuityPlanner,
    HardGateResult,
    VisualScoreResult,
)
from autopilot.providers.pexels_provider import PexelsAssetProvider
from autopilot.providers.pixabay_provider import PixabayAssetProvider
from autopilot.core.infographics_generator import InfographicsAssetProvider, InfographicsGenerator
from autopilot.providers.openverse_provider import OpenverseAssetProvider
from autopilot.providers.local_asset_provider import LocalAssetProvider
from autopilot.core.timeline_compiler import TimelineCompiler


@pytest.fixture
def temp_dir(tmp_path):
    return tmp_path


@pytest.fixture
def sample_test_image(temp_dir):
    """Generate a clean test image with a high-contrast rectangle in the lower region."""
    img_path = temp_dir / "test_frame.png"
    img = Image.new("RGB", (1080, 1920), color=(30, 30, 40))
    draw = ImageDraw.Draw(img)
    # Bright high-energy block in lower half (y: 1200 - 1600)
    draw.rectangle([200, 1200, 880, 1600], fill=(255, 200, 50), outline=(255, 255, 255), width=5)
    img.save(str(img_path))
    return img_path


@pytest.fixture
def sample_blurry_image(temp_dir):
    """Generate an extremely blurry flat image."""
    img_path = temp_dir / "blurry_frame.png"
    img = Image.new("RGB", (1080, 1920), color=(128, 128, 128))
    img.save(str(img_path))
    return img_path


# ---------------------------------------------------------------------------
# 1. Multi-Tier Providers & Fallback
# ---------------------------------------------------------------------------

def test_pexels_provider_graceful_health_and_search_without_key():
    prov = PexelsAssetProvider(api_key="")
    health = prov.health_check()
    assert health.healthy is False
    assert "not configured" in health.error

    results = prov.search({"query": "cyberpunk city"})
    assert results == []


def test_pixabay_provider_graceful_health_and_search_without_key():
    prov = PixabayAssetProvider(api_key="")
    health = prov.health_check()
    assert health.healthy is False
    assert "not configured" in health.error

    results = prov.search({"query": "deep space"})
    assert results == []


def test_infographics_generator_and_provider(temp_dir):
    prov = InfographicsAssetProvider(output_dir=temp_dir)
    health = prov.health_check()
    assert health.healthy is True

    cands = prov.search({
        "visual_concept": "Exponential Quantum Growth",
        "headline": "Quantum Computing",
        "stat_value": "1000x",
        "subtext": "Speedup achieved in quantum simulation",
    })
    assert len(cands) == 1
    cand = cands[0]
    assert cand.provenance.provider == "infographics"
    assert cand.dimensions.width == 1080
    assert cand.dimensions.height == 1920
    assert Path(cand.source_url).exists()
    assert cand.license.rights_status == "VERIFIED"
    assert cand.license.commercial_use is True


# ---------------------------------------------------------------------------
# 2. Hard Quality & Rights Gates
# ---------------------------------------------------------------------------

def test_hard_gate_commercial_rights_rejection():
    # Non-commercial license must fail hard gate
    nc_cand = AssetCandidate(
        candidate_id="test_nc",
        asset_type="image",
        source_id="123",
        source_url="http://example.com/nc.jpg",
        title="Restricted Photo",
        license=AssetLicense(
            license_name="CC BY-NC 4.0",
            commercial_use=False,
            derivative_use=True,
            rights_status="REJECTED",
        ),
        provenance=AssetProvenance(provider="openverse"),
    )
    res = evaluate_hard_quality_gates(nc_cand)
    assert res.passed is False
    assert any("Rights Gate" in r for r in res.reasons)


def test_hard_gate_watermark_rejection():
    wm_cand = AssetCandidate(
        candidate_id="test_wm",
        asset_type="image",
        source_id="124",
        source_url="http://example.com/wm.jpg",
        title="Stock Photo Preview Shutterstock",
        tags=["watermark", "sample"],
        license=AssetLicense(
            license_name="CC0",
            commercial_use=True,
            derivative_use=True,
            rights_status="VERIFIED",
        ),
        provenance=AssetProvenance(provider="openverse"),
    )
    res = evaluate_hard_quality_gates(wm_cand)
    assert res.passed is False
    assert any("Watermark" in r for r in res.reasons)


def test_hard_gate_effective_resolution_rule():
    # 4K Landscape (3840x2160) supports 9:16 vertical crop -> PASS
    cand_4k = AssetCandidate(
        candidate_id="test_4k",
        asset_type="video",
        source_id="4k",
        source_url="http://example.com/4k.mp4",
        title="4K Drone Shot",
        dimensions=AssetDimensions(width=3840, height=2160),
        license=AssetLicense(license_name="Pexels", commercial_use=True, rights_status="VERIFIED"),
        provenance=AssetProvenance(provider="pexels"),
    )
    res_4k = evaluate_hard_quality_gates(cand_4k)
    assert res_4k.passed is True
    assert res_4k.effective_resolution_ok is True

    # Extremely low resolution (200x200) -> FAIL
    cand_low = AssetCandidate(
        candidate_id="test_low",
        asset_type="image",
        source_id="low",
        source_url="http://example.com/low.jpg",
        title="Low res thumbnail",
        dimensions=AssetDimensions(width=200, height=200),
        license=AssetLicense(license_name="CC0", commercial_use=True, rights_status="VERIFIED"),
        provenance=AssetProvenance(provider="openverse"),
    )
    res_low = evaluate_hard_quality_gates(cand_low)
    assert res_low.passed is False
    assert any("resolution" in r.lower() for r in res_low.reasons)


def test_hard_gate_blur_rejection(sample_test_image, sample_blurry_image):
    cand = AssetCandidate(
        candidate_id="test_sharp",
        asset_type="image",
        source_id="sharp",
        source_url=str(sample_test_image),
        path_local=str(sample_test_image),
        title="Sharp Frame",
        license=AssetLicense(license_name="CC0", commercial_use=True, rights_status="VERIFIED"),
        provenance=AssetProvenance(provider="local"),
    )
    # Sharp image passes
    res_sharp = evaluate_hard_quality_gates(cand, local_path=sample_test_image, min_blur_threshold=80.0)
    assert res_sharp.passed is True

    # Blurry flat image fails
    res_blurry = evaluate_hard_quality_gates(cand, local_path=sample_blurry_image, min_blur_threshold=80.0)
    assert res_blurry.passed is False
    assert any("blur" in r.lower() for r in res_blurry.reasons)


def test_hard_gate_video_duration_short_rejection():
    cand_short = AssetCandidate(
        candidate_id="test_short_vid",
        asset_type="video",
        source_id="short",
        source_url="http://example.com/short.mp4",
        title="Short clip",
        dimensions=AssetDimensions(duration_sec=2.0),
        license=AssetLicense(license_name="Pexels", commercial_use=True, rights_status="VERIFIED"),
        provenance=AssetProvenance(provider="pexels"),
    )
    # Required scene duration is 6.0s -> 2.0s is rejected
    res = evaluate_hard_quality_gates(cand_short, target_duration=6.0)
    assert res.passed is False
    assert any("duration" in r.lower() for r in res.reasons)


# ---------------------------------------------------------------------------
# 3. Semantic Visual Scoring & Assertion Levels
# ---------------------------------------------------------------------------

def test_semantic_visual_scoring_coverage_classes():
    req_exact = VisualRequirements(
        visual_concept="Saturn Rings Cassini Spacecraft",
        b_roll_search_query="Saturn rings Cassini probe exploration",
        required_shot_type=ShotType.AERIAL,
        visual_assertion_level=VisualAssertionLevel.LITERAL,
    )

    # Candidate with exact keywords
    cand_exact = AssetCandidate(
        candidate_id="cand_exact",
        asset_type="video",
        source_id="1",
        source_url="http://example.com/saturn.mp4",
        title="Cassini probe approaching Saturn rings footage",
        tags=["saturn", "rings", "cassini", "spacecraft", "aerial"],
        dimensions=AssetDimensions(width=1080, height=1920),
        license=AssetLicense(license_name="Pexels", commercial_use=True, rights_status="VERIFIED"),
        provenance=AssetProvenance(provider="pexels"),
    )
    res_exact = compute_semantic_visual_score(cand_exact, req_exact)
    assert res_exact.coverage_class in (VisualCoverageClass.EXACT_MATCH, VisualCoverageClass.STRONG_MATCH)
    assert res_exact.total_score >= 0.70
    assert "lexical_relevance" in res_exact.breakdown

    # Unrelated candidate -> MISMATCH
    cand_mismatch = AssetCandidate(
        candidate_id="cand_mismatch",
        asset_type="image",
        source_id="2",
        source_url="http://example.com/cat.jpg",
        title="Cute sleeping cat on sofa",
        tags=["cat", "pet", "animal"],
        dimensions=AssetDimensions(width=800, height=800),
        license=AssetLicense(license_name="CC0", commercial_use=True, rights_status="VERIFIED"),
        provenance=AssetProvenance(provider="openverse"),
    )
    res_mismatch = compute_semantic_visual_score(cand_mismatch, req_exact)
    assert res_mismatch.coverage_class == VisualCoverageClass.MISMATCH
    assert res_mismatch.total_score < 0.40


def test_visual_assertion_level_awareness():
    # Abstract metaphorical concept
    req_abstract = VisualRequirements(
        visual_concept="Inflation compounding over decades",
        b_roll_search_query="Stack of hourglass sand running out",
        required_shot_type=ShotType.CLOSE_UP,
        visual_assertion_level=VisualAssertionLevel.METAPHORICAL,
    )
    cand_meta = AssetCandidate(
        candidate_id="cand_hourglass",
        asset_type="video",
        source_id="hg1",
        source_url="http://example.com/hourglass.mp4",
        title="Close up hourglass sand flowing time",
        tags=["hourglass", "sand", "time", "close"],
        dimensions=AssetDimensions(width=1080, height=1920),
        license=AssetLicense(license_name="Pexels", commercial_use=True, rights_status="VERIFIED"),
        provenance=AssetProvenance(provider="pexels"),
    )
    res_meta = compute_semantic_visual_score(cand_meta, req_abstract)
    assert res_meta.coverage_class in (VisualCoverageClass.STRONG_MATCH, VisualCoverageClass.CONTEXTUAL_MATCH)
    assert res_meta.contextual_allowed_reason is not None
    assert "metaphorical" in res_meta.contextual_allowed_reason.lower()


# ---------------------------------------------------------------------------
# 4. Saliency Framing & Dynamic Caption Safe Zone
# ---------------------------------------------------------------------------

def test_saliency_and_safe_zone_detection(sample_test_image):
    # sample_test_image has high energy in lower half (y: 1200-1600)
    center_x, center_y, safe_zone = compute_saliency_and_safe_zone(sample_test_image)
    assert 0.0 <= center_x <= 1.0
    assert 0.0 <= center_y <= 1.0
    assert center_y > 0.55  # Energy is located in bottom half
    # Because subject is at bottom, caption safe zone must be moved to TOP!
    assert safe_zone == CaptionPosition.TOP


# ---------------------------------------------------------------------------
# 5. Visual Diversity & pHash Duplicate Protection
# ---------------------------------------------------------------------------

def test_visual_diversity_and_phash_hamming():
    tracker = VisualDiversityTracker()
    phash_a = "ffff0000ffff0000"
    phash_near_a = "ffff0000ffff0001"  # 1 bit difference
    phash_b = "0000ffff0000ffff"      # 64 bit difference

    assert compute_phash_hamming_distance(phash_a, phash_near_a) == 1
    assert compute_phash_hamming_distance(phash_a, phash_b) == 64

    tracker.register_selection("asset_1", phash_a, "pexels", "ocean waves")
    assert tracker.is_duplicate("asset_1", phash_a) is True
    assert tracker.is_duplicate("asset_2", phash_near_a) is True  # Near duplicate flagged!
    assert tracker.is_duplicate("asset_3", phash_b) is False      # Different asset allowed


# ---------------------------------------------------------------------------
# 6. Shot Continuity Progression
# ---------------------------------------------------------------------------

def test_shot_continuity_planner():
    planner = ShotContinuityPlanner()
    # First scene establishes wide
    bonus1 = planner.evaluate_progression(ShotType.WIDE)
    assert bonus1 > 0.0

    # Moving to medium is favored progression
    bonus2 = planner.evaluate_progression(ShotType.MEDIUM)
    assert bonus2 >= 0.05

    # Moving to same shot type gets no bonus
    bonus3 = planner.evaluate_progression(ShotType.MEDIUM)
    assert bonus3 == 0.0


# ---------------------------------------------------------------------------
# 7. Visual Change Budgeting & Multi-Shot Planning
# ---------------------------------------------------------------------------

def test_visual_change_budgeting_long_scenes(temp_dir):
    engine = VisualIntelligenceEngine(cache_dir=temp_dir)

    # Intent Scene with duration 8.0s and visual_change_budget = 2
    timeline = MaterializedTimeline(
        timeline_id="mat_test_budget",
        job_id="job_budget",
        intent_timeline_id="intent_budget",
        total_measured_duration_sec=8.0,
        scenes=[
            MaterializedScene(
                scene_id="scene_long",
                order=1,
                timing=MaterializedTiming(start_time_sec=0.0, end_time_sec=8.0, duration_sec=8.0),
                narration=MaterializedNarration(
                    text="This is an extended narrative scene exploring complex topics across time.",
                    audio_artifact_path=str(temp_dir / "audio.wav"),
                ),
                visual_requirements=VisualRequirements(
                    visual_concept="Deep Space Galaxy Exploration",
                    b_roll_search_query="galaxy nebula deep space starfield",
                    required_shot_type=ShotType.WIDE,
                    visual_change_budget=2,
                ),
                audio_plan=MaterializedAudioPlan(
                    voice_path=str(temp_dir / "audio.wav"),
                    voice_duration_sec=8.0,
                ),
            )
        ],
    )

    engine.process_materialized_timeline(timeline, download_media=False)
    scene = timeline.scenes[0]
    assert len(scene.selected_assets) == 2  # Split into 2 sub-shots!
    shot1, shot2 = scene.selected_assets[0], scene.selected_assets[1]
    assert shot1.duration_sec == 4.0
    assert shot1.start_offset_sec == 0.0
    assert shot2.duration_sec == 4.0
    assert shot2.start_offset_sec == 4.0


# ---------------------------------------------------------------------------
# 8. Machine-Readable Provenance Preservation & MaterializedTimeline Integration
# ---------------------------------------------------------------------------

def test_materialized_timeline_provenance_and_compiler_handoff(temp_dir):
    engine = VisualIntelligenceEngine(cache_dir=temp_dir)

    # Create synthetic test audio file
    fake_audio = temp_dir / "voice.wav"
    # Simple valid 44.1kHz stereo WAV header + silent PCM payload
    import wave
    with wave.open(str(fake_audio), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(b"\x00\x00" * 44100 * 4)  # 4 seconds

    timeline = MaterializedTimeline(
        timeline_id="mat_test_prov",
        job_id="job_prov",
        intent_timeline_id="intent_prov",
        total_measured_duration_sec=4.0,
        scenes=[
            MaterializedScene(
                scene_id="scene_01",
                order=1,
                timing=MaterializedTiming(start_time_sec=0.0, end_time_sec=4.0, duration_sec=4.0),
                narration=MaterializedNarration(
                    text="Quantum computers process vast information simultaneously.",
                    audio_artifact_path=str(fake_audio),
                ),
                visual_requirements=VisualRequirements(
                    visual_concept="Quantum Microchip Processor",
                    b_roll_search_query="quantum processor microchip glowing circuitry",
                    required_shot_type=ShotType.CLOSE_UP,
                    visual_assertion_level=VisualAssertionLevel.LITERAL,
                ),
                audio_plan=MaterializedAudioPlan(
                    voice_path=str(fake_audio),
                    voice_duration_sec=4.0,
                    sample_rate=44100,
                    channels=2,
                ),
            )
        ],
    )

    # Process visual intelligence
    engine.process_materialized_timeline(timeline, download_media=True)
    scene = timeline.scenes[0]

    assert len(scene.selected_assets) == 1
    selected = scene.selected_assets[0]
    assert selected.asset_id is not None
    assert Path(selected.asset_path).exists()
    assert selected.crop_framing is not None
    assert selected.crop_framing.caption_safe_zone in (CaptionPosition.TOP, CaptionPosition.CENTER, CaptionPosition.LOWER)

    # Verify Provenance
    prov = selected.provenance
    assert prov["provider"] in ("infographics", "local", "openverse", "pexels", "pixabay")
    assert "license_name" in prov
    assert "rights_status" in prov
    assert "download_hash" in prov
    assert "scoring" in prov
    assert prov["scoring"]["total_score"] > 0.0

    # Compile with TimelineCompiler to verify RenderPlan generation and invariant checks
    compiler = TimelineCompiler()
    render_plan = compiler.compile(timeline)
    assert render_plan is not None
    assert render_plan.job_id == "job_prov"
    assert len(render_plan.scenes) >= 1
    assert render_plan.mpt_handoff is not None
    assert len(render_plan.mpt_handoff.video_materials) >= 1
    assert render_plan.render_plan_sha256 is not None
