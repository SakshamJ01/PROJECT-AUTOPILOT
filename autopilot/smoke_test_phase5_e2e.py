"""Phase 5 Multi-Modal Creative QA & Targeted Regeneration Real End-to-End Smoke Test.

This test validates:
1. Real multi-scene video generation (2 scenes with mastered audio, word timestamps, kinetic captions, BGM & SFX).
2. Initial physical rendering using FFmpeg.
3. Technical QA validation.
4. Multi-modal Creative QA evaluating 10 dimensions.
5. Injected visual mismatch defect detection via Defect Classifier.
6. Publish Readiness Gate blocking publication on defect presence.
7. Surgical Targeted Regeneration replacing only Scene 2 visual while preserving Scene 1.
8. Recompilation into a fresh sealed RenderPlan (v1.1) and physical re-render.
9. Re-QA passing cleanly with Publish Readiness Gate approving release.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent))

from autopilot.core.config import CONFIG
from autopilot.core.audio_mastering import AudioMasteringEngine
from autopilot.core.infographics_generator import InfographicsGenerator
from autopilot.core.kinetic_typography import KineticTypographyEngine
from autopilot.core.audio_scene_graph import (
    generate_ambient_bgm_track,
    generate_sfx_sample,
    synthesize_procedural_wav,
)
from autopilot.core.timeline import (
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
    VisualAssertionLevel,
    VisualCoverageClass,
    VisualRequirements,
    WordTimestamp,
)
from autopilot.core.timeline_compiler import TimelineCompiler
from autopilot.core.renderer import FFmpegRenderer
from autopilot.core.creative_qa import CreativeQAEngine
from autopilot.core.defect_classifier import DefectClassifierEngine, RegenerationTargetType
from autopilot.core.targeted_regeneration import TargetedRegenerationController
from autopilot.core.publish_readiness import PublishReadinessGate, PublishReadinessStatus


def main():
    print("=" * 70)
    print("STARTING PHASE 5 REAL END-TO-END QA & TARGETED REGENERATION SMOKE TEST")
    print("=" * 70)

    artifacts_dir = CONFIG.get_artifacts_dir() / "smoke_phase5_e2e"
    assets_dir = artifacts_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    mastering_engine = AudioMasteringEngine()
    compiler = TimelineCompiler()
    renderer = FFmpegRenderer()
    qa_engine = CreativeQAEngine(sample_interval_sec=1.0)
    defect_classifier = DefectClassifierEngine()
    regen_controller = TargetedRegenerationController()
    publish_gate = PublishReadinessGate()

    # -------------------------------------------------------------
    # 1. Synthesize Mastered Audio for 2 Scenes
    # -------------------------------------------------------------
    print("\n[Step 1] Synthesizing & Mastering Scene Voice Stems...")
    raw_v1 = artifacts_dir / "voice_s1_raw.wav"
    raw_v2 = artifacts_dir / "voice_s2_raw.wav"
    mastered_v1 = artifacts_dir / "voice_s1_mastered.wav"
    mastered_v2 = artifacts_dir / "voice_s2_mastered.wav"

    synthesize_procedural_wav(raw_v1, duration_sec=3.0, generator_func=lambda t, d: 0.3 * math.sin(2 * math.pi * 220.0 * t))
    synthesize_procedural_wav(raw_v2, duration_sec=3.5, generator_func=lambda t, d: 0.3 * math.sin(2 * math.pi * 280.0 * t))

    m1 = mastering_engine.master_audio(raw_v1, mastered_v1)
    m2 = mastering_engine.master_audio(raw_v2, mastered_v2)
    print(f"  Scene 1 Voice: {mastered_v1.name} ({m1.duration_sec:.2f}s, {m1.sample_rate}Hz stereo)")
    print(f"  Scene 2 Voice: {mastered_v2.name} ({m2.duration_sec:.2f}s, {m2.sample_rate}Hz stereo)")

    # -------------------------------------------------------------
    # 2. Generate Visual Assets (Scene 1: Valid, Scene 2: Intentionally Mismatched)
    # -------------------------------------------------------------
    print("\n[Step 2] Generating Visual Assets...")
    info_gen = InfographicsGenerator(width=1080, height=1920)

    asset1_path = assets_dir / "scene1_hook_visual.png"
    asset2_defective_path = assets_dir / "scene2_defective_visual.png"
    asset2_corrected_path = assets_dir / "scene2_corrected_visual.png"

    info_gen.generate_stat_card(
        headline="QUANTUM INTEL",
        stat_value="STOP SCROLLING",
        subtext="Crucial breakthrough revealed",
        out_path=asset1_path,
        accent_color=(255, 69, 0),
    )
    # Defective asset (Mismatched Cat Meme Card)
    info_gen.generate_stat_card(
        headline="UNRELATED MEME",
        stat_value="CAT CARD",
        subtext="Completely irrelevant footage",
        out_path=asset2_defective_path,
        accent_color=(128, 128, 128),
    )
    # Corrected high-relevance asset for later targeted regeneration
    info_gen.generate_stat_card(
        headline="QUANTUM LEAP",
        stat_value="1,000x FASTER",
        subtext="Superconducting Quantum Processor",
        out_path=asset2_corrected_path,
        accent_color=(0, 255, 170),
    )
    print(f"  Asset 1: {asset1_path.name}")
    print(f"  Asset 2 (Defective): {asset2_defective_path.name}")
    print(f"  Asset 2 (Corrected): {asset2_corrected_path.name}")

    # -------------------------------------------------------------
    # 3. Audio Scene Graph Elements (BGM & SFX)
    # -------------------------------------------------------------
    print("\n[Step 3] Generating Audio Scene Graph Elements (BGM & SFX)...")
    bgm_path = artifacts_dir / "bgm_ambient.wav"
    generate_ambient_bgm_track(bgm_path, duration_sec=6.5)
    sfx_impact = generate_sfx_sample("impact", artifacts_dir)
    sfx_whoosh = generate_sfx_sample("whoosh", artifacts_dir)

    # -------------------------------------------------------------
    # 4. Construct Initial MaterializedTimeline (with Injected Defect)
    # -------------------------------------------------------------
    print("\n[Step 4] Constructing Initial MaterializedTimeline (v1.0) with Injected Defect...")
    words_s1 = [
        WordTimestamp(word="Stop", start=0.1, end=0.6, confidence=0.99),
        WordTimestamp(word="scrolling", start=0.6, end=1.2, confidence=0.99),
        WordTimestamp(word="and", start=1.2, end=1.5, confidence=0.98),
        WordTimestamp(word="check", start=1.5, end=2.0, confidence=0.99),
        WordTimestamp(word="this.", start=2.0, end=2.8, confidence=0.99),
    ]
    words_s2 = [
        WordTimestamp(word="This", start=3.1, end=3.5, confidence=0.99),
        WordTimestamp(word="quantum", start=3.5, end=4.1, confidence=0.99),
        WordTimestamp(word="processor", start=4.1, end=4.7, confidence=0.98),
        WordTimestamp(word="changes", start=4.7, end=5.3, confidence=0.99),
        WordTimestamp(word="everything.", start=5.3, end=6.2, confidence=0.99),
    ]

    typo = KineticTypographyEngine()
    phrases_s1 = typo.segment_words_into_phrases(words_s1, min_words=2, max_words=3, emphasis_keywords=["Stop", "check"])
    phrases_s2 = typo.segment_words_into_phrases(words_s2, min_words=2, max_words=3, emphasis_keywords=["quantum", "everything."])

    cplan_s1 = MaterializedCaptionPlan(
        style_preset="hormozi_yellow_pop",
        position=CaptionPosition.CENTER,
        phrases=phrases_s1,
        platform_safe_zone=PlatformSafeZone.YOUTUBE_SHORTS,
    )
    cplan_s2 = MaterializedCaptionPlan(
        style_preset="beast_bold_stroke",
        position=CaptionPosition.LOWER,
        phrases=phrases_s2,
        platform_safe_zone=PlatformSafeZone.YOUTUBE_SHORTS,
    )

    scene_1 = MaterializedScene(
        scene_id="scene_01_hook",
        order=1,
        narrative_role=NarrativeRole.HOOK,
        timing=MaterializedTiming(start_time_sec=0.0, end_time_sec=3.0, duration_sec=3.0),
        narration=MaterializedNarration(
            text="Stop scrolling and check this.",
            audio_artifact_path=str(mastered_v1),
            word_timestamps=words_s1,
        ),
        visual_requirements=VisualRequirements(
            visual_concept="Dramatic hook card with stop scrolling message",
            required_shot_type=ShotType.CLOSE_UP,
            coverage_expectation=VisualCoverageClass.STRONG_MATCH,
            visual_assertion_level=VisualAssertionLevel.LITERAL,
        ),
        selected_assets=[
            SelectedAsset(
                asset_id="asset_s1_hook",
                asset_path=str(asset1_path),
                media_type="image",
                duration_sec=3.0,
                provenance={"license": "commercial_safe", "provider": "procedural", "semantic_score": 0.95},
                crop_framing=CropFraming(saliency_x=0.5, saliency_y=0.25, caption_safe_zone=CaptionPosition.CENTER),
            )
        ],
        caption_plan=cplan_s1,
        audio_plan=MaterializedAudioPlan(
            voice_path=str(mastered_v1),
            voice_duration_sec=3.0,
            sfx_events=[SFXEvent(cue="impact", time_sec=0.1, volume_db=-6.0)],
        ),
    )

    # Injected Defect in Scene 2: Visual requirements specify literal quantum chip, but asset has low semantic match
    scene_2_defective = MaterializedScene(
        scene_id="scene_02_body",
        order=2,
        narrative_role=NarrativeRole.EXPLANATION,
        timing=MaterializedTiming(start_time_sec=3.0, end_time_sec=6.5, duration_sec=3.5),
        narration=MaterializedNarration(
            text="This quantum processor changes everything.",
            audio_artifact_path=str(mastered_v2),
            word_timestamps=words_s2,
        ),
        visual_requirements=VisualRequirements(
            visual_concept="Quantum processor supercomputer motherboard microchip close-up",
            required_shot_type=ShotType.MACRO,
            coverage_expectation=VisualCoverageClass.STRONG_MATCH,
            visual_assertion_level=VisualAssertionLevel.LITERAL,
        ),
        selected_assets=[
            SelectedAsset(
                asset_id="asset_s2_mismatched",
                asset_path=str(asset2_defective_path),
                media_type="image",
                duration_sec=3.5,
                provenance={
                    "license": "commercial_safe",
                    "provider": "procedural",
                    "semantic_score": 0.25,
                    "relevance_note": "MISMATCH: Cat meme card does not depict quantum processor",
                },
                crop_framing=CropFraming(saliency_x=0.5, saliency_y=0.50, caption_safe_zone=CaptionPosition.LOWER),
            )
        ],
        caption_plan=cplan_s2,
        audio_plan=MaterializedAudioPlan(
            voice_path=str(mastered_v2),
            voice_duration_sec=3.5,
            sfx_events=[SFXEvent(cue="whoosh", time_sec=3.0, volume_db=-9.0)],
        ),
    )

    timeline_v1 = MaterializedTimeline(
        timeline_id="mt_smoke_phase5_v1",
        timeline_version="v1.0",
        job_id="job_smoke_phase5",
        intent_timeline_id="it_smoke_phase5",
        total_measured_duration_sec=6.5,
        scenes=[scene_1, scene_2_defective],
        audio_mix_manifest={"bgm_path": str(bgm_path), "bgm_volume_db": -22.0},
    )

    # -------------------------------------------------------------
    # 5. Compile RenderPlan v1.0 & Perform Physical Initial Render
    # -------------------------------------------------------------
    print("\n[Step 5] Compiling RenderPlan v1.0 & Rendering Initial MP4...")
    plan_v1 = compiler.compile(timeline_v1, check_physical_files=True)
    out_video_v1 = artifacts_dir / "render_v1_with_defect.mp4"
    render_res_v1 = renderer.render(plan_v1, str(out_video_v1))
    print(f"  Physical Render v1: {render_res_v1.output_path} ({render_res_v1.duration_sec:.2f}s, {render_res_v1.file_size_bytes} bytes)")
    assert out_video_v1.exists() and out_video_v1.stat().st_size > 0

    # -------------------------------------------------------------
    # 6. Run Creative QA & Defect Classifier on Initial Render
    # -------------------------------------------------------------
    print("\n[Step 6] Running Multi-Modal Creative QA on Render v1.0...")
    qa_report_v1 = qa_engine.evaluate_production(timeline_v1, video_path=out_video_v1)
    print(f"  Creative QA Overall Score: {qa_report_v1.overall_score:.1f}/100.0 (Status: {qa_report_v1.overall_status.value})")
    for name, item in qa_report_v1.get_metrics_dict().items():
        print(f"    - {item.name:30s}: {item.score:5.1f} | Status: {item.status.value:5s} | {item.evidence}")

    print("\n[Step 7] Classifying Defects from QA Findings...")
    defects_v1 = defect_classifier.classify_defects(qa_report_v1)
    print(f"  Detected Defects Count: {len(defects_v1)}")
    for d in defects_v1:
        print(f"    * [{d.severity.value}] {d.code} ({d.regeneration_target.value}) -> {d.evidence}")

    # Evaluate Publish Readiness Gate
    gate_decision_v1 = publish_gate.evaluate(creative_report=qa_report_v1, defects=defects_v1)
    print(f"\n[Step 8] Publish Readiness Gate Decision (v1.0): {gate_decision_v1.status.value}")
    print(f"  Blockers: {gate_decision_v1.blocking_reasons}")
    assert gate_decision_v1.status != PublishReadinessStatus.READY, "Gate must block defective v1.0 render!"

    # -------------------------------------------------------------
    # 7. Surgical Targeted Regeneration of Scene 2 Visual Only
    # -------------------------------------------------------------
    print("\n[Step 9] Executing Surgical Targeted Regeneration for Scene 2...")
    new_asset_s2 = SelectedAsset(
        asset_id="asset_s2_quantum_corrected",
        asset_path=str(asset2_corrected_path),
        media_type="image",
        duration_sec=3.5,
        provenance={
            "license": "commercial_safe",
            "provider": "procedural",
            "semantic_score": 0.96,
            "relevance_note": "MATCH: Stat callout illustrating 1,000x quantum computing speedup",
        },
        crop_framing=CropFraming(saliency_x=0.5, saliency_y=0.30, caption_safe_zone=CaptionPosition.LOWER),
    )

    timeline_v2 = regen_controller.regenerate_scene_visual(
        timeline=timeline_v1,
        scene_id="scene_02_body",
        new_asset=new_asset_s2,
    )

    print(f"  Timeline Version Bumped: {timeline_v1.timeline_version} -> {timeline_v2.timeline_version}")
    print(f"  Parent Version Recorded: {timeline_v2.parent_timeline_version}")
    print(f"  Invalidation Trace: {timeline_v2.audio_mix_manifest.get('last_invalidation_reason')}")

    # Verify Unaffected Scene 1 is Preserved Bit-for-Bit
    assert timeline_v2.scenes[0].selected_assets[0].asset_id == "asset_s1_hook", "Scene 1 visual must be preserved!"
    assert timeline_v2.scenes[0].narration.text == "Stop scrolling and check this.", "Scene 1 narration must be preserved!"
    assert timeline_v2.scenes[1].selected_assets[0].asset_id == "asset_s2_quantum_corrected", "Scene 2 visual must be updated!"

    # -------------------------------------------------------------
    # 8. Recompile & Re-render Sealed RenderPlan v1.1
    # -------------------------------------------------------------
    print("\n[Step 10] Recompiling Sealed RenderPlan (v1.1) & Physical Re-render...")
    plan_v2 = regen_controller.recompile_render_plan(timeline_v2, check_physical_files=True)
    out_video_v2 = artifacts_dir / "render_v2_regenerated.mp4"
    render_res_v2 = renderer.render(plan_v2, str(out_video_v2))
    print(f"  Physical Render v2: {render_res_v2.output_path} ({render_res_v2.duration_sec:.2f}s, {render_res_v2.file_size_bytes} bytes)")
    assert out_video_v2.exists() and out_video_v2.stat().st_size > 0

    # -------------------------------------------------------------
    # 9. Re-evaluate QA & Publish Readiness Gate on Regenerated Video
    # -------------------------------------------------------------
    print("\n[Step 11] Running Re-QA on Regenerated Video v1.1...")
    qa_report_v2 = qa_engine.evaluate_production(timeline_v2, video_path=out_video_v2)
    print(f"  Creative QA Re-score: {qa_report_v2.overall_score:.1f}/100.0 (Status: {qa_report_v2.overall_status.value})")
    for name, item in qa_report_v2.get_metrics_dict().items():
        print(f"    - {item.name:30s}: {item.score:5.1f} | Status: {item.status.value:5s}")

    defects_v2 = defect_classifier.classify_defects(qa_report_v2)
    print(f"  Remaining Defects Count: {len(defects_v2)}")

    gate_decision_v2 = publish_gate.evaluate(creative_report=qa_report_v2, defects=defects_v2)
    print(f"\n[Step 12] Final Publish Readiness Decision: {gate_decision_v2.status.value}")
    print(f"  Summary: {gate_decision_v2.summary}")
    assert gate_decision_v2.status == PublishReadinessStatus.READY, f"Gate must approve regenerated production: {gate_decision_v2.blocking_reasons}"

    print("\n" + "=" * 70)
    print("PHASE 5 REAL END-TO-END SMOKE TEST COMPLETED WITH 100% SUCCESS!")
    print("=" * 70)


if __name__ == "__main__":
    main()
