import os
import pytest
import sqlite3
from unittest.mock import MagicMock, patch

from autopilot.db.manager import DBManager
from autopilot.core.pipeline import PipelineOrchestrator
from autopilot.cli.main import run_produce
from autopilot.providers.openai_llm_provider import OpenAICompatibleLLMProvider
from autopilot.providers.mock_script import MockScriptProvider
from autopilot.core.contracts import ScriptDocument


def json_response_bytes(content_str: str) -> bytes:
    import json
    payload = {
        "choices": [
            {
                "message": {
                    "content": content_str
                }
            }
        ]
    }
    return json.dumps(payload).encode("utf-8")


@pytest.fixture
def tmp_db(tmp_path):
    db_path = tmp_path / "test_autopilot.db"
    db = DBManager(str(db_path))
    db.init_schema()
    return db


def test_pipeline_persists_research_evidence(tmp_path, tmp_db):
    """Pipeline research stage persists research_evidence rows."""
    pipeline = PipelineOrchestrator(db=tmp_db)
    job_id = "test-job-ev-001"
    topic = "Quantum Teleportation Progress"

    pipeline.run_pipeline(job_id=job_id, topic=topic, production_engine="ffmpeg")

    report = tmp_db.get_latest_research_report_for_topic(topic)
    assert report is not None
    assert report["topic"] == topic
    assert len(report["evidence"]) > 0
    assert "source_id" in report["evidence"][0]
    assert "snippet" in report["evidence"][0]


def test_run_produce_creates_and_passes_research(tmp_path, monkeypatch):
    """run_produce() creates research if missing and passes evidence to provider."""
    db_path = tmp_path / "autopilot.db"
    monkeypatch.setattr("autopilot.cli.main.CONFIG.db_path", str(db_path))

    mock_provider = MagicMock()
    mock_provider.provider_name = "mock_test"
    dummy_script = MockScriptProvider().generate_script("AI Research Breakthroughs", content_id="prod-ai-001")
    mock_provider.generate_script.return_value = dummy_script

    with patch("autopilot.providers.mock_script.MockScriptProvider", return_value=mock_provider):
        code = run_produce(topic="AI Research Breakthroughs", llm_provider="mock", policy="development")
        assert code == 0

    assert mock_provider.generate_script.called
    call_kwargs = mock_provider.generate_script.call_args.kwargs
    passed_report = call_kwargs.get("research_report")
    assert passed_report is not None
    assert "evidence" in passed_report
    assert len(passed_report["evidence"]) > 0


def test_existing_research_is_reused_without_regeneration(tmp_path, monkeypatch):
    """Existing completed research report is reused and not regenerated."""
    db_path = tmp_path / "autopilot.db"
    db = DBManager(str(db_path))
    db.init_schema()
    monkeypatch.setattr("autopilot.cli.main.CONFIG.db_path", str(db_path))

    topic = "Reused Research Topic"
    req_id = "req-pre-existing-001"
    rep_id = "rep-pre-existing-001"
    db.create_research_request(req_id, topic)
    db.save_research_report(rep_id, req_id, topic, status="completed", summary="Pre-existing summary")
    db.record_research_evidence(
        evidence_id="ev-pre-001",
        report_id=rep_id,
        source_id="src-pre-001",
        snippet="Pre-existing evidence snippet",
        relevance_score=0.9,
    )

    with patch("autopilot.providers.mock_search.MockSearchProvider.search") as mock_search:
        mock_provider = MagicMock()
        mock_provider.provider_name = "mock_test"
        mock_provider.generate_script.return_value = MockScriptProvider().generate_script(topic, content_id="prod-reused-001")

        with patch("autopilot.providers.mock_script.MockScriptProvider", return_value=mock_provider):
            code = run_produce(topic=topic, llm_provider="mock", policy="development")
            assert code == 0

        # MockSearchProvider should NOT have been called because report existed
        mock_search.assert_not_called()

    passed_report = mock_provider.generate_script.call_args.kwargs.get("research_report")
    assert passed_report["report_id"] == rep_id
    assert passed_report["evidence"][0]["source_id"] == "src-pre-001"


def test_openai_provider_receives_evidence_structure_and_grounds():
    """OpenAICompatibleLLMProvider processes summary + evidence[] and sets research_grounding to grounded."""
    provider = OpenAICompatibleLLMProvider(api_key="test-key", base_url="http://localhost:11434/v1")

    research_report = {
        "report_id": "rep-test-001",
        "topic": "Superconductors",
        "summary": "Recent room-temperature superconductor claims",
        "evidence": [
            {
                "evidence_id": "ev-001",
                "source_id": "src-sup-001",
                "snippet": "LK-99 ambient pressure superconductor measurement notes.",
                "url": "https://example.org/lk99",
                "title": "LK99 Study",
                "publisher": "Physics Journal",
            }
        ],
    }

    mock_llm_json = """{
        "title": "Superconductor Breakthrough",
        "description": "Overview of superconductors",
        "hook_text": "Room temperature superconductors changed everything.",
        "scenes": [
            {"scene_id": "scene_1", "narration": "Researchers tested LK-99.", "visual_description": "Lab setup"}
        ],
        "source_references": ["src-sup-001"]
    }"""

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_response = MagicMock()
        mock_response.read.return_value = json_response_bytes(mock_llm_json)
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        doc = provider.generate_script(
            topic="Superconductors",
            content_id="job-sup-001",
            research_report=research_report,
        )

    assert doc.source_references == ["src-sup-001"]
    assert doc.generation_metadata["research_grounding"] == "grounded"


def test_openai_provider_rejects_fabricated_source_references():
    """Fabricated source references not present in research evidence are filtered out."""
    provider = OpenAICompatibleLLMProvider(api_key="test-key", base_url="http://localhost:11434/v1")

    research_report = {
        "summary": "Valid research summary",
        "evidence": [
            {
                "source_id": "src-valid-001",
                "snippet": "Legitimate study result.",
            }
        ],
    }

    mock_llm_json = """{
        "title": "Fake Source Test",
        "description": "Test description",
        "hook_text": "Hook text",
        "scenes": [{"scene_id": "scene_1", "narration": "Content", "visual_description": "Visual"}],
        "source_references": ["src-valid-001", "src-fake-999"]
    }"""

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_response = MagicMock()
        mock_response.read.return_value = json_response_bytes(mock_llm_json)
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        doc = provider.generate_script(
            topic="Fake Source Test",
            content_id="job-fake-001",
            research_report=research_report,
        )

    assert doc.source_references == ["src-valid-001"]
    assert "src-fake-999" in doc.generation_metadata["rejected_source_references"]
    assert doc.generation_metadata["research_grounding"] == "grounded"


def test_mock_produce_backward_compatible(tmp_path, monkeypatch):
    """Mock/default produce behavior remains backward compatible (uses development policy to skip production enforcement)."""
    db_path = tmp_path / "autopilot.db"
    monkeypatch.setattr("autopilot.cli.main.CONFIG.db_path", str(db_path))

    code = run_produce(topic="Backward Compatibility Topic", llm_provider="mock", policy="development")
    assert code == 0

    db = DBManager(str(db_path))
    report = db.get_latest_research_report_for_topic("Backward Compatibility Topic")
    assert report is not None
    assert len(report["evidence"]) > 0
