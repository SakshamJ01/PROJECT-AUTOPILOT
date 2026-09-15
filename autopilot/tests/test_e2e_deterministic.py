"""End-to-End Deterministic Integration Test — Full local-first production loop.
Executes: Topic -> Research -> Script -> Voice -> Local Assets -> Normalization -> Render -> Final MP4.
Completely offline, deterministic, zero external API spend.
"""
import json
import pytest
from pathlib import Path

from autopilot.core.config import CONFIG
from autopilot.core.contracts import (
    ContentItem, ScriptDocument, ScriptScene, ContentPackage,
    PublicationMetadata, ProvenanceRecord, RenderPlan, QAStatus
)
from autopilot.providers.mock_script import MockScriptProvider
from autopilot.providers.mock_tts import MockTTSProvider
from autopilot.providers.local_asset_provider import LocalAssetProvider
from autopilot.core.asset_pipeline import process_scene_assets
from autopilot.core.renderer import FFmpegRenderer
from autopilot.core.media_inspection import inspect_media
from autopilot.core.audio_duration import extract_duration
from autopilot.core.asset_cache import compute_file_sha256
from autopilot.core.qa_engine import QAEngine, export_qa_artifacts
from autopilot.db.manager import DBManager


def test_full_deterministic_production_pipeline(tmp_path):
    job_id = "e2e-det-001"
    topic = "The Invention of the Steam Engine"
    db_path = tmp_path / "e2e.db"
    db = DBManager(db_path)
    db.init_schema()
    db.create_job(job_id=job_id, topic=topic)

    # 1. Script Generation (1 scene for MVP single-scene renderer)
    script_provider = MockScriptProvider()
    script = script_provider.generate_script(topic=topic, content_id=job_id, max_scenes=1)
    assert len(script.scenes) == 1

    # 2. Voice / TTS Synthesis
    tts_provider = MockTTSProvider()
    voice_dir = tmp_path / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    voice_artifacts = []
    total_audio_sec = 0.0

    for scene in script.scenes:
        if scene.narration:
            audio_out = voice_dir / f"scene_{scene.scene_id}.wav"
            tts_provider.synthesize(scene.narration, str(audio_out))
            assert audio_out.exists()
            dur_info = extract_duration(str(audio_out))
            assert dur_info["valid"] is True
            voice_artifacts.append(str(audio_out.resolve()))
            total_audio_sec += dur_info.get("duration_sec", 2.0)

    # 3. Asset Acquisition & Normalization
    asset_artifacts, quality_report = process_scene_assets(
        script=script,
        job_id=job_id,
        provider_name="local",
        db=db,
    )
    assert len(asset_artifacts) == len(script.scenes)
    assert quality_report["status"] in ("completed", "pending")

    # 4. Construct Content Package
    package = ContentPackage(
        content_item=ContentItem(content_id=job_id, topic=topic),
        script=script,
        voice_artifacts=voice_artifacts,
        measured_duration_sec=total_audio_sec,
        publication=PublicationMetadata(title=script.working_title or topic),
        provenance=ProvenanceRecord(provider="local_e2e_pipeline"),
    )

    # 5. Build RenderPlan with Normalized Dynamic Asset Paths
    render_scenes = []
    for idx, scene in enumerate(script.scenes):
        matched_art = next((a for a in asset_artifacts if a.scene_id == scene.scene_id), None)
        norm_path = matched_art.normalized_path if matched_art else None
        voice_path = voice_artifacts[idx] if idx < len(voice_artifacts) else None
        render_scenes.append({
            "scene_id": scene.scene_id,
            "duration_sec": scene.estimated_duration_seconds,
            "asset_path": norm_path,
            "audio_path": voice_path,
        })

    render_plan = RenderPlan(
        plan_id=f"plan-{job_id}",
        content_id=job_id,
        job_id=job_id,
        profile="vertical_short",
        scenes=render_scenes,
    )

    # 6. Render Video via FFmpegRenderer
    renderer = FFmpegRenderer(profile="vertical_short")
    out_mp4 = tmp_path / "final_output.mp4"
    render_out = renderer.render(render_plan, str(out_mp4))

    assert Path(render_out.output_path).exists()
    assert Path(render_out.output_path).stat().st_size > 0

    # 7. Quality Probe Verification
    probe = inspect_media(render_out.output_path)
    assert probe["valid"] is True
    assert probe["width"] == 1080
    assert probe["height"] == 1920
    assert probe["duration_sec"] > 0
    assert render_out.checksum_sha256 is not None

    # 8. Milestone 5 QA Engine & Quality Gates Verification
    qa_engine = QAEngine(CONFIG)
    qa_report = qa_engine.evaluate(
        media_path=render_out.output_path,
        plan=render_plan,
        package=package,
        asset_artifacts=asset_artifacts,
        profile="vertical_short",
        job_id=job_id,
        db_manager=db,
    )
    assert qa_report.status in (QAStatus.PASS, QAStatus.WARN)
    assert qa_report.publish_allowed is True
    assert qa_report.receipt is not None
    assert qa_report.receipt.status in (QAStatus.PASS, QAStatus.WARN)
    assert len(qa_report.receipt.blocking_findings) == 0

    # Verify QA persistence and artifact export
    db.record_qa_report(qa_report)
    exported = export_qa_artifacts(qa_report, tmp_path)
    assert Path(exported["receipt"]).exists()
    assert Path(exported["quality_report"]).exists()
    assert Path(exported["metrics"]).exists()
    assert Path(exported["findings"]).exists()

    db_report = db.get_qa_report(qa_report.report_id)
    assert db_report is not None
    assert db_report["publish_allowed"] == 1

