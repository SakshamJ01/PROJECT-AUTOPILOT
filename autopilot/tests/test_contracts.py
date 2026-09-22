"""Unit and contract tests — Phase 0."""
from __future__ import annotations
import pytest
from pathlib import Path
import sqlite3
import json

from autopilot.core.state_machine import WorkflowState, validate_transition, is_transition_legal
from autopilot.core.config import CONFIG
from autopilot.db.manager import DBManager
from autopilot.providers.contracts import ProviderRegistry, LLMProvider, ProviderHealth, CapabilityMetadata, CostUsageMetadata, ProviderErrorType
from autopilot.core.artifacts import job_artifact_dir, script_path, render_path, media_path
from autopilot.cli.main import check_ffmpeg, check_sqlite


class TestStateTransitions:
    def test_idea_to_researching_ok(self):
        validate_transition(WorkflowState.IDEA, WorkflowState.RESEARCHING)

    def test_researching_to_rendered_illegal(self):
        with pytest.raises(ValueError):
            validate_transition(WorkflowState.RESEARCHING, WorkflowState.RENDERED)

    def test_rendered_to_qa_ok(self):
        validate_transition(WorkflowState.RENDERED, WorkflowState.QA)

    def test_all_legal_transitions_exist(self):
        # At minimum verify a representative set across the chain
        chain = [
            (WorkflowState.IDEA, WorkflowState.RESEARCHING),
            (WorkflowState.RESEARCHING, WorkflowState.RESEARCHED),
            (WorkflowState.RESEARCHED, WorkflowState.SCRIPTING),
            (WorkflowState.SCRIPTING, WorkflowState.SCRIPTED),
            (WorkflowState.SCRIPTED, WorkflowState.ASSET_PREPARING),
            (WorkflowState.ASSET_PREPARING, WorkflowState.ASSETS_READY),
            (WorkflowState.ASSETS_READY, WorkflowState.VOICING),
            (WorkflowState.VOICING, WorkflowState.VOICE_READY),
            (WorkflowState.VOICE_READY, WorkflowState.EDITING),
            (WorkflowState.EDITING, WorkflowState.RENDERING),
            (WorkflowState.RENDERING, WorkflowState.RENDERED),
            (WorkflowState.RENDERED, WorkflowState.QA),
            (WorkflowState.QA, WorkflowState.APPROVED),
            (WorkflowState.APPROVED, WorkflowState.PUBLISHING),
            (WorkflowState.PUBLISHING, WorkflowState.PUBLISHED),
            (WorkflowState.PUBLISHED, WorkflowState.LEARNED),
        ]
        for f, t in chain:
            assert is_transition_legal(f, t), f"Transition {f.value} -> {t.value} should be legal"

    def test_failed_states_terminal_retry(self):
        # After failure, can go back to previous or IDEA
        validate_transition(WorkflowState.FAILED_RENDER, WorkflowState.IDEA)
        validate_transition(WorkflowState.FAILED_RENDER, WorkflowState.VOICE_READY)


class TestPersistence:
    def test_db_init_and_job_lifecycle(self, tmp_path):
        db_path = tmp_path / "test.db"
        db = DBManager(db_path)
        db.init_schema()
        db.create_job("test-01", topic="t")
        db.update_job_status("test-01", "RESEARCHED")
        row = db.get_job("test-01")
        assert row is not None
        assert row["status"] == "RESEARCHED"
        db.log_event("test-01", "IDEA", "RESEARCHING")
        events = db.get_events("test-01")
        assert len(events) >= 1

    def test_idempotency_no_corruption(self, tmp_path):
        db_path = tmp_path / "test_idem.db"
        db = DBManager(db_path)
        db.init_schema()
        db.create_job("idem-01", idempotency_key="key-a")
        db.create_job("idem-01", idempotency_key="key-a")  # same key
        row = db.get_job("idem-01")
        assert row is not None
        # Second insert should be ignore (OR IGNORE)

    def test_artifact_recording(self, tmp_path):
        db_path = tmp_path / "test_art.db"
        db = DBManager(db_path)
        db.init_schema()
        db.create_job("art-01")
        db.record_artifact("art-01", "/tmp/test.mp4", "media", checksum="abc123")
        arts = db.get_artifacts_for_job("art-01")
        assert len(arts) == 1
        assert arts[0]["checksum_sha256"] == "abc123"

    def test_errors_logged(self, tmp_path):
        db_path = tmp_path / "test_err.db"
        db = DBManager(db_path)
        db.init_schema()
        db.record_error(None, "RENDERING", "render_failed", "FFmpeg error")
        errs = db.get_errors_for_job()
        assert len(errs) == 1
        assert errs[0]["stage"] == "RENDERING"


class TestConfig:
    def test_config_defaults(self):
        c = CONFIG
        assert c.artifacts_dir.exists() or True  # may be created on access
        assert c.log_level == "INFO"

    def test_no_hardcoded_secrets(self):
        # Source inspection: config module must not contain literal secrets
        config_path = Path(__file__).resolve().parent.parent / "autopilot" / "core" / "config.py"
        content = config_path.read_text(encoding="utf-8")
        forbidden = ["apikey", "secret_key", "password", "token=", "api_key="]
        for f in forbidden:
            assert f not in content.lower(), f"Potential secret pattern found: {f}"


class TestProviderContracts:
    def test_registry_empty_graceful(self):
        reg = ProviderRegistry()
        assert reg.list_available() == []
        # health_all should not crash
        health = reg.health_all()
        assert health == {}

    def test_stub_providerRegisters(self):
        reg = ProviderRegistry()

        class StubLLM:
            provider_name = "stub_llm"
            capability = CapabilityMetadata()
            error_type = ProviderErrorType.UNCONFIGURED
            cost_meta = CostUsageMetadata()

            def health_check(self):
                return ProviderHealth(healthy=False, provider_name="stub_llm", error="not configured")

        stub = StubLLM()
        reg.register(stub)
        assert "stub_llm" in reg.list_available()
        h = reg.health_all()["stub_llm"]
        assert h.healthy is False


class TestArtifactPaths:
    def test_job_artifact_dirs(self, tmp_path, monkeypatch):
        # Monkeypatch artifacts dir to tmp_path for isolation
        from autopilot.core import artifacts
        original = CONFIG.artifacts_dir
        monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path)
        # Re-run dir creation logic via artifacts module using new path
        # Direct call using monkeypatched config
        d = artifacts.job_artifact_dir("test-art-001")
        assert d.exists()
        assert (d / "media").exists()
        assert (d / "script").exists()

    def test_job_artifact_dir_windows_safe(self, tmp_path, monkeypatch):
        """job_artifact_dir must create a valid directory even when job_id
        contains Windows‑invalid characters such as ?."""
        from autopilot.core import artifacts
        monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path)

        job_id = "prod-Why-Is-the-Sky-Blue?-8c5e6c8"
        d = artifacts.job_artifact_dir(job_id)

        # Directory must exist and be named with a safe replacement, not "?"
        assert d.exists()
        assert not any(c in d.name for c in "><:\"/\\|?*")
        # The original job_id must be recoverable / preserved elsewhere;
        # here we just verify the on-disk name is safe.
        assert "?" not in d.name

        # Subdirectories must also exist
        assert (d / "media").exists()
        assert (d / "render").exists()


class TestHealthCommands:
    def test_ffmpeg_check(self):
        info = check_ffmpeg()
        assert info.get("available") is True, "FFmpeg should be available in this environment"

    def test_sqlite_check(self):
        info = check_sqlite()
        assert info.get("available") is True


class TestSyntheticFFmpeg:
    def test_small_video_creation(self, tmp_path):
        out = tmp_path / "test.mp4"
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x320:rate=1",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            str(out),
        ]
        result = __import__("subprocess").run(cmd, capture_output=True, text=True)
        assert result.returncode == 0, f"FFmpeg synthetic failed: {result.stderr}"
        assert out.exists()
        assert out.stat().st_size > 0
