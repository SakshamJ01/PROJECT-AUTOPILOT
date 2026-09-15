"""Tests proving engine-aware resume and cache invalidation."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from autopilot.core.config import CONFIG
from autopilot.core.contracts import RenderPlan, ProductionResult
from autopilot.core.pipeline import PipelineOrchestrator, PipelineError


def test_resume_invalidates_when_production_engine_changes(tmp_path, monkeypatch):
    """Proves that a render generated with 'ffmpeg' is invalidated if 'moneyprinterturbo' is requested."""
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    monkeypatch.setattr(CONFIG, "db_path", tmp_path / "test.db")
    monkeypatch.setattr(CONFIG, "moneyprinter_endpoint", "http://127.0.0.1:59999")

    orchestrator = PipelineOrchestrator(CONFIG)
    job_id = "test-inval-001"

    # 1. First run with explicit ffmpeg backend
    res1 = orchestrator.run_pipeline(
        job_id=job_id,
        topic="Test Topic",
        production_engine="ffmpeg",
        tts_provider="mock",
        asset_provider="local",
        llm_provider="mock",
        research_provider="mock_search",
    )
    assert res1["status"] in ("APPROVED", "QA", "RENDERED", "success")

    # Verify render_plan recorded engine as ffmpeg
    plan_file = tmp_path / "artifacts" / "jobs" / job_id / "render" / "render_plan.json"
    assert plan_file.exists()
    plan_data = json.loads(plan_file.read_text(encoding="utf-8"))
    assert plan_data.get("production_engine") == "ffmpeg"

    # 2. Second run for same job but requesting moneyprinterturbo
    # It must NOT consider the ffmpeg media valid, but must attempt MoneyPrinterTurbo (and fail because endpoint is offline)
    with pytest.raises(PipelineError) as exc_info:
        orchestrator.run_pipeline(
            job_id=job_id,
            topic="Test Topic",
            production_engine="moneyprinterturbo",
            tts_provider="mock",
            asset_provider="local",
            llm_provider="mock",
            research_provider="mock_search",
        )

    assert exc_info.value.stage == "RENDER"
    assert "moneyprinterturbo" in str(exc_info.value).lower()
