"""Integration tests for the Strict QA Publishing Gate — Milestone 6."""
import json
import pytest
from pathlib import Path

from autopilot.core.config import CONFIG
from autopilot.core.contracts import QAStatus, PublishStatus
from autopilot.core.publisher import PublishingEngine
from autopilot.providers.mock_publisher import MockPublisher
from autopilot.db.manager import DBManager


def create_test_media(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"TEST_VIDEO_PAYLOAD_FOR_M6_PUBLISH_GATE")
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_publish_gate_allows_qa_pass(tmp_path, monkeypatch):
    job_id = "test-gate-pass-001"
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    db = DBManager(tmp_path / "test.db")
    db.init_schema()
    db.create_job(job_id=job_id, topic="Gate Pass Topic")

    media_file = tmp_path / "artifacts" / "jobs" / job_id / "render" / "final.mp4"
    chk = create_test_media(media_file)

    # Write passing QA receipt
    qa_dir = tmp_path / "artifacts" / "jobs" / job_id / "quality"
    qa_dir.mkdir(parents=True, exist_ok=True)
    qa_receipt = {
        "receipt_id": f"rcpt-qa-{job_id}",
        "job_id": job_id,
        "content_id": job_id,
        "status": "PASS",
        "publish_allowed": True,
        "media_path": str(media_file),
        "media_checksum_sha256": chk,
    }
    (qa_dir / "receipt.json").write_text(json.dumps(qa_receipt), encoding="utf-8")

    engine = PublishingEngine(CONFIG)
    mock_prov = MockPublisher()
    result = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        dry_run=True,
        provider=mock_prov,
        db_manager=db,
    )

    assert result.success is True
    assert result.status == PublishStatus.DRY_RUN
    assert result.receipt is not None


def test_publish_gate_blocks_when_composite_gate_blocks_despite_passing_receipt(tmp_path, monkeypatch):
    """A permissive technical receipt must not override a composite hard block.

    Reproduces the real fail-open: technical QA passed with zero findings, but
    Creative QA hard-blocked on pacing. The nested PublishReceipt written to
    disk still said publish_allowed=true, so the publisher would have uploaded
    a video the readiness gate had rejected.
    """
    job_id = "test-gate-composite-block-001"
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    db = DBManager(tmp_path / "test.db")
    db.init_schema()
    db.create_job(job_id=job_id, topic="Composite Block Topic")

    media_file = tmp_path / "artifacts" / "jobs" / job_id / "render" / "final.mp4"
    chk = create_test_media(media_file)

    # Technical QA passed cleanly...
    qa_dir = tmp_path / "artifacts" / "jobs" / job_id / "quality"
    qa_dir.mkdir(parents=True, exist_ok=True)
    (qa_dir / "receipt.json").write_text(
        json.dumps({
            "receipt_id": f"rcpt-qa-{job_id}",
            "job_id": job_id,
            "content_id": job_id,
            "status": "PASS",
            "publish_allowed": True,
            "media_path": str(media_file),
            "media_checksum_sha256": chk,
        }),
        encoding="utf-8",
    )

    # ...but the composite publish-readiness gate blocked it.
    gate_dir = tmp_path / "artifacts" / "jobs" / job_id / "qa"
    gate_dir.mkdir(parents=True, exist_ok=True)
    (gate_dir / "publish_readiness.json").write_text(
        json.dumps({
            "is_ready_to_publish": False,
            "status": "BLOCKED",
            "technical_qa_passed": True,
            "creative_qa_passed": False,
            "blocking_reasons": ["Creative QA hard block (overall 82.7); blocking dimensions: Pacing & Cadence"],
        }),
        encoding="utf-8",
    )

    engine = PublishingEngine(CONFIG)
    result = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        dry_run=False,
        provider=MockPublisher(),
        db_manager=db,
    )

    assert result.success is False
    assert result.error is not None
    assert "readiness" in result.error.message.lower()


def test_gate_verdict_is_propagated_into_the_receipt():
    """A composite block must reach the receipt the publisher reads.

    Guards against a silent regression: the propagation helper once raised
    NameError, which the surrounding `except Exception` swallowed, leaving a
    blocked job with a receipt that carried no blocking finding at all.
    """
    from autopilot.cli.main import _propagate_gate_verdict_to_receipt
    from autopilot.core.contracts import QAReport, PublishReceipt, QAStatus

    class _Decision:
        is_ready_to_publish = False
        technical_qa_passed = True
        creative_qa_passed = False
        overall_confidence = 0.89
        blocking_reasons = ["Creative QA hard block (overall 82.7); blocking dimensions: Pacing & Cadence"]
        warning_reasons = ["Warning [VISUAL_MISMATCH_SCENE-02]: Matched"]

    report = QAReport(
        report_id="q",
        job_id="j",
        content_id="j",
        status=QAStatus.BLOCK,
        publish_allowed=False,
        receipt=PublishReceipt(receipt_id="r", job_id="j", content_id="j"),
    )
    # The receipt starts out optimistic, exactly as technical QA leaves it.
    assert report.receipt.publish_allowed is True
    assert report.receipt.blocking_findings == []

    _propagate_gate_verdict_to_receipt(report, _Decision())

    assert report.receipt.publish_allowed is False
    assert len(report.receipt.blocking_findings) == 1
    assert "Pacing & Cadence" in report.receipt.blocking_findings[0].message
    assert len(report.receipt.warnings) == 1


def _report_with(checks):
    from autopilot.core.contracts import QAReport, QAStatus
    return QAReport(
        report_id="q", job_id="j", content_id="j",
        status=QAStatus.PASS, publish_allowed=True, checks=checks,
    )


def _check(check_id, status=None):
    from autopilot.core.contracts import QACheck, QAStatus
    return QACheck(
        check_id=check_id, category="c",
        status=status or QAStatus.PASS, message="m",
    )


_INVARIANT_CHECKS = (
    "check-render-plan-consistency",
    "check-provenance-integrity",
    "check-scene-coverage",
)


@pytest.mark.parametrize(
    "checks, expect_rights, expect_invariants, why",
    [
        ([], False, False, "no checks ran: nothing may be claimed"),
        (list(_INVARIANT_CHECKS), False, False, "rights check missing"),
        (
            [*_INVARIANT_CHECKS, "check-rights-gate"],
            True,
            False,
            "missing render_provenance.json is not evidence of anything",
        ),
    ],
)
def test_rights_and_invariants_evidence_fails_closed(checks, expect_rights, expect_invariants, why):
    """Gate inputs must be derived from real checks, never assumed.

    `rights_verified` and `invariants_verified` used to be hardcoded True, so
    the composite gate certified verification it never performed.
    """
    from autopilot.cli.main import _derive_rights_and_invariants

    built = [_check(c) for c in checks]
    rights, invariants, evidence = _derive_rights_and_invariants(_report_with(built), "missing-job")

    assert rights is expect_rights, f"rights mismatch: {why}"
    assert invariants is expect_invariants, f"invariants mismatch: {why}"
    assert "rights" in evidence and "invariants" in evidence


def test_non_passing_rights_check_does_not_verify():
    from autopilot.cli.main import _derive_rights_and_invariants
    from autopilot.core.contracts import QAStatus

    checks = [_check(c) for c in _INVARIANT_CHECKS]
    checks.append(_check("check-rights-gate", QAStatus.WARN))
    rights, _, _ = _derive_rights_and_invariants(_report_with(checks), "missing-job")
    assert rights is False


def test_publish_gate_blocks_qa_block(tmp_path, monkeypatch):
    job_id = "test-gate-block-001"
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    db = DBManager(tmp_path / "test.db")
    db.init_schema()
    db.create_job(job_id=job_id, topic="Gate Block Topic")

    media_file = tmp_path / "artifacts" / "jobs" / job_id / "render" / "final.mp4"
    chk = create_test_media(media_file)

    # Write BLOCKING QA receipt
    qa_dir = tmp_path / "artifacts" / "jobs" / job_id / "quality"
    qa_dir.mkdir(parents=True, exist_ok=True)
    qa_receipt = {
        "receipt_id": f"rcpt-qa-{job_id}",
        "job_id": job_id,
        "content_id": job_id,
        "status": "BLOCK",
        "publish_allowed": False,
        "media_path": str(media_file),
        "media_checksum_sha256": chk,
    }
    (qa_dir / "receipt.json").write_text(json.dumps(qa_receipt), encoding="utf-8")

    engine = PublishingEngine(CONFIG)
    mock_prov = MockPublisher()
    result = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        dry_run=False,
        provider=mock_prov,
        db_manager=db,
    )

    assert result.success is False
    assert result.status == PublishStatus.BLOCKED_QA
    assert result.error is not None
    assert result.error.error_code == "QA_GATE_BLOCKED"
    # Ensure provider was never called
    job_row = db.get_job(job_id)
    assert job_row["status"] == "FAILED_PUBLISH"


def test_publish_gate_blocks_missing_qa_receipt(tmp_path, monkeypatch):
    job_id = "test-gate-missing-qa"
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    db = DBManager(tmp_path / "test.db")
    db.init_schema()
    db.create_job(job_id=job_id, topic="Missing QA Topic")

    media_file = tmp_path / "artifacts" / "jobs" / job_id / "render" / "final.mp4"
    create_test_media(media_file)

    engine = PublishingEngine(CONFIG)
    mock_prov = MockPublisher()
    result = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        provider=mock_prov,
        db_manager=db,
    )

    assert result.success is False
    assert result.status == PublishStatus.BLOCKED_QA
    assert result.error.error_code == "QA_REPORT_MISSING"


def test_publish_gate_blocks_checksum_mismatch(tmp_path, monkeypatch):
    job_id = "test-gate-checksum-mismatch"
    monkeypatch.setattr(CONFIG, "artifacts_dir", tmp_path / "artifacts")
    db = DBManager(tmp_path / "test.db")
    db.init_schema()
    db.create_job(job_id=job_id, topic="Mismatch Topic")

    media_file = tmp_path / "artifacts" / "jobs" / job_id / "render" / "final.mp4"
    create_test_media(media_file)

    # QA recorded a different checksum
    qa_dir = tmp_path / "artifacts" / "jobs" / job_id / "quality"
    qa_dir.mkdir(parents=True, exist_ok=True)
    qa_receipt = {
        "receipt_id": f"rcpt-qa-{job_id}",
        "job_id": job_id,
        "content_id": job_id,
        "status": "PASS",
        "publish_allowed": True,
        "media_path": str(media_file),
        "media_checksum_sha256": "tampered_or_different_hash_value",
    }
    (qa_dir / "receipt.json").write_text(json.dumps(qa_receipt), encoding="utf-8")

    engine = PublishingEngine(CONFIG)
    mock_prov = MockPublisher()
    result = engine.publish_job(
        job_id=job_id,
        platform="youtube",
        provider=mock_prov,
        db_manager=db,
    )

    assert result.success is False
    assert result.status == PublishStatus.BLOCKED_QA
    assert result.error.error_code == "CHECKSUM_MISMATCH"
