"""Tests for Production and Transcription engine contracts and protocols."""
import pytest
from autopilot.core.contracts import (
    ProductionRequest,
    ProductionResult,
    ProductionEngineType,
    RenderPlan,
    RenderOutput,
    ScriptDocument,
    ScriptScene,
    TranscriptionRequest,
    TranscriptionResult,
    WordTimestamp,
    SegmentTimestamp,
)


def test_production_request_and_result_roundtrip():
    req = ProductionRequest(
        job_id="prod-test-001",
        content_id="prod-test-001",
        topic="3 AI Secrets",
        output_path="artifacts/jobs/prod-test-001/render/final.mp4",
        production_engine="moneyprinterturbo",
        video_ratio="9:16",
    )
    assert req.job_id == "prod-test-001"
    assert req.production_engine == "moneyprinterturbo"

    res = ProductionResult(
        job_id="prod-test-001",
        video_path="artifacts/jobs/prod-test-001/render/final.mp4",
        duration_sec=15.5,
        width=1080,
        height=1920,
        engine_name="moneyprinterturbo",
        engine_version="v1.2.0",
        checksum_sha256="abc123sha",
        success=True,
    )
    json_str = res.model_dump_json()
    loaded = ProductionResult.model_validate_json(json_str)
    assert loaded.duration_sec == 15.5
    assert loaded.width == 1080
    assert loaded.height == 1920
    assert loaded.engine_name == "moneyprinterturbo"


def test_transcription_models_roundtrip():
    words = [
        WordTimestamp(word="Hello", start_sec=0.0, end_sec=0.5, probability=0.98),
        WordTimestamp(word="world", start_sec=0.5, end_sec=1.0, probability=0.99),
    ]
    seg = SegmentTimestamp(segment_id=1, start_sec=0.0, end_sec=1.0, text="Hello world", words=words)
    res = TranscriptionResult(
        text="Hello world",
        language="en",
        duration_sec=1.0,
        segments=[seg],
        srt_content="1\n00:00:00,000 --> 00:00:01,000\nHello world\n",
        ass_content="Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,Hello world",
    )
    json_str = res.model_dump_json()
    loaded = TranscriptionResult.model_validate_json(json_str)
    assert len(loaded.segments) == 1
    assert len(loaded.segments[0].words) == 2
    assert loaded.segments[0].words[0].word == "Hello"
