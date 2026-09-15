"""Phase 2 tests for Batch Production, Job Isolation, Failure Categorization, and Targeted Regeneration.
Covers:
- Batch production from plain text topics file (.txt)
- Job isolation: job failure does not halt subsequent batch items
- Categorized retries (retryable vs non-retryable)
- Targeted QA defect classification and corrective instruction tracking
- Max regeneration attempts enforcement and transition to NEEDS_REVIEW
"""
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from autopilot.core.batch import BatchProcessor
from autopilot.core.contracts import (
    BatchManifest,
    BatchItem,
    RegenerationDefectType,
    QAFinding,
    QASeverity,
    QAStatus,
    QAReport,
    PublishReceipt,
)
from autopilot.core.pipeline import PipelineOrchestrator, PipelineError, classify_qa_defect
from autopilot.core.state_machine import WorkflowState


def test_batch_parse_plain_text_topics(tmp_path):
    """Verify BatchProcessor can parse plain-text topic lists (.txt)."""
    txt_file = tmp_path / "topics.txt"
    txt_file.write_text(
        "3 fascinating facts about quantum computers\n"
        "# This is a comment\n"
        "Why the Roman Colosseum survived earthquakes\n"
        "\n"
        "How neural networks simulate biological synapses\n",
        encoding="utf-8"
    )

    processor = BatchProcessor()
    manifest = processor.parse_manifest_file(txt_file, channel_id="science_shorts")

    assert manifest is not None
    assert len(manifest.items) == 3
    assert manifest.items[0].topic == "3 fascinating facts about quantum computers"
    assert manifest.items[0].channel_id == "science_shorts"
    assert manifest.items[1].topic == "Why the Roman Colosseum survived earthquakes"
    assert manifest.items[2].topic == "How neural networks simulate biological synapses"


def test_batch_isolated_failures():
    """Verify that an error in job #2 does not halt jobs #1 and #3."""
    items = [
        BatchItem(topic="Topic One", channel_id="science_shorts"),
        BatchItem(topic="Topic Two Fatal", channel_id="science_shorts"),
        BatchItem(topic="Topic Three", channel_id="science_shorts"),
    ]
    manifest = BatchManifest(
        manifest_id="mf-isolation-test",
        name="isolation_test",
        channel_id="science_shorts",
        items=items,
    )

    mock_orch = MagicMock(spec=PipelineOrchestrator)

    def side_effect_run(job_id, topic, **kwargs):
        if "Fatal" in topic:
            raise PipelineError(
                f"Non-retryable contract failure for {topic}",
                category="NON_RETRYABLE",
                stage="SCRIPT",
            )
        return {
            "status": "success",
            "media_path": f"/tmp/{job_id}/final.mp4",
            "qa_status": "PASS",
        }

    mock_orch.run_pipeline.side_effect = side_effect_run

    processor = BatchProcessor()
    result = processor.execute_batch(manifest=manifest, orchestrator=mock_orch)

    assert result["total_jobs"] == 3
    assert result["succeeded_jobs"] == 2
    assert result["failed_jobs"] == 1
    assert result["job_results"][0]["status"] == "succeeded"
    assert result["job_results"][1]["status"] == "failed"
    assert result["job_results"][1]["error_category"] == "NON_RETRYABLE"
    assert result["job_results"][2]["status"] == "succeeded"


def test_qa_defect_classification():
    """Verify classify_qa_defect maps QA issues to correct defect categories."""
    # 1. Hook defect
    hook_finding = QAFinding(
        finding_id="f1",
        check_id="hook_engagement",
        category="editorial",
        severity=QASeverity.CRITICAL,
        message="Hook is weak or generic and lacks curiosity",
    )
    defect, reason, instruction, stage = classify_qa_defect([hook_finding])
    assert defect == RegenerationDefectType.HOOK_DEFECT
    assert stage == "SCRIPT"
    assert "hook" in instruction.lower()

    # 2. Duration defect
    dur_finding = QAFinding(
        finding_id="f2",
        check_id="duration_tolerance",
        category="timing",
        severity=QASeverity.CRITICAL,
        message="Total duration exceeds limit by 12 seconds",
    )
    defect, reason, instruction, stage = classify_qa_defect([dur_finding])
    assert defect == RegenerationDefectType.DURATION_MISMATCH
    assert stage == "SCRIPT"

    # 3. Grounding defect
    ground_finding = QAFinding(
        finding_id="f3",
        check_id="fact_check",
        category="grounding",
        severity=QASeverity.CRITICAL,
        message="Unverified fact or claim not in research evidence",
    )
    defect, reason, instruction, stage = classify_qa_defect([ground_finding])
    assert defect == RegenerationDefectType.GROUNDING_DEFECT
    assert stage == "SCRIPT"


def test_targeted_regeneration_loop_and_needs_review(tmp_path):
    """Verify that repeated QA failures trigger targeted regeneration and transition to NEEDS_REVIEW when limit reached."""
    from autopilot.core.config import Config
    from autopilot.db.manager import DBManager

    db_path = tmp_path / "test_regen.db"
    db = DBManager(db_path)
    db.init_schema()
    cfg = Config(artifacts_dir=tmp_path, db_path=db_path)
    orch = PipelineOrchestrator(config=cfg, db=db)

    failing_report = QAReport(
        report_id="rep-fail",
        job_id="job-regen-test-01",
        content_id="job-regen-test-01",
        status=QAStatus.BLOCK,
        publish_allowed=False,
        receipt=PublishReceipt(
            receipt_id="rec-fail",
            job_id="job-regen-test-01",
            content_id="job-regen-test-01",
            status=QAStatus.BLOCK,
            publish_allowed=False,
            blocking_findings=[
                QAFinding(
                    finding_id="f1",
                    check_id="hook_engagement",
                    category="editorial",
                    severity=QASeverity.CRITICAL,
                    message="Hook is weak or missing curiosity",
                )
            ],
        ),
    )

    with patch("autopilot.core.qa_engine.QAEngine.evaluate", return_value=failing_report):
        with pytest.raises(PipelineError) as exc_info:
            orch.run_pipeline(
                job_id="job-regen-test-01",
                topic="Quantum Entanglement",
                tts_provider="none",
                asset_provider="local",
                production_engine="ffmpeg",
                max_regeneration_attempts=2,
            )

        assert "NEEDS_REVIEW" in exc_info.value.message or exc_info.value.category == "BLOCKED"
        # Check that job status in DB is recorded as NEEDS_REVIEW
        job_rec = db.get_job("job-regen-test-01")
        assert job_rec["status"] == WorkflowState.NEEDS_REVIEW.value
