"""Tests for MoneyPrinterTurboAdapter — Upstream API compatibility with MoneyPrinterTurbo v1.3.6."""
import json
import pytest
import urllib.error
from pathlib import Path
from unittest.mock import patch, MagicMock

from autopilot.core.contracts import (
    ProductionRequest,
    ProductionResult,
    ScriptDocument,
    ScriptScene,
)
from autopilot.providers.production.moneyprinter_adapter import (
    MoneyPrinterTurboAdapter,
    ProductionEngineUnavailableError,
)


def _make_mock_response(data_dict=None, raw_body=None, status=200):
    mock = MagicMock()
    mock.status = status
    mock.code = status
    mock.__enter__.return_value = mock
    if raw_body is not None:
        mock.read.return_value = raw_body
    elif data_dict is not None:
        mock.read.return_value = json.dumps(data_dict).encode("utf-8")
    else:
        mock.read.return_value = b""
    return mock


def test_moneyprinter_default_endpoint_and_version():
    """Verify default endpoint resolves to port 8080 and reports v1.3.6 engine version."""
    adapter = MoneyPrinterTurboAdapter()
    assert "8080" in adapter.endpoint
    assert adapter.endpoint == "http://127.0.0.1:8080"
    assert adapter.engine_version == "v1.3.6"
    assert adapter.engine_name == "moneyprinterturbo"


# 1. Current MPT health endpoint succeeds
def test_moneyprinter_health_endpoint_succeeds():
    """Live MPT health endpoint (/api/v1/tasks?page=1&page_size=1) returning HTTP 200 succeeds."""
    adapter = MoneyPrinterTurboAdapter(endpoint="http://127.0.0.1:8080")
    success_resp = _make_mock_response({
        "status": 200,
        "message": "success",
        "data": {"tasks": [], "total": 0, "page": 1, "page_size": 1},
    })

    with patch("urllib.request.urlopen", return_value=success_resp) as mock_urlopen:
        health = adapter.health_check()
        assert health["healthy"] is True
        assert health["mode"] == "http_api"
        assert health["version"] == "v1.3.6"
        # Verify it targeted /api/v1/tasks?page=1&page_size=1
        call_args = mock_urlopen.call_args[0][0]
        assert "/api/v1/tasks?page=1&page_size=1" in call_args.full_url


# 2. Health endpoint malformed response fails
def test_moneyprinter_health_endpoint_malformed_response_fails():
    """Health check fails if endpoint returns malformed, non-JSON response."""
    adapter = MoneyPrinterTurboAdapter(endpoint="http://127.0.0.1:8080")
    malformed_resp = _make_mock_response(raw_body=b"<html><body>502 Bad Gateway</body></html>", status=200)

    with patch("urllib.request.urlopen", return_value=malformed_resp):
        health = adapter.health_check()
        assert health["healthy"] is False
        assert health["mode"] == "unreachable"
        assert "unreachable" in health["error"].lower()


# 3. Health endpoint 404 fails
def test_moneyprinter_health_endpoint_404_fails():
    """Health check fails clearly if endpoint returns HTTP 404."""
    adapter = MoneyPrinterTurboAdapter(endpoint="http://127.0.0.1:8080")

    def mock_404(req, timeout=None):
        raise urllib.error.HTTPError(
            url="http://127.0.0.1:8080/api/v1/tasks?page=1&page_size=1",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=None,
        )

    with patch("urllib.request.urlopen", side_effect=mock_404):
        health = adapter.health_check()
        assert health["healthy"] is False
        assert health["mode"] == "unreachable"


# 4. Service unavailable fails
def test_moneyprinter_service_unavailable_fails(tmp_path):
    """Offline endpoint or connection refused returns healthy=False with clear error."""
    adapter = MoneyPrinterTurboAdapter(endpoint="http://127.0.0.1:59999", cli_path=str(tmp_path / "nonexistent_mpt"))
    health = adapter.health_check()
    assert health["healthy"] is False
    assert health["mode"] == "unreachable"
    assert "unreachable" in health["error"].lower()


# 5. Current POST /api/v1/videos request shape
def test_moneyprinter_build_task_payload_shape():
    """Builds valid VideoParams schema payload matching upstream MoneyPrinterTurbo v1.3.6."""
    adapter = MoneyPrinterTurboAdapter()
    script = ScriptDocument(
        content_id="test-job-001",
        topic="3 AI Secrets",
        working_title="3 AI Secrets You Won't Believe",
        scenes=[
            ScriptScene(
                scene_id="s1",
                order=1,
                narration="AI is rapidly transforming productivity.",
                visual_intent="A person using a futuristic computer",
            ),
            ScriptScene(
                scene_id="s2",
                order=2,
                narration="Neural networks learn from data.",
                visual_intent="Neural network architecture nodes",
            ),
        ],
    )
    req = ProductionRequest(
        job_id="test-job-001",
        content_id="test-job-001",
        topic="3 AI Secrets",
        script=script,
        output_path="artifacts/jobs/test-job-001/render/final.mp4",
        video_ratio="9:16",
    )
    payload = adapter._build_task_payload(req)
    assert payload["video_subject"] == "3 AI Secrets"
    assert "AI is rapidly transforming productivity." in payload["video_script"]
    assert "Neural networks learn from data." in payload["video_script"]
    assert "A person using a futuristic computer" in payload["video_terms"]
    assert payload["video_aspect"] == "9:16"
    assert payload["video_concat_mode"] == "random"
    assert payload["video_count"] == 1
    assert payload["subtitle_enabled"] is True


# 6. Current task polling shape & 7. Task success state
def test_moneyprinter_generate_task_submit_polling_and_success(tmp_path):
    """Tests full generation lifecycle: POST /api/v1/videos -> GET /api/v1/tasks/{id} polling -> state=1 success."""
    # autostart=False keeps these adapter-contract tests hermetic; the service
    # lifecycle (probe/spawn/readiness) is covered by test_moneyprinter_runtime.py.
    adapter = MoneyPrinterTurboAdapter(endpoint="http://127.0.0.1:8080", poll_interval_seconds=0.01, autostart=False)

    out_file = tmp_path / "final.mp4"
    temp_rendered = tmp_path / "temp_render.mp4"
    temp_rendered.write_bytes(b"MPT_GENERATED_VIDEO_BYTES_1234567890")

    req = ProductionRequest(
        job_id="test-job-poll",
        content_id="test-job-poll",
        topic="AI Topic",
        output_path=str(out_file),
        timeout_seconds=5,
    )

    # Health check probe response
    health_resp = _make_mock_response({
        "status": 200,
        "message": "success",
        "data": {"tasks": [], "total": 0, "page": 1, "page_size": 1},
    })

    # Task submit mock (POST /api/v1/videos -> returns task_id)
    submit_resp = _make_mock_response({
        "status": 200,
        "message": "success",
        "data": {"task_id": "task-abc-123"}
    })

    # Task poll mock (GET /api/v1/tasks/task-abc-123 -> state=1 complete)
    poll_resp = _make_mock_response({
        "status": 200,
        "message": "success",
        "data": {
            "task_id": "task-abc-123",
            "state": 1,
            "progress": 100,
            "videos": [str(temp_rendered)],
        }
    })

    polled_urls = []

    def mock_urlopen_router(request, timeout=None):
        url = getattr(request, "full_url", str(request))
        polled_urls.append(url)
        if "/api/v1/tasks?page=1&page_size=1" in url:
            return health_resp
        elif "/api/v1/videos" in url:
            return submit_resp
        elif "/api/v1/tasks/task-abc-123" in url:
            return poll_resp
        return health_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen_router), \
         patch("autopilot.providers.production.moneyprinter_adapter.inspect_media") as mock_inspect:
        mock_inspect.return_value = {
            "valid": True,
            "video": {"width": 1080, "height": 1920, "codec_name": "h264"},
            "audio": {"codec_name": "aac"},
            "format": {"duration": 15.0, "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
        }
        res = adapter.generate(req)

        assert res.success is True
        assert res.job_id == "test-job-poll"
        assert res.duration_sec == 15.0
        assert res.engine_name == "moneyprinterturbo"
        assert res.engine_version == "v1.3.6"
        assert out_file.exists()

        # Verify exact polling endpoint shape
        assert any("/api/v1/tasks/task-abc-123" in u for u in polled_urls)


# 8. Task failure state
def test_moneyprinter_generate_fails_when_task_fails(tmp_path):
    """When MPT task reports state=-1 (failure), adapter must raise clear RuntimeError without fallback."""
    adapter = MoneyPrinterTurboAdapter(endpoint="http://127.0.0.1:8080", poll_interval_seconds=0.01, autostart=False)

    out_file = tmp_path / "final.mp4"
    req = ProductionRequest(
        job_id="test-job-fail",
        content_id="test-job-fail",
        topic="Fail Topic",
        output_path=str(out_file),
        timeout_seconds=5,
    )

    health_resp = _make_mock_response({
        "status": 200,
        "message": "success",
        "data": {"tasks": [], "total": 0, "page": 1, "page_size": 1},
    })

    submit_resp = _make_mock_response({
        "status": 200,
        "data": {"task_id": "task-fail-999"}
    })

    poll_resp = _make_mock_response({
        "status": 200,
        "data": {
            "task_id": "task-fail-999",
            "state": -1,
            "error": "Failed to synthesize speech with TTS provider",
        }
    })

    def mock_urlopen_router(request, timeout=None):
        url = getattr(request, "full_url", str(request))
        if "/api/v1/tasks?page=1&page_size=1" in url:
            return health_resp
        elif "/api/v1/videos" in url:
            return submit_resp
        elif "/api/v1/tasks/task-fail-999" in url:
            return poll_resp
        return health_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen_router):
        with pytest.raises(RuntimeError) as exc_info:
            adapter.generate(req)
        assert "Failed to synthesize speech with TTS provider" in str(exc_info.value)


# 9. No FFmpeg fallback & 10. No mock fallback
def test_moneyprinter_no_ffmpeg_or_mock_fallback_when_unavailable():
    """When MoneyPrinterTurbo is offline, generate() must fail closed with ProductionEngineUnavailableError."""
    adapter = MoneyPrinterTurboAdapter(endpoint="http://127.0.0.1:59999", autostart=False)
    req = ProductionRequest(
        job_id="test-job-002",
        content_id="test-job-002",
        topic="Test Topic",
        output_path="artifacts/jobs/test-job-002/render/final.mp4",
    )
    with pytest.raises(ProductionEngineUnavailableError) as exc_info:
        adapter.generate(req)

    err_str = str(exc_info.value).lower()
    assert "unavailable" in err_str
    assert "legacy fallback available via --production-engine ffmpeg" in err_str
    # Verify it did not fall back or return a mock result
    assert not Path("artifacts/jobs/test-job-002/render/final.mp4").exists()



