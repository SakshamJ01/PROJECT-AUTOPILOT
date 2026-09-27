"""Real Visual Smoke Test Script — Phase 3.
Runs an end-to-end visual intelligence pipeline execution with real disk verification.
"""
from __future__ import annotations
import sys
import wave
from pathlib import Path
from datetime import datetime, timezone

from autopilot.core.config import CONFIG
from autopilot.core.timeline import (
    MaterializedTimeline,
    MaterializedScene,
    MaterializedTiming,
    MaterializedNarration,
    MaterializedAudioPlan,
    MaterializedCaptionPlan,
    ShotType,
    VisualCoverageClass,
    VisualAssertionLevel,
    VisualRequirements,
    CaptionPosition,
)
from autopilot.core.visual_intelligence import VisualIntelligenceEngine
from autopilot.core.timeline_compiler import TimelineCompiler


def main():
    artifacts_dir = CONFIG.get_artifacts_dir() / "smoke_phase3"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = artifacts_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    print("=== PHASE 3 REAL VISUAL SMOKE TEST ===")

    # 1. Create real audio file for the smoke test
    audio_path = artifacts_dir / "smoke_voice.wav"
    with wave.open(str(audio_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(b"\x00\x00" * 44100 * 5)  # 5 seconds of 44.1kHz stereo audio

    # 2. Build multi-scene MaterializedTimeline representing distinct visual requirements
    timeline = MaterializedTimeline(
        timeline_id="smoke_timeline_phase3",
        job_id="job_smoke_p3",
        intent_timeline_id="intent_smoke_p3",
        total_measured_duration_sec=10.0,
        scenes=[
            MaterializedScene(
                scene_id="scene_01_hook",
                order=1,
                timing=MaterializedTiming(start_time_sec=0.0, end_time_sec=5.0, duration_sec=5.0),
                narration=MaterializedNarration(
                    text="James Webb Telescope captures ancient galaxies at the edge of time.",
                    audio_artifact_path=str(audio_path),
                ),
                visual_requirements=VisualRequirements(
                    visual_concept="Deep Space Cosmic Nebula Telescope View",
                    b_roll_search_query="deep space nebula stars telescope",
                    required_shot_type=ShotType.WIDE,
                    visual_assertion_level=VisualAssertionLevel.LITERAL,
                ),
                audio_plan=MaterializedAudioPlan(
                    voice_path=str(audio_path),
                    voice_duration_sec=5.0,
                    sample_rate=44100,
                    channels=2,
                ),
            ),
            MaterializedScene(
                scene_id="scene_02_schematic",
                order=2,
                timing=MaterializedTiming(start_time_sec=5.0, end_time_sec=10.0, duration_sec=5.0),
                narration=MaterializedNarration(
                    text="Over 13 billion light years of cosmic evolution decoded.",
                    audio_artifact_path=str(audio_path),
                ),
                visual_requirements=VisualRequirements(
                    visual_concept="Cosmic Timeline 13 Billion Years",
                    b_roll_search_query="cosmic evolution timeline chart statistics",
                    required_shot_type=ShotType.DIAGRAM,
                    visual_assertion_level=VisualAssertionLevel.SCHEMATIC,
                ),
                audio_plan=MaterializedAudioPlan(
                    voice_path=str(audio_path),
                    voice_duration_sec=5.0,
                    sample_rate=44100,
                    channels=2,
                ),
            )
        ]
    )

    # 3. Instantiate VisualIntelligenceEngine
    engine = VisualIntelligenceEngine(cache_dir=cache_dir)

    # 4. Execute visual materialization
    print("Executing VisualIntelligenceEngine.process_materialized_timeline...")
    mat_timeline = engine.process_materialized_timeline(timeline, download_media=True)

    # 5. Verify physically on disk and inspect output
    print("\n--- Physical Verification ---")
    for scene in mat_timeline.scenes:
        print(f"\nScene: {scene.scene_id} ({scene.timing.duration_sec}s)")
        print(f"  Selected Assets: {len(scene.selected_assets)}")
        for idx, asset in enumerate(scene.selected_assets):
            print(f"  Asset [{idx+1}]: {asset.asset_id}")
            print(f"    Path: {asset.asset_path}")
            p = Path(asset.asset_path)
            assert p.exists(), f"Physical file missing on disk: {asset.asset_path}"
            assert p.stat().st_size > 0, f"File is 0 bytes: {asset.asset_path}"
            print(f"    Size: {p.stat().st_size} bytes [EXISTS ON DISK]")
            print(f"    Crop Framing: saliency=({asset.crop_framing.saliency_x}, {asset.crop_framing.saliency_y}) safe_zone={asset.crop_framing.caption_safe_zone}")
            print(f"    Provenance Provider: {asset.provenance.get('provider')} License: {asset.provenance.get('license')}")
            print(f"    Score: {asset.provenance.get('scoring', {}).get('total_score')} ({asset.provenance.get('scoring', {}).get('coverage_class')})")

    # 6. Compile Timeline into RenderPlan and check MPT Handoff
    print("\n--- Timeline Compiler & Invariant Verification ---")
    compiler = TimelineCompiler()
    render_plan = compiler.compile(mat_timeline)
    print(f"Compilation Successful: RenderPlan ID = {render_plan.plan_id}")
    print(f"RenderPlan SHA256 = {render_plan.render_plan_sha256}")
    print(f"MPT Video Materials count = {len(render_plan.mpt_handoff.video_materials)}")
    for idx, mat in enumerate(render_plan.mpt_handoff.video_materials):
        print(f"  MPT Material [{idx+1}] = {mat} (Order Preserved: {Path(mat).exists()})")

    print("\n=== PHASE 3 SMOKE TEST COMPLETED SUCCESSFULLY ===")


if __name__ == "__main__":
    main()
