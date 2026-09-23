"""Regression tests for Windows-invalid log filenames.

Job IDs can contain characters that are illegal in Windows filenames (e.g. the
'?' in prod-Why-Is-the-Sky-Blue?-0af365a5). The logger must sanitise only the
on-disk filename while preserving the original job_id inside the JSON entries.
"""
from __future__ import annotations
import json

import pytest

from autopilot.core.config import CONFIG
from autopilot.core.logging import StructuredLogger, sanitize_log_filename


PROD_JOB_ID = "prod-Why-Is-the-Sky-Blue?-0af365a5"


@pytest.fixture
def isolated_logs(tmp_path, monkeypatch):
    """Redirect log writes to a per-test tmp dir so the real artifacts stay clean."""
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    return tmp_path / "artifacts" / "logs"


def _read_entries(logger: StructuredLogger) -> list[dict]:
    lines = logger.log_file.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_sanitize_replaces_windows_invalid_chars():
    """Every Windows-invalid filename character is replaced."""
    for ch in '<>:"/\\|?*':
        assert ch not in sanitize_log_filename(f"job{ch}id")
    assert sanitize_log_filename(PROD_JOB_ID) == "prod-Why-Is-the-Sky-Blue_-0af365a5"


def test_sanitize_strips_control_codes():
    """Control codes are replaced 1:1 so distinct job IDs stay distinguishable."""
    clean = sanitize_log_filename("job\x00\x07id")
    assert clean == "job__id"
    assert all(ord(c) > 0x1F for c in clean)


def test_sanitize_handles_trailing_dots_and_spaces():
    """Windows strips trailing dots/spaces; the sanitizer must do it deterministically."""
    assert sanitize_log_filename("job .") == "job"
    assert sanitize_log_filename("job...") == "job"
    assert not sanitize_log_filename("job.").endswith(".")


def test_sanitize_escapes_reserved_windows_device_names():
    """Reserved device names stay reserved even with an extension."""
    for reserved in ("CON", "PRN", "AUX", "NUL", "COM1", "LPT9"):
        clean = sanitize_log_filename(reserved)
        assert clean != reserved
        assert clean.upper() not in {"CON", "PRN", "AUX", "NUL", "COM1", "LPT9"}


def test_sanitize_falls_back_for_empty_input():
    """Fallback only when nothing usable remains; '???' -> '___' is still a valid name."""
    assert sanitize_log_filename("") == "job"
    assert sanitize_log_filename("...") == "job"
    assert sanitize_log_filename("   ") == "job"
    assert sanitize_log_filename("???") == "___"


def test_sanitize_is_deterministic():
    assert sanitize_log_filename(PROD_JOB_ID) == sanitize_log_filename(PROD_JOB_ID)


def test_sanitize_leaves_valid_job_ids_untouched():
    """Already-safe job IDs must not change (backward compatibility with existing logs)."""
    for job_id in ("system", "smoke-phase0-001", "worker-10760-1cd7e4", "prod-Test-topic-011f1ce2"):
        assert sanitize_log_filename(job_id) == job_id


def test_logger_with_invalid_job_id_writes_without_error(isolated_logs):
    """The reported crash: a '?' in the job_id raised OSError on Windows."""
    logger = StructuredLogger(job_id=PROD_JOB_ID, stage="regression")
    logger.info("started", details={"topic": "sky"})

    assert logger.log_file.exists()
    assert "?" not in logger.log_file.name
    entries = _read_entries(logger)
    assert len(entries) == 1
    # Original job_id is preserved verbatim inside the JSON entry.
    assert entries[0]["job_id"] == PROD_JOB_ID
    assert entries[0]["stage"] == "regression"


def test_logger_appends_to_same_sanitized_file(isolated_logs):
    """Repeated writes with the same unsafe job_id hit one deterministic file."""
    logger = StructuredLogger(job_id=PROD_JOB_ID, stage="regression")
    logger.info("first")
    logger.warning("second")

    entries = _read_entries(logger)
    assert [e["event"] for e in entries] == ["first", "second"]
    assert all(e["job_id"] == PROD_JOB_ID for e in entries)


def test_logger_default_system_job_id(isolated_logs):
    logger = StructuredLogger(stage="init")
    logger.info("boot")
    assert logger.job_id == "system"
    assert logger.log_file.name == "system.jsonl"


def test_sanitized_log_filename_is_valid_on_windows(isolated_logs):
    """Round-trip check that the produced path is openable on the host OS."""
    logger = StructuredLogger(job_id='aux<>:"/\\|?*CON', stage="adversarial")
    logger.error("boom", error="test")
    assert logger.log_file.exists()
    assert _read_entries(logger)[0]["error"] == "test"
