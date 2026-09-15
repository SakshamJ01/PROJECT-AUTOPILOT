"""Tests verifying production engine selection and proving NO silent fallback."""
import pytest
from unittest.mock import patch, MagicMock

from autopilot.core.config import CONFIG
from autopilot.core.contracts import ProductionRequest, ScriptDocument, ScriptScene
from autopilot.providers.production.factory import get_production_engine
from autopilot.providers.production.moneyprinter_adapter import (
    MoneyPrinterTurboAdapter,
    ProductionEngineUnavailableError,
)
from autopilot.providers.production.ffmpeg_adapter import FFmpegProductionAdapter
from autopilot.core.pipeline import PipelineOrchestrator, PipelineError


def test_factory_returns_correct_adapters():
    mpt_engine = get_production_engine("moneyprinterturbo")
    assert isinstance(mpt_engine, MoneyPrinterTurboAdapter)
    assert mpt_engine.engine_name == "moneyprinterturbo"

    ffmpeg_engine = get_production_engine("ffmpeg")
    assert isinstance(ffmpeg_engine, FFmpegProductionAdapter)
    assert ffmpeg_engine.engine_name == "ffmpeg"


def test_factory_rejects_unknown_engine():
    with pytest.raises(ValueError) as exc:
        get_production_engine("unknown_engine_xyz")
    assert "unknown production engine" in str(exc.value).lower()


def test_no_silent_fallback_to_ffmpeg_when_moneyprinter_requested(tmp_path, monkeypatch):
    """Proves that selecting moneyprinterturbo when unavailable raises an explicit error and does NOT fallback to FFmpeg."""
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    monkeypatch.setattr(CONFIG, "db_path", tmp_path / "test.db")
    monkeypatch.setattr(CONFIG, "moneyprinter_endpoint", "http://127.0.0.1:59999")

    orchestrator = PipelineOrchestrator(CONFIG)
    job_id = "test-no-fallback-001"

    with pytest.raises(PipelineError) as exc_info:
        orchestrator.run_pipeline(
            job_id=job_id,
            topic="3 AI Secrets",
            production_engine="moneyprinterturbo",
            tts_provider="mock",
            asset_provider="local",
            llm_provider="mock",
            research_provider="mock_search",
        )

    # Invariant: Must fail in RENDER stage and mention moneyprinterturbo
    assert exc_info.value.stage == "RENDER"
    assert "moneyprinterturbo" in str(exc_info.value).lower()
