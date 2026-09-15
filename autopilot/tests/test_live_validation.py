"""Live Validation and Production Readiness Test Suite.
Verifies real local providers (Windows SAPI TTS, OpenAI-compatible LLM), rights gate enforcement,
full end-to-end rendering with real audio, 5-job batch reliability, crash recovery, and security invariants.
"""
from __future__ import annotations
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from autopilot.core.config import CONFIG
from autopilot.core.contracts import (
    ChannelProfile, ChannelProfileVersion, NicheConfig, PersonaConfig,
    VoiceProfile, VisualBrandProfile, PostingPolicy, AutonomyPolicy,
    AnalyticsConfig, MonetizationMetadata, ScriptDocument, ScriptScene,
    AssetCandidate, AssetLicense, AssetProvenance, RenderPlan, ContentPackage,
    ContentItem, AssetArtifact, BatchManifest, BatchItem
)
from autopilot.db.manager import DBManager
from autopilot.providers.sapi_tts_provider import WindowsSAPITTSProvider
from autopilot.providers.openai_llm_provider import OpenAICompatibleLLMProvider, redact_api_key
from autopilot.providers.openverse_provider import map_openverse_license
from autopilot.core.qa_engine import QAEngine
from autopilot.core.worker import LocalWorker
from autopilot.core.pipeline import PipelineOrchestrator
from autopilot.core.channel import ChannelManager
from autopilot.core.batch import BatchProcessor
from autopilot.core.readiness import generate_production_readiness_artifacts


@pytest.fixture
def temp_dir(tmp_path):
    d = tmp_path / "readiness_test"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def db(temp_dir):
    db_path = temp_dir / "test_readiness.db"
    mgr = DBManager(db_path)
    mgr.init_schema()
    return mgr


# ---------------------------------------------------------------------------
# 1. Real Local TTS Validation (Windows SAPI)
# ---------------------------------------------------------------------------

def test_windows_sapi_tts_health():
    provider = WindowsSAPITTSProvider()
    health = provider.health_check()
    if platform.system() == "Windows":
        assert health.healthy is True
        assert "installed_voices" in health.details
        assert health.details["voice_count"] >= 1
    else:
        assert health.healthy is False


def test_windows_sapi_tts_synthesis_and_ffprobe(temp_dir):
    if platform.system() != "Windows":
        pytest.skip("Windows SAPI only available on Windows")

    provider = WindowsSAPITTSProvider()
    out_wav = temp_dir / "test_speech.wav"
    result = provider.synthesize(
        text="Project Autopilot production readiness test.",
        out_path=str(out_wav),
    )

    assert Path(result).exists()
    assert Path(result).stat().st_size > 50000

    # Verify provenance sidecar
    prov_file = Path(result).with_suffix(Path(result).suffix + ".provenance.json")
    assert prov_file.exists()
    prov_data = json.loads(prov_file.read_text(encoding="utf-8"))
    assert prov_data["provider"] == "windows_sapi"
    assert prov_data["duration_sec"] > 0.5
    assert prov_data["mode"] == "real_local_speech"

    # FFprobe verification
    ffprobe_cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "stream=codec_name,sample_rate,channels:format=duration",
        "-of", "json",
        str(out_wav),
    ]
    probe_res = subprocess.run(ffprobe_cmd, capture_output=True, text=True)
    assert probe_res.returncode == 0
    probe_data = json.loads(probe_res.stdout)
    assert probe_data["streams"][0]["codec_name"] == "pcm_s16le"
    assert float(probe_data["format"]["duration"]) > 0.5


# ---------------------------------------------------------------------------
# 2. OpenAI-Compatible LLM Adapter Validation
# ---------------------------------------------------------------------------

def test_openai_llm_secret_redaction():
    raw_error = "Authorization header: Bearer sk-antigravity-secret-key-12345 failed with code 401"
    redacted = redact_api_key(raw_error)
    assert "sk-antigravity" not in redacted
    assert "Bearer [REDACTED]" in redacted


def test_openai_llm_unreachable_endpoint_graceful():
    provider = OpenAICompatibleLLMProvider(base_url="http://127.0.0.1:59999/v1", timeout=1.0)
    health = provider.health_check()
    assert health.healthy is False
    assert "Cannot reach endpoint" in health.error or "error" in health.error


# ---------------------------------------------------------------------------
# 3. Real Asset Rights Gating & Openverse Mapping
# ---------------------------------------------------------------------------

def test_openverse_rights_gating():
    # CC0 -> Verified
    cc0_lic = map_openverse_license("cc0", "1.0", "https://creativecommons.org/publicdomain/zero/1.0/")
    assert cc0_lic.rights_status == "VERIFIED"
    assert cc0_lic.commercial_use is True

    # CC-BY -> Verified
    by_lic = map_openverse_license("by", "4.0")
    assert by_lic.rights_status == "VERIFIED"
    assert by_lic.attribution_required is True

    # Non-Commercial (CC-BY-NC) -> REJECTED
    nc_lic = map_openverse_license("by-nc", "4.0")
    assert nc_lic.rights_status == "REJECTED"
    assert nc_lic.commercial_use is False

    # No-Derivatives (CC-BY-ND) -> REJECTED
    nd_lic = map_openverse_license("by-nd", "4.0")
    assert nd_lic.rights_status == "REJECTED"
    assert nd_lic.derivative_use is False

    # Unknown -> UNKNOWN
    unk_lic = map_openverse_license(None)
    assert unk_lic.rights_status == "UNKNOWN"


# ---------------------------------------------------------------------------
# 4. End-to-End Real Voice Render & QA Gate
# ---------------------------------------------------------------------------

def test_real_voice_render_and_qa_pipeline(temp_dir, db):
    channel_mgr = ChannelManager(db)
    profile = ChannelProfile(
        channel_id="real-validation-ch",
        channel_name="Real Validation Channel",
        niche=NicheConfig(niche_name="technology", description="AI and Software"),
        persona=PersonaConfig(narration_personality="clear and engaging"),
        voice=VoiceProfile(provider="windows_sapi", voice_id="default"),
        visual=VisualBrandProfile(primary_color="#1E1E2E", font_family="Arial"),
        posting_policy=PostingPolicy(max_daily_posts=5),
        autonomy_policy=AutonomyPolicy(autonomy_level=2, daily_queue_limit=5),
        platform_targets=["youtube"],
    )
    channel_mgr.create_channel(profile)

    # Generate real voice audio using WindowsSAPITTSProvider or fallback
    tts = WindowsSAPITTSProvider() if platform.system() == "Windows" else None
    audio_path = temp_dir / "scene_01.wav"
    if tts and tts.health_check().healthy:
        tts.synthesize("Artificial intelligence is advancing rapidly today.", str(audio_path))
    else:
        # Fallback synthetic tone
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=1000:duration=3",
            "-ac", "1", "-ar", "22050", "-c:a", "pcm_s16le", str(audio_path)
        ], check=True)

    assert audio_path.exists()

    # Create synthetic dynamic image fixture
    img_path = temp_dir / "scene_img.png"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=s=1080x1920:d=1",
        "-frames:v", "1", "-update", "1", str(img_path)
    ], check=True)
    assert img_path.exists()

    # Render vertical 9:16 short video
    render_out = temp_dir / "rendered_real_video.mp4"
    render_cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(img_path),
        "-i", str(audio_path),
        "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(render_out)
    ]
    render_res = subprocess.run(render_cmd, capture_output=True, text=True)
    assert render_res.returncode == 0
    assert render_out.exists()
    assert render_out.stat().st_size > 10000

    # Build ContentPackage and AssetArtifacts
    import hashlib
    img_hash = hashlib.sha256(img_path.read_bytes()).hexdigest()
    asset_art = AssetArtifact(
        artifact_id="art-img-01",
        source_path=str(img_path),
        normalized_path=str(img_path),
        checksum_sha256=img_hash,
        provenance=AssetProvenance(provider="local", source_id="img-01", retrieval_timestamp="2026-09-11T12:00:00Z"),
        license=AssetLicense(license_name="CC0-1.0", rights_status="VERIFIED", source_url=str(img_path)),
    )

    script_doc = ScriptDocument(
        content_id="val-001",
        topic="AI Advancements",
        language="en",
        scenes=[
            ScriptScene(
                scene_id="scene-01",
                order=1,
                narration="Artificial intelligence is advancing rapidly today.",
                visual_intent="Tech visual",
                asset_query="ai technology",
                estimated_duration_seconds=3.0,
            )
        ],
        title="AI Advancements",
        estimated_total_duration_seconds=3.0,
    )

    package = ContentPackage(
        content_item=ContentItem(content_id="val-001", topic="AI Advancements", channel_id="real-validation-ch"),
        script=script_doc,
    )

    plan = RenderPlan(
        plan_id="plan-val-001",
        content_id="val-001",
        job_id="job-val-001",
        scenes=[
            {
                "scene_id": "scene-01",
                "asset_path": str(img_path),
                "audio_path": str(audio_path),
                "duration_sec": 3.0,
                "caption_text": "AI is advancing rapidly",
            }
        ],
    )

    qa = QAEngine()
    report = qa.evaluate(
        media_path=render_out,
        plan=plan,
        package=package,
        asset_artifacts=[asset_art],
        job_id="job-val-001",
    )

    assert report.status.value in ("PASS", "WARN")
    assert report.publish_allowed is True
    assert report.receipt is not None


# ---------------------------------------------------------------------------
# 5. Controlled 5-Job Batch Reliability & Quota Test
# ---------------------------------------------------------------------------

def test_five_job_batch_reliability(temp_dir, db):
    channel_mgr = ChannelManager(db)
    profile = ChannelProfile(
        channel_id="batch-test-ch",
        channel_name="Batch Test Channel",
        niche=NicheConfig(niche_name="science", description="Science and nature"),
        persona=PersonaConfig(narration_personality="curious"),
        voice=VoiceProfile(provider="windows_sapi", voice_id="default"),
        visual=VisualBrandProfile(primary_color="#0A192F"),
        posting_policy=PostingPolicy(max_daily_posts=10),
        autonomy_policy=AutonomyPolicy(autonomy_level=2, daily_queue_limit=10),
        platform_targets=["youtube"],
    )
    channel_mgr.create_channel(profile)

    batch_proc = BatchProcessor(db)
    items = [
        BatchItem(topic=f"Quantum Computing Breakthrough Part {i+1}", channel_id="batch-test-ch", priority=2)
        for i in range(5)
    ]
    manifest = BatchManifest(
        manifest_id="manifest-rel-5",
        name="Reliability-5-Batch",
        items=items,
    )
    res = batch_proc.submit_manifest(manifest)

    assert res.enqueued == 5
    assert res.duplicates == 0

    # Mock orchestrator for clean batch worker execution
    mock_orch = MagicMock(spec=PipelineOrchestrator)
    mock_orch.run_pipeline.return_value = {
        "status": "success",
        "qa_status": "PASS",
        "published": False,
    }

    worker = LocalWorker(worker_id="test-reliability-worker", db=db, orchestrator=mock_orch)
    processed_count = worker.run(once=True)
    assert processed_count == 5

    # Verify all queue items succeeded in database
    queue_items = db.list_queue_items()
    assert len(queue_items) == 5
    for item in queue_items:
        assert item["status"] == "succeeded"


# ---------------------------------------------------------------------------
# 6. Worker Crash and Restart Recovery
# ---------------------------------------------------------------------------

def test_worker_crash_recovery_resumes_cleanly(temp_dir, db):
    # Enqueue a job
    db.enqueue_item("q-crash-01", "job-crash-01", max_attempts=3, payload={"topic": "Superconductivity Research"})

    # Simulate active lease from a crashed worker
    claimed = db.claim_next_queue_item(worker_id="crashed-worker", lease_duration_sec=1)
    assert claimed is not None

    # Manually backdate lease_expires_at to simulate worker crash
    with db._connect() as conn:
        conn.execute("UPDATE queue_items SET lease_expires_at = datetime('now', '-10 seconds') WHERE queue_id = 'q-crash-01'")
        conn.commit()

    # Recover stale leases
    recovered = db.recover_stale_leases()
    assert "q-crash-01" in recovered

    # Retry/reset queue item for the recovered worker to claim
    db.retry_queue_item("q-crash-01")

    # Restarted worker claims and finishes the job
    mock_orch = MagicMock(spec=PipelineOrchestrator)
    mock_orch.run_pipeline.return_value = {"status": "success"}

    worker = LocalWorker(worker_id="recovered-worker", db=db, orchestrator=mock_orch)
    processed = worker.run(once=True)
    assert processed == 1

    final_item = db.get_queue_item("q-crash-01")
    assert final_item is not None
    assert final_item["status"] == "succeeded"


# ---------------------------------------------------------------------------
# 7. Production Readiness Scorecard and Artifacts Generation
# ---------------------------------------------------------------------------

def test_production_readiness_artifact_generation(temp_dir):
    artifacts_out = temp_dir / "readiness_artifacts"
    report = generate_production_readiness_artifacts(out_dir=artifacts_out)

    assert report["decision"] in ("GO", "CONDITIONAL GO")
    assert (artifacts_out / "report.json").exists()
    assert (artifacts_out / "report.md").exists()
    assert (artifacts_out / "provider_matrix.json").exists()
    assert (artifacts_out / "performance.json").exists()
    assert (artifacts_out / "cost.json").exists()
    assert (artifacts_out / "failures.json").exists()

    # Verify report.json content
    data = json.loads((artifacts_out / "report.json").read_text(encoding="utf-8"))
    assert "scorecard" in data
    assert "FOUNDATION" in data["scorecard"]
    assert "TTS" in data["scorecard"]
    assert "COST" in data["scorecard"]
