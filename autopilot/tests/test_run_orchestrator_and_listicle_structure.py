"""Tests for Task A (Run Path via PipelineOrchestrator) and Task B (Editorial Listicle Structure & Regeneration).

Covers all 16 required test scenarios:
RUN PATH:
1. `run` invokes PipelineOrchestrator
2. all expected stages are reachable
3. provider/channel/policy values propagate
4. `run` does not accidentally use the legacy direct path

EDITORIAL STRUCTURE:
5. "3 surprising facts..." detects a 3-item list
6. valid hook + 3 facts passes
7. 1 fact for a 3-item request fails
8. 2 facts for a 3-item request fails
9. one scene containing all facts does not automatically pass
10. unrelated non-listicle topics remain flexible
11. CTA does not count as a fact
12. structure defect produces a useful reason

REGENERATION:
13. LIST_STRUCTURE_DEFECT maps to targeted regeneration
14. regeneration preserves research
15. regeneration is bounded by max attempts
16. persistent failure becomes NEEDS_REVIEW
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from autopilot.cli.main import main, build_parser
from autopilot.core.contracts import (
    ScriptDocument,
    ScriptScene,
    RegenerationDefectType,
)
from autopilot.core.state_machine import WorkflowState
from autopilot.core.quality import (
    detect_listicle_cardinality,
    count_substantive_fact_units,
    is_pure_cta_scene,
    evaluate_script,
)
from autopilot.core.pipeline import PipelineOrchestrator, classify_qa_defect, PipelineError
from autopilot.core.config import Config
from autopilot.db.manager import DBManager


# ====================================================================
# RUN PATH TESTS (1-4)
# ====================================================================

def test_1_run_invokes_pipeline_orchestrator():
    """1. Verify CLI `run` command invokes PipelineOrchestrator.run_pipeline."""
    with patch("autopilot.core.pipeline.PipelineOrchestrator.run_pipeline") as mock_run:
        mock_run.return_value = {
            "job_id": "test-job-01",
            "status": "success",
            "media_path": "/fake/final.mp4",
            "qa_status": "APPROVED",
        }
        test_args = [
            "autopilot",
            "run",
            "--topic", "Quantum computing fundamentals",
            "--channel", "tech_shorts",
            "--policy", "mock",
        ]
        with patch("sys.argv", test_args):
            ret = main()
            assert ret == 0
            assert mock_run.called
            call_kwargs = mock_run.call_args.kwargs
            assert call_kwargs["topic"] == "Quantum computing fundamentals"
            assert call_kwargs["channel_id"] == "tech_shorts"
            assert call_kwargs["policy"] == "mock"


def test_2_all_expected_stages_are_reachable(tmp_path):
    """2. Verify PipelineOrchestrator runs through all expected stages: RESEARCH, SCRIPT, VOICE, ASSETS, RENDER, QA."""
    stages_reached = []

    def on_progress(stage):
        stages_reached.append(stage)

    db_path = tmp_path / "test_stages.db"
    db = DBManager(db_path)
    db.init_schema()
    cfg = Config(artifacts_dir=tmp_path, db_path=db_path)
    orch = PipelineOrchestrator(config=cfg, db=db)

    res = orch.run_pipeline(
        job_id="job-stages-test",
        topic="3 surprising facts about artificial intelligence",
        channel_id="tech_shorts",
        policy="mock",
        llm_provider="mock",
        research_provider="mock_search",
        tts_provider="none",
        production_engine="ffmpeg",
        asset_provider="local",
        on_stage_progress=on_progress,
    )

    assert "RESEARCH" in stages_reached
    assert "SCRIPT" in stages_reached
    assert "VOICE" in stages_reached
    assert "ASSETS" in stages_reached
    assert "RENDER" in stages_reached
    assert "QA" in stages_reached
    assert res["status"] in ("APPROVED", "QA", "RENDERED", "success")


def test_3_provider_channel_policy_values_propagate():
    """3. Verify provider, channel, and policy parameters propagate into run_pipeline."""
    with patch("autopilot.core.pipeline.PipelineOrchestrator.run_pipeline") as mock_run:
        mock_run.return_value = {"status": "success", "media_path": "/tmp/out.mp4", "qa_status": "APPROVED"}
        test_args = [
            "autopilot",
            "run",
            "--topic", "Deep space missions",
            "--channel", "science_shorts",
            "--policy", "mock",
            "--asset-provider", "local",
            "--production-engine", "ffmpeg",
        ]
        with patch("sys.argv", test_args):
            main()
            kwargs = mock_run.call_args.kwargs
            assert kwargs["channel_id"] == "science_shorts"
            assert kwargs["policy"] == "mock"
            assert kwargs["asset_provider"] == "local"
            assert kwargs["production_engine"] == "ffmpeg"


def test_4_run_does_not_use_legacy_direct_path():
    """4. Verify `run` does NOT call run_produce directly."""
    with patch("autopilot.cli.main.run_produce") as mock_produce, \
         patch("autopilot.core.pipeline.PipelineOrchestrator.run_pipeline") as mock_run:
        mock_run.return_value = {"status": "success", "media_path": "/tmp/out.mp4", "qa_status": "APPROVED"}
        test_args = ["autopilot", "run", "--topic", "Robotics", "--policy", "mock"]
        with patch("sys.argv", test_args):
            main()
            assert not mock_produce.called
            assert mock_run.called


# ====================================================================
# EDITORIAL STRUCTURE TESTS (5-12)
# ====================================================================

def test_5_detects_3_item_list():
    """5. Verify detect_listicle_cardinality detects 3 items from topic string."""
    topics = [
        "3 surprising facts about artificial intelligence",
        "3 surprising facts about AI",
        "Top 3 secrets of machine learning",
        "three ways AI is transforming science",
        "5 incredible discoveries in astronomy",
        "10 mind-blowing tech breakthroughs",
    ]
    assert detect_listicle_cardinality(topics[0]) == 3
    assert detect_listicle_cardinality(topics[1]) == 3
    assert detect_listicle_cardinality(topics[2]) == 3
    assert detect_listicle_cardinality(topics[3]) == 3
    assert detect_listicle_cardinality(topics[4]) == 5
    assert detect_listicle_cardinality(topics[5]) == 10


def test_6_valid_hook_plus_3_facts_passes():
    """6. Verify a script with hook + 3 substantive fact scenes passes evaluation for a 3-item request."""
    scenes = [
        ScriptScene(
            scene_id="scene-01",
            order=1,
            scene_type="talking_head",
            narration="Did you know these three surprising facts about artificial intelligence?",
            visual_intent="Host looking amazed in modern AI lab",
            asset_query="modern AI lab",
            estimated_duration_seconds=4.0,
        ),
        ScriptScene(
            scene_id="scene-02",
            order=2,
            scene_type="broll",
            narration="First, the earliest neural network was built out of vacuum tubes back in 1951.",
            visual_intent="Vintage vacuum tube computer setup",
            asset_query="vintage supercomputer",
            estimated_duration_seconds=5.0,
        ),
        ScriptScene(
            scene_id="scene-03",
            order=3,
            scene_type="broll",
            narration="Second, modern AI models can analyze protein structures in seconds instead of years.",
            visual_intent="3D molecular protein structure rotating",
            asset_query="protein folding biology",
            estimated_duration_seconds=5.0,
        ),
        ScriptScene(
            scene_id="scene-04",
            order=4,
            scene_type="broll",
            narration="Third, artificial intelligence discovered new potential antibiotics never seen before in nature.",
            visual_intent="Petri dish in pharmaceutical lab",
            asset_query="medical laboratory petri dish",
            estimated_duration_seconds=5.0,
        ),
        ScriptScene(
            scene_id="scene-05",
            order=5,
            scene_type="cta",
            narration="Subscribe for more daily AI breakthroughs and tech insights.",
            visual_intent="Subscribe animation button",
            asset_query="neon subscribe icon",
            estimated_duration_seconds=3.0,
        ),
    ]
    doc = ScriptDocument(
        content_id="doc-valid-3",
        topic="3 surprising facts about artificial intelligence",
        hook="Did you know these three surprising facts about artificial intelligence?",
        scenes=scenes,
        cta="Subscribe for more daily AI breakthroughs and tech insights.",
    )
    report = evaluate_script(doc, topic="3 surprising facts about artificial intelligence")
    assert report.overall in ("pass", "pass_with_warnings")
    assert report.blocking_count == 0
    assert report.requested_items == 3
    assert report.detected_items == 4  # 1 hook scene + 3 fact scenes = 4 substantive scenes >= 3
    assert report.structure_status == "valid"


def test_7_one_fact_for_3_item_request_fails():
    """7. Verify 1 fact for a 3-item request fails with list_structure blocking defect."""
    scenes = [
        ScriptScene(
            scene_id="scene-01",
            order=1,
            scene_type="talking_head",
            narration="Artificial intelligence can now fold complex proteins faster than human researchers.",
            visual_intent="AI lab",
            asset_query="AI lab",
            estimated_duration_seconds=5.0,
        ),
    ]
    doc = ScriptDocument(
        content_id="doc-fail-1",
        topic="3 surprising facts about artificial intelligence",
        hook="Here are 3 facts about AI.",
        scenes=scenes,
        cta="Follow for more.",
    )
    report = evaluate_script(doc, topic="3 surprising facts about artificial intelligence")
    assert report.overall == "fail"
    assert report.blocking_count >= 1
    list_check = next((c for c in report.checks if c.check_name == "list_structure"), None)
    assert list_check is not None
    assert list_check.status == "fail"
    assert list_check.severity == "blocking"
    assert report.structure_status == "defect"
    assert report.requested_items == 3
    assert report.detected_items == 1


def test_8_two_facts_for_3_item_request_fails():
    """8. Verify 2 facts for a 3-item request fails with list_structure blocking defect."""
    scenes = [
        ScriptScene(
            scene_id="scene-01",
            order=1,
            scene_type="broll",
            narration="First, AI originated in the 1950s with the Dartmouth workshop.",
            visual_intent="Historical Dartmouth college campus",
            asset_query="historic university campus",
            estimated_duration_seconds=5.0,
        ),
        ScriptScene(
            scene_id="scene-02",
            order=2,
            scene_type="broll",
            narration="Second, neural networks are loosely inspired by human brain architecture.",
            visual_intent="Brain synapses firing glowing blue",
            asset_query="human brain synapses",
            estimated_duration_seconds=5.0,
        ),
    ]
    doc = ScriptDocument(
        content_id="doc-fail-2",
        topic="3 surprising facts about artificial intelligence",
        hook="3 surprising facts about AI.",
        scenes=scenes,
        cta="Subscribe for more.",
    )
    report = evaluate_script(doc, topic="3 surprising facts about artificial intelligence")
    assert report.overall == "fail"
    assert report.structure_status == "defect"
    assert report.detected_items == 2
    assert report.requested_items == 3


def test_9_one_scene_containing_all_facts_fails():
    """9. Verify a single scene with all facts crammed in text fails the structural requirement."""
    scenes = [
        ScriptScene(
            scene_id="scene-01",
            order=1,
            scene_type="talking_head",
            narration="First AI started in 1950, second it folds proteins, and third it creates antibiotics.",
            visual_intent="Host talking about AI",
            asset_query="technology lab host",
            estimated_duration_seconds=12.0,
        ),
    ]
    doc = ScriptDocument(
        content_id="doc-crammed",
        topic="3 surprising facts about artificial intelligence",
        hook="3 facts you did not know.",
        scenes=scenes,
        cta="Follow for more.",
    )
    report = evaluate_script(doc, topic="3 surprising facts about artificial intelligence")
    assert report.overall == "fail"
    assert report.structure_status == "defect"
    assert report.detected_items == 1  # only 1 substantive scene


def test_10_unrelated_non_listicle_topic_remains_flexible():
    """10. Verify generic non-listicle topics remain flexible (1 or 2 scenes pass)."""
    scenes = [
        ScriptScene(
            scene_id="scene-01",
            order=1,
            scene_type="talking_head",
            narration="How does gravity bend light around massive galaxies? Einstein predicted it in 1915.",
            visual_intent="Gravitational lensing in deep space",
            asset_query="gravitational lensing telescope",
            estimated_duration_seconds=7.0,
        ),
    ]
    doc = ScriptDocument(
        content_id="doc-generic",
        topic="How gravitational lensing works",
        hook="How does gravity bend light around massive galaxies?",
        scenes=scenes,
        cta="Subscribe for more physics.",
    )
    report = evaluate_script(doc, topic="How gravitational lensing works")
    assert report.overall in ("pass", "pass_with_warnings")
    assert report.structure_type == "general"
    assert report.requested_items is None
    assert report.structure_status == "valid"


def test_11_cta_does_not_count_as_fact():
    """11. Verify pure CTA scene is excluded from substantive fact count."""
    cta_scene = ScriptScene(
        scene_id="scene-cta",
        order=3,
        scene_type="cta",
        narration="Subscribe for more daily videos!",
        visual_intent="Subscribe icon",
        asset_query="subscribe button",
        estimated_duration_seconds=2.0,
    )
    assert is_pure_cta_scene(cta_scene) is True

    fact_scene = ScriptScene(
        scene_id="scene-01",
        order=1,
        scene_type="broll",
        narration="AI models can predict weather patterns weeks in advance with unprecedented accuracy.",
        visual_intent="Weather satellite imagery",
        asset_query="meteorology satellite weather",
        estimated_duration_seconds=5.0,
    )
    assert is_pure_cta_scene(fact_scene) is False
    assert count_substantive_fact_units([fact_scene, cta_scene]) == 1


def test_12_structure_defect_produces_useful_reason():
    """12. Verify list_structure defect message clearly explains expected vs detected counts."""
    scenes = [
        ScriptScene(
            scene_id="scene-01",
            order=1,
            scene_type="talking_head",
            narration="AI is growing fast in 2026.",
            visual_intent="AI server rack",
            asset_query="AI server rack",
            estimated_duration_seconds=4.0,
        ),
    ]
    doc = ScriptDocument(
        content_id="doc-reason-test",
        topic="5 key AI developments",
        hook="Here are 5 key AI developments.",
        scenes=scenes,
    )
    report = evaluate_script(doc, topic="5 key AI developments")
    list_check = next((c for c in report.checks if c.check_name == "list_structure"), None)
    assert list_check is not None
    assert "expected at least 5" in list_check.message
    assert "detected only 1" in list_check.message


# ====================================================================
# REGENERATION TESTS (13-16)
# ====================================================================

def test_13_list_structure_defect_maps_to_targeted_regeneration():
    """13. Verify classify_qa_defect properly maps list_structure defect to LIST_STRUCTURE_DEFECT."""
    class DummyFinding:
        def __init__(self, message, category, check_id):
            self.message = message
            self.category = category
            self.check_id = check_id

    finding = DummyFinding(
        message="List structure defect: expected at least 3 distinct fact/content scenes, but detected only 1.",
        category="list_structure",
        check_id="check-list-structure",
    )
    defect_type, reason, instruction, target_stage = classify_qa_defect([finding])
    assert defect_type == RegenerationDefectType.LIST_STRUCTURE_DEFECT
    assert target_stage == "SCRIPT"
    assert "one clear scene/fact unit" in instruction


def test_14_regeneration_preserves_research(tmp_path):
    """14. Verify that targeted script regeneration preserves research and does not re-fetch research."""
    db_path = tmp_path / "test_regen_research.db"
    db = DBManager(db_path)
    db.init_schema()
    cfg = Config(artifacts_dir=tmp_path, db_path=db_path)
    orch = PipelineOrchestrator(config=cfg, db=db)

    # Pre-seed research in DB
    topic = "3 surprising facts about artificial intelligence"
    req_id = "req-preseed-01"
    rep_id = "rep-preseed-01"
    db.create_research_request(req_id, topic)
    db.save_research_report(rep_id, req_id, topic, status="completed", summary="Preseeded research summary")
    db.record_research_evidence("ev-01", rep_id, "src-01", "Fact 1 evidence", 0.9, "discovered")

    # Run orchestrator
    res = orch.run_pipeline(
        job_id="job-regen-preserve",
        topic=topic,
        channel_id="tech_shorts",
        policy="mock",
        llm_provider="mock",
        research_provider="mock_search",
        tts_provider="none",
        production_engine="ffmpeg",
        asset_provider="local",
    )
    # Ensure research report in DB is still the original preseeded one
    latest_rep = db.get_latest_research_report_for_topic(topic)
    assert latest_rep is not None
    assert latest_rep["report_id"] == rep_id


def test_15_regeneration_is_bounded_by_max_attempts(tmp_path):
    """15. Verify script regeneration stops when max_regeneration_attempts is reached."""
    db_path = tmp_path / "test_bounded.db"
    db = DBManager(db_path)
    db.init_schema()
    cfg = Config(artifacts_dir=tmp_path, db_path=db_path)
    orch = PipelineOrchestrator(config=cfg, db=db)

    # Mock script provider to always return an invalid 1-scene script for a 3-item topic
    class AlwaysSingleSceneMock:
        provider_name = "mock_single"
        def generate_script(self, *args, **kwargs):
            return ScriptDocument(
                content_id="doc-bounded",
                topic="3 surprising facts about artificial intelligence",
                hook="Single scene hook",
                scenes=[
                    ScriptScene(
                        scene_id="scene-01",
                        order=1,
                        scene_type="talking_head",
                        narration="Only one fact narration here.",
                        visual_intent="lab",
                        asset_query="lab",
                        estimated_duration_seconds=5.0,
                    )
                ],
            )

    with patch("autopilot.providers.openai_llm_provider.get_llm_provider", return_value=AlwaysSingleSceneMock()):
        with pytest.raises(PipelineError) as exc_info:
            orch.run_pipeline(
                job_id="job-bounded-fail",
                topic="3 surprising facts about artificial intelligence",
                channel_id="tech_shorts",
                policy="mock",
                llm_provider="mock",
                research_provider="mock_search",
                tts_provider="none",
                production_engine="ffmpeg",
                max_regeneration_attempts=2,
            )
        assert "Script Quality Gate Blocked" in str(exc_info.value) or "attempts" in str(exc_info.value)


def test_16_persistent_failure_becomes_needs_review(tmp_path):
    """16. Verify persistent structure failure marks job as NEEDS_REVIEW in database."""
    db_path = tmp_path / "test_needs_review.db"
    db = DBManager(db_path)
    db.init_schema()
    cfg = Config(artifacts_dir=tmp_path, db_path=db_path)
    orch = PipelineOrchestrator(config=cfg, db=db)

    class AlwaysSingleSceneMock:
        provider_name = "mock_single"
        def generate_script(self, *args, **kwargs):
            return ScriptDocument(
                content_id="doc-nr",
                topic="3 surprising facts about artificial intelligence",
                hook="Single scene hook",
                scenes=[
                    ScriptScene(
                        scene_id="scene-01",
                        order=1,
                        scene_type="talking_head",
                        narration="Single fact.",
                        visual_intent="lab",
                        asset_query="lab",
                        estimated_duration_seconds=5.0,
                    )
                ],
            )

    job_id = "job-nr-check"
    with patch("autopilot.providers.openai_llm_provider.get_llm_provider", return_value=AlwaysSingleSceneMock()):
        try:
            orch.run_pipeline(
                job_id=job_id,
                topic="3 surprising facts about artificial intelligence",
                channel_id="tech_shorts",
                policy="mock",
                llm_provider="mock",
                research_provider="mock_search",
                tts_provider="none",
                production_engine="ffmpeg",
                max_regeneration_attempts=1,
            )
        except PipelineError:
            pass

    job_record = db.get_job(job_id)
    assert job_record is not None
    assert job_record["status"] == WorkflowState.NEEDS_REVIEW.value
