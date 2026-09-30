"""Unit Tests for Phase 5 — Multi-Modal Creative QA, Defect Classification & Targeted Regeneration."""
from __future__ import annotations

import pytest
from pathlib import Path

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
    TrimRange,
    VisualRequirements,
    WordTimestamp,
)
from autopilot.core.creative_qa import (
    CreativeQAEngine,
    CreativeQAReport,
    CreativeQAStatus,
)
from autopilot.core.defect_classifier import (
    DefectClassifierEngine,
    DefectItem,
    DefectSeverity,
    RegenerationTargetType,
)
from autopilot.core.targeted_regeneration import TargetedRegenerationController
from autopilot.core.publish_readiness import (
    PublishReadinessDecision,
    PublishReadinessGate,
    PublishReadinessStatus,
)
from autopilot.core.creative_benchmark import (
    BENCHMARK_60_CORPUS,
    CreativeBenchmarkRunner,
)


@pytest.fixture
def sample_materialized_timeline(tmp_path: Path) -> MaterializedTimeline:
    """Fixture providing a valid 2-scene MaterializedTimeline."""
    img1 = tmp_path / "img1.png"
    img2 = tmp_path / "img2.png"
    img1.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    img2.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    voice1 = tmp_path / "voice1.wav"
    voice2 = tmp_path / "voice2.wav"
    voice1.write_bytes(b"RIFF" + b"\x00" * 100)
    voice2.write_bytes(b"RIFF" + b"\x00" * 100)

    words1 = [
        WordTimestamp(word="Quantum", start=0.1, end=0.8),
        WordTimestamp(word="computing", start=0.8, end=1.5),
        WordTimestamp(word="leaps.", start=1.5, end=2.2),
    ]
    words2 = [
        WordTimestamp(word="This", start=2.5, end=2.9),
        WordTimestamp(word="changes", start=2.9, end=3.6),
        WordTimestamp(word="everything.", start=3.6, end=4.5),
    ]

    scene1 = MaterializedScene(
        scene_id="scene_01_hook",
        order=1,
        narrative_role=NarrativeRole.HOOK,
        timing=MaterializedTiming(start_time_sec=0.0, end_time_sec=2.4, duration_sec=2.4),
        visual_requirements=VisualRequirements(visual_concept="Quantum computing concept"),
        selected_assets=[
            SelectedAsset(
                asset_id="asset_01",
                asset_path=str(img1),
                media_type="image",
                duration_sec=2.4,
                # Recorded by the real CLIP visual-semantic gate in production.
                provenance={"semantic_score": 0.86, "provider": "pexels", "media_kind": "image"},
                crop_framing=CropFraming(saliency_x=0.5, saliency_y=0.45, caption_safe_zone=CaptionPosition.LOWER),
            )
        ],
        narration=MaterializedNarration(text="Quantum computing leaps.", audio_artifact_path=str(voice1), word_timestamps=words1),
        caption_plan=MaterializedCaptionPlan(
            phrases=[CaptionPhrase(phrase_text="Quantum computing leaps.", start_sec=0.1, end_sec=2.2)],
            position=CaptionPosition.LOWER,
        ),
        audio_plan=MaterializedAudioPlan(voice_path=str(voice1), voice_duration_sec=2.4, sfx_events=[SFXEvent(cue="impact", time_sec=0.05)]),
    )

    scene2 = MaterializedScene(
        scene_id="scene_02_reveal",
        order=2,
        narrative_role=NarrativeRole.REVEAL,
        timing=MaterializedTiming(start_time_sec=2.4, end_time_sec=5.0, duration_sec=2.6),
        visual_requirements=VisualRequirements(visual_concept="Quantum reveal concept"),
        selected_assets=[
            SelectedAsset(
                asset_id="asset_02",
                asset_path=str(img2),
                media_type="image",
                duration_sec=2.6,
                provenance={"semantic_score": 0.84, "provider": "pexels", "media_kind": "image"},
                crop_framing=CropFraming(saliency_x=0.5, saliency_y=0.55, caption_safe_zone=CaptionPosition.TOP),
            )
        ],
        narration=MaterializedNarration(text="This changes everything.", audio_artifact_path=str(voice2), word_timestamps=words2),
        caption_plan=MaterializedCaptionPlan(
            phrases=[CaptionPhrase(phrase_text="This changes everything.", start_sec=2.5, end_sec=4.5)],
            position=CaptionPosition.TOP,
        ),
        audio_plan=MaterializedAudioPlan(voice_path=str(voice2), voice_duration_sec=2.6, sfx_events=[SFXEvent(cue="whoosh_fast", time_sec=0.0)]),
    )

    return MaterializedTimeline(
        timeline_id="mt-test-prod-p5",
        job_id="job-test-prod-p5",
        intent_timeline_id="it-test-prod-p5",
        total_measured_duration_sec=5.0,
        target_resolution="1080x1920",
        scenes=[scene1, scene2],
    )


def test_creative_qa_evaluation_report(sample_materialized_timeline: MaterializedTimeline):
    """Verify that CreativeQAEngine evaluates all 10 creative dimensions and produces a valid scorecard."""
    engine = CreativeQAEngine()
    report = engine.evaluate_production(sample_materialized_timeline)

    assert report.report_id.startswith("cqa-")
    assert 0.0 <= report.overall_score <= 100.0
    assert report.overall_status in (CreativeQAStatus.PASS, CreativeQAStatus.WARN)
    assert report.confidence >= 0.80

    # Verify all 10 creative dimensions exist and have evidence
    assert report.hook_effectiveness.score > 70.0
    assert report.visual_match.score > 70.0
    assert report.pacing_cadence.score > 70.0
    assert report.caption_readability.score > 70.0
    assert report.caption_placement.score > 70.0
    assert report.narrative_coherence.score > 70.0
    assert report.audio_balance.score > 70.0
    assert report.visual_continuity.score == 100.0
    assert report.dead_static_sections.score == 100.0


def test_defect_classifier_mapping(sample_materialized_timeline: MaterializedTimeline):
    """Verify that DefectClassifier transforms QA scorecard anomalies into machine-readable defect items."""
    # Inject a known visual mismatch defect
    sample_materialized_timeline.scenes[0].selected_assets[0].asset_path = "/nonexistent/asset.mp4"
    engine = CreativeQAEngine()
    report = engine.evaluate_production(sample_materialized_timeline)

    classifier = DefectClassifierEngine()
    defects = classifier.classify_defects(report)

    assert len(defects) > 0
    mismatch_defect = next((d for d in defects if "VISUAL_MISMATCH" in d.code), None)
    assert mismatch_defect is not None
    assert mismatch_defect.scene_id == "scene_01_hook"
    assert mismatch_defect.regeneration_target == RegenerationTargetType.VISUAL_ASSET
    assert "asset path does not exist" in mismatch_defect.evidence.lower()


def test_targeted_regeneration_visual_surgical(sample_materialized_timeline: MaterializedTimeline, tmp_path: Path):
    """Verify that targeted visual regeneration updates only the target scene and preserves unaffected scenes."""
    controller = TargetedRegenerationController()
    new_img = tmp_path / "replacement.png"
    new_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

    new_asset = SelectedAsset(
        asset_id="asset_01_new",
        asset_path=str(new_img),
        media_type="image",
        duration_sec=2.4,
    )

    updated = controller.regenerate_scene_visual(sample_materialized_timeline, "scene_01_hook", new_asset)

    # Scene 1 updated
    assert updated.scenes[0].selected_assets[0].asset_id == "asset_01_new"
    # Scene 2 strictly preserved
    assert updated.scenes[1].selected_assets[0].asset_id == "asset_02"
    assert updated.scenes[1].narration.text == "This changes everything."
    # Lineage incremented
    assert updated.timeline_version != "v1.0"
    assert updated.parent_timeline_version == "v1.0"
    assert "Regenerated visual for scene_01_hook" in updated.audio_mix_manifest.get("last_invalidation_reason", "")


def test_targeted_regeneration_voice_realigns_captions(sample_materialized_timeline: MaterializedTimeline, tmp_path: Path):
    """Verify that targeted voice regeneration realigns word timestamps and regenerates captions."""
    controller = TargetedRegenerationController()
    new_voice = tmp_path / "new_voice_1.wav"
    new_voice.write_bytes(b"RIFF" + b"\x00" * 50)

    new_words = [
        WordTimestamp(word="Brand", start=0.1, end=0.6),
        WordTimestamp(word="new", start=0.6, end=1.1),
        WordTimestamp(word="breakthrough.", start=1.1, end=2.0),
    ]

    updated = controller.regenerate_scene_voice(
        timeline=sample_materialized_timeline,
        scene_id="scene_01_hook",
        new_voice_path=str(new_voice),
        new_word_timestamps=new_words,
        new_duration_sec=2.0,
        text_override="Brand new breakthrough.",
    )

    assert updated.scenes[0].narration.text == "Brand new breakthrough."
    assert updated.scenes[0].narration.audio_artifact_path == str(new_voice)
    assert len(updated.scenes[0].caption_plan.phrases) > 0
    assert "breakthrough" in updated.scenes[0].caption_plan.phrases[0].phrase_text.lower()


def test_publish_readiness_gate_evaluation(sample_materialized_timeline: MaterializedTimeline):
    """Verify that PublishReadinessGate evaluates composite technical, creative, and rights gates."""
    engine = CreativeQAEngine()
    report = engine.evaluate_production(sample_materialized_timeline)
    classifier = DefectClassifierEngine()
    defects = classifier.classify_defects(report)

    gate = PublishReadinessGate()
    decision = gate.evaluate(
        creative_report=report,
        defects=defects,
        technical_report=None,
        rights_verified=True,
        invariants_verified=True,
    )

    assert decision.is_ready_to_publish is True
    assert decision.status == PublishReadinessStatus.READY
    assert len(decision.blocking_reasons) == 0


def test_publish_readiness_gate_blocks_on_rights_failure(sample_materialized_timeline: MaterializedTimeline):
    """Verify that hard legal/rights failures block publication and cannot be overridden."""
    engine = CreativeQAEngine()
    report = engine.evaluate_production(sample_materialized_timeline)
    gate = PublishReadinessGate()

    decision = gate.evaluate(
        creative_report=report,
        defects=[],
        rights_verified=False,  # Rights failure
        invariants_verified=True,
    )

    assert decision.is_ready_to_publish is False
    assert decision.status == PublishReadinessStatus.BLOCKED
    assert decision.can_override_with_human_approval is False
    assert "Commercial rights" in decision.blocking_reasons[0]


def test_publish_readiness_gate_human_review_flow(sample_materialized_timeline: MaterializedTimeline):
    """Verify that low-confidence / human review status halts publication until explicit approval."""
    engine = CreativeQAEngine()
    report = engine.evaluate_production(sample_materialized_timeline)
    report.overall_status = CreativeQAStatus.HUMAN_REVIEW
    report.human_review_required = True

    gate = PublishReadinessGate()
    # Unapproved
    dec1 = gate.evaluate(creative_report=report, defects=[], human_review_approved=False)
    assert dec1.is_ready_to_publish is False
    assert dec1.status == PublishReadinessStatus.PENDING_HUMAN_REVIEW

    # Approved by human operator
    dec2 = gate.evaluate(creative_report=report, defects=[], human_review_approved=True)
    assert dec2.is_ready_to_publish is True
    assert dec2.status == PublishReadinessStatus.READY


def test_benchmark_60_corpus_coverage():
    """Verify that the 60-topic benchmark corpus contains 10 topics across all 6 specified categories."""
    categories = ["Science", "History", "Technology", "Geography", "Listicles", "Explainers & Myths"]
    assert len(BENCHMARK_60_CORPUS) == 60

    for cat in categories:
        corpus = CreativeBenchmarkRunner.get_corpus(cat)
        assert len(corpus) == 10, f"Category '{cat}' does not have exactly 10 topics (found {len(corpus)})"


def test_benchmark_negative_defect_fixtures():
    """Verify that negative defect fixtures trigger expected defect classifications in Creative QA."""
    engine = CreativeQAEngine()
    classifier = DefectClassifierEngine()

    # 1. Bad crop fixture
    bad_crop_tl = CreativeBenchmarkRunner.create_negative_defect_fixture("bad_crop")
    r1 = engine.evaluate_production(bad_crop_tl)
    d1 = classifier.classify_defects(r1)
    assert any("BAD_CROP" in d.code for d in d1)

    # 2. Caption overflow fixture
    overflow_tl = CreativeBenchmarkRunner.create_negative_defect_fixture("caption_overflow")
    r2 = engine.evaluate_production(overflow_tl)
    d2 = classifier.classify_defects(r2)
    assert any("CAPTION_OVERFLOW" in d.code for d in d2)

    # 3. Pacing drag fixture
    pacing_tl = CreativeBenchmarkRunner.create_negative_defect_fixture("pacing_drag")
    r3 = engine.evaluate_production(pacing_tl)
    d3 = classifier.classify_defects(r3)
    assert any("PACING" in d.code for d in d3)


def test_golden_master_comparison():
    """Verify that Golden Master comparison computes score deltas accurately."""
    tl = CreativeBenchmarkRunner.create_negative_defect_fixture("pacing_drag")
    engine = CreativeQAEngine()
    report_a = engine.evaluate_production(tl)
    report_b = engine.evaluate_production(tl)
    report_b.overall_score = 92.0

    comp = CreativeBenchmarkRunner.compare_productions(report_a, report_b)
    assert comp.score_delta > 0
    assert comp.winner == report_b.production_id
