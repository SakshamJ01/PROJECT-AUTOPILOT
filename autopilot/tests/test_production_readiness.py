"""Regression tests for Production Readiness — End-to-End pipeline integrity.
Tests provider selection, research isolation, audio duration mapping, semantic asset filtering,
safe resumption, and CLI render command.
"""
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from autopilot.core.config import Config, CONFIG
from autopilot.core.contracts import (
    ScriptDocument, ScriptScene, ContentPackage, ContentItem,
    ProvenanceRecord, PublicationMetadata, RenderPlan, AssetArtifact,
    AssetLicense, AssetCandidate, AssetDimensions,
)
from autopilot.db.manager import DBManager
from autopilot.core.pipeline import PipelineOrchestrator, PipelineError
from autopilot.core.asset_pipeline import (
    derive_visual_subject_query, derive_deterministic_fallback_query,
    process_scene_assets,
)
from autopilot.core.renderer import FFmpegRenderer
from autopilot.cli.main import run_render


def test_research_provider_isolation_in_db(tmp_path):
    """Verify that get_latest_research_report_for_topic distinguishes providers."""
    db_path = tmp_path / "test_res.db"
    db = DBManager(db_path)
    db.init_schema()

    topic = "Artificial Intelligence"
    # Save a mock report
    db.create_research_request("req-mock-1", topic)
    db.save_research_report(
        "rep-mock-1", "req-mock-1", topic, status="completed",
        summary="Discovered 3 sources via mock_search",
        provenance_json=json.dumps({"provider": "mock_search"}),
    )

    # Looking for wikipedia should not return the mock report
    wiki_rep = db.get_latest_research_report_for_topic(topic, provider="wikipedia")
    assert wiki_rep is None

    # Looking for mock should return the mock report
    mock_rep = db.get_latest_research_report_for_topic(topic, provider="mock_search")
    assert mock_rep is not None
    assert mock_rep["report_id"] == "rep-mock-1"

    # Now save a real wikipedia report
    db.create_research_request("req-wiki-1", topic)
    db.save_research_report(
        "rep-wiki-1", "req-wiki-1", topic, status="completed",
        summary="Discovered 3 sources via wikipedia",
        provenance_json=json.dumps({"provider": "wikipedia"}),
    )
    db.record_research_evidence(
        evidence_id="ev-wiki-1",
        report_id="rep-wiki-1",
        source_id="src-1",
        snippet="Real evidence from Wikipedia about AI.",
        relevance_score=0.9,
        status="relevant",
    )

    # Now looking for wikipedia returns the wikipedia report
    wiki_rep2 = db.get_latest_research_report_for_topic(topic, provider="wikipedia")
    assert wiki_rep2 is not None
    assert wiki_rep2["report_id"] == "rep-wiki-1"


def test_pipeline_orchestrator_honors_wikipedia_provider(tmp_path):
    """Verify PipelineOrchestrator instantiates WikipediaProvider when requested."""
    db_path = tmp_path / "test_pipe.db"
    db = DBManager(db_path)
    db.init_schema()
    cfg = Config(artifacts_dir=tmp_path, db_path=db_path)
    orch = PipelineOrchestrator(config=cfg, db=db)

    with patch("autopilot.providers.wikipedia_provider.WikipediaProvider.search") as mock_search:
        mock_search.return_value = [
            {"source_id": "wiki-ai", "title": "AI", "publisher": "Wikipedia", "url": "https://en.wikipedia.org/wiki/AI", "snippet": "AI snippet"}
        ]
        res = orch.run_pipeline(
            job_id="job-wiki-test",
            topic="Artificial Intelligence",
            research_provider="wikipedia",
            llm_provider="mock",
            tts_provider="none",
            asset_provider="local",
            production_engine="ffmpeg",
        )
        assert mock_search.called
        assert res["status"] == "success"

        # Verify DB recorded wikipedia provider
        rep = db.get_latest_research_report_for_topic("Artificial Intelligence", provider="wikipedia")
        assert rep is not None
        assert "wikipedia" in rep["summary"]


def test_audio_duration_drives_render_scene_duration(tmp_path):
    """Verify that measured audio duration overrides blind estimated duration in RenderPlan."""
    db_path = tmp_path / "test_dur.db"
    db = DBManager(db_path)
    db.init_schema()
    cfg = Config(artifacts_dir=tmp_path, db_path=db_path)
    orch = PipelineOrchestrator(config=cfg, db=db)

    # Pre-create mock audio file with duration 8.4s
    job_id = "job-dur-test"
    voice_dir = tmp_path / "jobs" / job_id / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    fake_wav = voice_dir / "scene_scene-01.wav"
    fake_wav.write_bytes(b"RIFFdummywavheaderdata")

    with patch("autopilot.core.pipeline.extract_duration") as mock_extract:
        mock_extract.return_value = {"valid": True, "duration_sec": 8.4}
        with patch("autopilot.core.pipeline.FFmpegRenderer.render") as mock_render, \
             patch("autopilot.core.pipeline.QAEngine.evaluate") as mock_qa:
            def fake_render_impl(plan, out_path):
                p = Path(out_path)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"dummy_mp4_bytes")
                from autopilot.core.contracts import RenderOutput
                return RenderOutput(
                    output_path=str(p),
                    duration_sec=8.4,
                    width=1080, height=1920, codec_video="h264", codec_audio="aac", container="mp4",
                    file_size_bytes=1000, checksum_sha256="abc123sha", profile="short_vertical"
                )
            mock_render.side_effect = fake_render_impl

            from autopilot.core.contracts import QAReport, QAStatus
            mock_qa.return_value = QAReport(
                report_id="rep-1", job_id=job_id, content_id=job_id, status=QAStatus.PASS, publish_allowed=True,
                profile="short_vertical"
            )
            # Create a mock script with 5.0s estimated duration
            sp_dir = tmp_path / "jobs" / job_id / "script"
            sp_dir.mkdir(parents=True, exist_ok=True)
            script = ScriptDocument(
                content_id=job_id,
                topic="Test Topic",
                scenes=[
                    ScriptScene(scene_id="scene-01", order=1, narration="Spoken words here.", visual_intent="talking head presenter", estimated_duration_seconds=5.0)
                ]
            )
            (sp_dir / "script.json").write_text(script.model_dump_json(), encoding="utf-8")
            pkg = ContentPackage(
                content_item=ContentItem(content_id=job_id, topic="Test Topic", format="9:16_video"),
                script=script,
                publication=PublicationMetadata(title="Test", description="Desc"),
                provenance=ProvenanceRecord(provider="mock_script"),
                voice_artifacts=[str(fake_wav)],
            )
            (sp_dir / "content_package.json").write_text(pkg.model_dump_json(), encoding="utf-8")

            res = orch.run_pipeline(
                job_id=job_id,
                topic="Test Topic",
                llm_provider="mock",
                tts_provider="mock",
                asset_provider="local",
                production_engine="ffmpeg",
            )

            # Check that render_plan has 8.4s instead of 5.0s
            plan_file = tmp_path / "jobs" / job_id / "render" / "render_plan.json"
            assert plan_file.exists()
            plan_data = json.loads(plan_file.read_text(encoding="utf-8"))
            assert plan_data["scenes"][0]["duration_sec"] == 8.4


def test_semantic_relevance_rejection_in_asset_pipeline(tmp_path):
    """Verify that assets with zero keyword match and low score are rejected."""
    cfg = Config(artifacts_dir=tmp_path, db_path=tmp_path / "test.db")
    db = DBManager(cfg.db_path)
    db.init_schema()

    script = ScriptDocument(
        content_id="job-sem-01",
        topic="Neural Networks and Deep Learning",
        scenes=[
            ScriptScene(
                scene_id="scene-01",
                order=1,
                narration="Neural networks mimic biological neurons in the human brain.",
                visual_intent="diagram of artificial neural network neurons",
                asset_query="artificial neural network neurons",
                estimated_duration_seconds=5.0,
            )
        ],
    )

    mock_openverse = MagicMock()
    # Return completely irrelevant candidate (Harry Potter bus)
    irrelevant_candidate = AssetCandidate(
        candidate_id="cand-unrelated",
        title="London double decker tourist bus street view",
        asset_type="image",
        source_url="https://example.com/bus.jpg",
        license=AssetLicense(license_name="CC0", rights_status="VERIFIED", commercial_allowed=True),
        dimensions=AssetDimensions(width=1080, height=1920),
    )
    mock_openverse.search.return_value = [irrelevant_candidate]

    with patch("autopilot.core.asset_pipeline.get_asset_provider", return_value=mock_openverse):
        artifacts, report = process_scene_assets(
            script=script,
            job_id="job-sem-01",
            provider_name="openverse",
            db=db,
            config=cfg,
        )
        # Should be rejected for low semantic relevance
        assert len(artifacts) == 0
        assert any("Semantic relevance score too low" in str(r) for r in report.get("rejections", []))


def test_renderer_avoids_cross_scene_audio_bleed():
    """Verify that FFmpegRenderer does not replay scene 1's audio on scene 2 if scene 2 has no voice."""
    renderer = FFmpegRenderer()
    plan = RenderPlan(
        plan_id="plan-test",
        content_id="item-test",
        job_id="job-bleed-test",
        profile="short_vertical",
        scenes=[
            {"scene_id": "scene-01", "duration_sec": 4.0, "asset_path": "nonexistent1.png", "audio_path": "voice1.wav"},
            {"scene_id": "scene-02", "duration_sec": 4.0, "asset_path": "nonexistent2.png", "audio_path": None},
        ],
    )

    with patch("subprocess.run") as mock_sub:
        mock_sub.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_bytes", return_value=b"dummy_mp4_bytes"), \
             patch("pathlib.Path.stat") as mock_stat:
            mock_stat.return_value = MagicMock(st_size=5000)
            out = renderer.render(plan, "out.mp4")

            # Verify that in segment 1 subprocess calls, scene 2 does NOT use voice1.wav
            calls = mock_sub.call_args_list
            # Find the command for segment 1 (scene-02)
            seg1_cmd = None
            for c in calls:
                cmd_args = c[0][0]
                if any("seg_1.mp4" in str(arg) for arg in cmd_args):
                    seg1_cmd = cmd_args
                    break

            assert seg1_cmd is not None
            # Must use anullsrc for scene 2 since audio_path was None
            assert any("anullsrc" in str(arg) for arg in seg1_cmd)
            # Must NOT contain voice1.wav in scene 2 command
            assert "voice1.wav" not in seg1_cmd


def test_cli_render_command(tmp_path):
    """Verify that run_render CLI entry point renders existing scripted job."""
    db_path = tmp_path / "test_render_cli.db"
    db = DBManager(db_path)
    db.init_schema()
    job_id = "job-cli-render-01"

    # Set up script artifact
    art_dir = tmp_path / "jobs" / job_id
    sp_dir = art_dir / "script"
    sp_dir.mkdir(parents=True, exist_ok=True)
    script = ScriptDocument(
        content_id=job_id,
        topic="3 facts about AI",
        scenes=[ScriptScene(scene_id="scene-01", order=1, narration="Fact 1", visual_intent="AI visual representation", estimated_duration_seconds=5.0)]
    )
    (sp_dir / "script.json").write_text(script.model_dump_json(), encoding="utf-8")
    pkg = ContentPackage(
        content_item=ContentItem(content_id=job_id, topic="3 facts about AI", format="9:16_video"),
        script=script,
        publication=PublicationMetadata(title="3 facts about AI", description="Desc"),
        provenance=ProvenanceRecord(provider="mock_script"),
    )
    (sp_dir / "content_package.json").write_text(pkg.model_dump_json(), encoding="utf-8")

    cfg = Config(artifacts_dir=tmp_path, db_path=db_path)
    target_mp4 = art_dir / "render" / "final.mp4"
    target_mp4.parent.mkdir(parents=True, exist_ok=True)
    target_mp4.write_bytes(b"dummy_mp4_bytes")

    with patch("autopilot.cli.main.CONFIG", cfg), \
         patch("autopilot.core.artifacts.CONFIG", cfg), \
         patch("autopilot.core.renderer.FFmpegRenderer.render") as mock_render:
        from autopilot.core.contracts import RenderOutput
        mock_render.return_value = RenderOutput(
            output_path=str(target_mp4),
            duration_sec=5.0, width=1080, height=1920, codec_video="h264", codec_audio="aac", container="mp4",
            file_size_bytes=1000, checksum_sha256="cli_checksum", profile="short_vertical"
        )
        exit_code = run_render(job_id=job_id, asset_provider="local", production_engine="ffmpeg")
        assert exit_code == 0
        # Verify media artifact recorded in DB
        arts = db.get_artifacts_for_job(job_id)
        assert any(a["artifact_type"] == "media" for a in arts)


def test_synthetic_fixture_rejection_for_real_research(tmp_path):
    """Verify that research reports containing synthetic fixtures are never resumed when wikipedia is requested."""
    db_path = tmp_path / "test_syn.db"
    db = DBManager(db_path)
    db.init_schema()

    topic = "3 facts about AI"
    req_id = "req-syn-01"
    rep_id = "rep-syn-01"
    db.create_research_request(req_id, topic)
    db.save_research_report(
        rep_id, req_id, topic, status="completed",
        summary="Synthetic research for '3 facts about AI': 5 sources discovered; all clearly marked as mock fixtures.",
        provenance_json=json.dumps({"provider": "wikipedia"}),
    )
    db.record_research_evidence(
        evidence_id="ev-syn-01",
        report_id=rep_id,
        source_id="wiki-AI",
        snippet="Evidence snippet for wiki-AI: Synthetic fixture.",
        relevance_score=0.75,
        status="relevant",
    )

    # When querying with provider="wikipedia", it MUST return None because of synthetic fixture contamination
    resumed = db.get_latest_research_report_for_topic(topic, provider="wikipedia")
    assert resumed is None
