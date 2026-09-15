"""Unit Tests for M5 QA Engine & Quality Gates.
Completely offline, deterministic, zero external API spend.
"""
import json
import pytest
from pathlib import Path

from autopilot.core.config import CONFIG, Config
from autopilot.core.contracts import (
    QAReport, QACheck, QAFinding, QAMetric, QAThreshold,
    QAArtifactReference, PublishReceipt, QAStatus, QASeverity,
    ContentPackage, ContentItem, ScriptDocument, ScriptScene,
    PublicationMetadata, ProvenanceRecord, RenderPlan,
    AssetArtifact, AssetLicense, AssetProvenance
)
from autopilot.core.qa_engine import QAEngine, export_qa_artifacts
from autopilot.db.manager import DBManager


# ------------------------------------------------------------------
# Model & Contract Unit Tests
# ------------------------------------------------------------------
def test_qa_status_and_severity_enums():
    assert QAStatus.PASS == "PASS"
    assert QAStatus.WARN == "WARN"
    assert QAStatus.BLOCK == "BLOCK"

    assert QASeverity.INFO == "INFO"
    assert QASeverity.LOW == "LOW"
    assert QASeverity.MEDIUM == "MEDIUM"
    assert QASeverity.HIGH == "HIGH"
    assert QASeverity.CRITICAL == "CRITICAL"


def test_qa_finding_and_check_creation():
    finding = QAFinding(
        finding_id="f-001",
        check_id="c-001",
        category="video_properties",
        severity=QASeverity.HIGH,
        status=QAStatus.BLOCK,
        message="Resolution wrong",
        measured_value="720x1280",
        expected_value="1080x1920",
    )
    assert finding.status == QAStatus.BLOCK
    assert finding.severity == QASeverity.HIGH

    check = QACheck(
        check_id="c-001",
        category="video_properties",
        status=QAStatus.BLOCK,
        severity=QASeverity.HIGH,
        message="Failed resolution check",
        findings=[finding],
    )
    assert len(check.findings) == 1
    assert check.status == QAStatus.BLOCK


def test_publish_receipt_invariant():
    receipt_pass = PublishReceipt(
        receipt_id="r-pass",
        job_id="j-1",
        content_id="c-1",
        status=QAStatus.PASS,
        publish_allowed=True,
    )
    assert receipt_pass.publish_allowed is True

    receipt_block = PublishReceipt(
        receipt_id="r-block",
        job_id="j-2",
        content_id="c-2",
        status=QAStatus.BLOCK,
        publish_allowed=False,
    )
    assert receipt_block.publish_allowed is False


def test_qa_report_round_trip_json():
    rep = QAReport(
        report_id="rep-001",
        job_id="job-001",
        content_id="content-001",
        qa_version="v1.0.0",
        status=QAStatus.PASS,
        publish_allowed=True,
        checks=[
            QACheck(check_id="c1", category="integrity", status=QAStatus.PASS, severity=QASeverity.INFO, message="All good")
        ],
        metrics=[
            QAMetric(metric_id="m1", name="video_width", category="video", value_numeric=1080.0, unit="px")
        ],
        receipt=PublishReceipt(
            receipt_id="rcpt-001",
            job_id="job-001",
            content_id="content-001",
            status=QAStatus.PASS,
            publish_allowed=True,
        ),
    )
    serialized = rep.model_dump_json()
    deserialized = QAReport.model_validate_json(serialized)
    assert deserialized.report_id == "rep-001"
    assert deserialized.status == QAStatus.PASS
    assert deserialized.publish_allowed is True
    assert len(deserialized.checks) == 1
    assert len(deserialized.metrics) == 1


# ------------------------------------------------------------------
# Dedicated QA Checks Unit Tests
# ------------------------------------------------------------------
def test_check_container_integrity_missing_file(tmp_path):
    engine = QAEngine(CONFIG)
    non_existent = tmp_path / "missing.mp4"
    check = engine.check_container_integrity(non_existent)
    assert check.status == QAStatus.BLOCK
    assert any(f.status == QAStatus.BLOCK for f in check.findings)


def test_check_container_integrity_zero_bytes(tmp_path):
    engine = QAEngine(CONFIG)
    empty_file = tmp_path / "empty.mp4"
    empty_file.write_bytes(b"")
    check = engine.check_container_integrity(empty_file)
    assert check.status == QAStatus.BLOCK
    assert any("zero bytes" in f.message.lower() for f in check.findings)


def test_check_video_properties_validation():
    engine = QAEngine(CONFIG)
    # Valid 1080x1920 stream
    v_valid = {
        "codec_name": "h264",
        "width": 1080,
        "height": 1920,
        "pix_fmt": "yuv420p",
        "r_frame_rate": "25/1",
    }
    check, metrics = engine.check_video_properties(v_valid)
    assert check.status == QAStatus.PASS
    assert len(check.findings) == 0
    assert any(m.name == "video_width" and m.value_numeric == 1080.0 for m in metrics)

    # Invalid dimensions (e.g. horizontal 1920x1080)
    v_invalid = {
        "codec_name": "h264",
        "width": 1920,
        "height": 1080,
        "pix_fmt": "yuv420p",
        "r_frame_rate": "25/1",
    }
    check_bad, _ = engine.check_video_properties(v_invalid)
    assert check_bad.status == QAStatus.BLOCK
    assert any("resolution mismatch" in f.message.lower() for f in check_bad.findings)


def test_check_audio_properties_validation():
    engine = QAEngine(CONFIG)
    a_valid = {
        "codec_name": "aac",
        "sample_rate": "44100",
        "channels": 2,
    }
    check, metrics = engine.check_audio_properties(a_valid, narration_expected=True)
    assert check.status == QAStatus.PASS
    assert any(m.name == "audio_codec" and m.value_text == "aac" for m in metrics)

    # Missing audio when narration is expected
    check_none, _ = engine.check_audio_properties(None, narration_expected=True)
    assert check_none.status == QAStatus.BLOCK


def test_check_duration_timeline():
    engine = QAEngine(CONFIG)
    plan = RenderPlan(
        plan_id="p1", content_id="c1", job_id="j1",
        scenes=[{"scene_id": "s1", "duration_sec": 5.0}, {"scene_id": "s2", "duration_sec": 5.0}]
    )
    # Matching duration
    chk_ok, _ = engine.check_duration_timeline(10.0, plan=plan, package=None)
    assert chk_ok.status == QAStatus.PASS

    # Drastic mismatch (measured 25s, expected 10s)
    chk_drift, _ = engine.check_duration_timeline(25.0, plan=plan, package=None)
    assert chk_drift.status == QAStatus.BLOCK
    assert any("duration drift" in f.message.lower() for f in chk_drift.findings)


def test_check_scene_coverage():
    engine = QAEngine(CONFIG)
    script = ScriptDocument(
        content_id="c1", topic="Topic",
        scenes=[
            ScriptScene(scene_id="scene-1", order=1, narration="First scene narration", visual_intent="v1"),
            ScriptScene(scene_id="scene-2", order=2, narration="Second scene narration", visual_intent="v2"),
        ]
    )
    pkg = ContentPackage(
        content_item=ContentItem(content_id="c1", topic="Topic"),
        script=script
    )
    # Complete coverage
    plan_complete = RenderPlan(
        plan_id="p1", content_id="c1", job_id="j1",
        scenes=[{"scene_id": "scene-1"}, {"scene_id": "scene-2"}]
    )
    chk_comp = engine.check_scene_coverage(plan_complete, pkg)
    assert chk_comp.status == QAStatus.PASS

    # Incomplete coverage (scene-2 missing)
    plan_incomplete = RenderPlan(
        plan_id="p2", content_id="c1", job_id="j1",
        scenes=[{"scene_id": "scene-1"}]
    )
    chk_incomp = engine.check_scene_coverage(plan_incomplete, pkg)
    assert chk_incomp.status == QAStatus.BLOCK
    assert any("missing from renderplan" in f.message.lower() for f in chk_incomp.findings)


def test_check_captions_validation():
    engine = QAEngine(CONFIG)
    # Valid captions
    plan_valid = RenderPlan(
        plan_id="p1", content_id="c1", job_id="j1",
        scenes=[{"scene_id": "s1", "duration_sec": 4.0, "caption_text": "Short clean caption"}]
    )
    chk = engine.check_captions(None, plan_valid)
    assert chk.status == QAStatus.PASS

    # Line too long (> 40 chars)
    long_line = "This is an extremely long subtitle line that easily exceeds forty characters without splitting"
    plan_long = RenderPlan(
        plan_id="p2", content_id="c1", job_id="j1",
        scenes=[{"scene_id": "s1", "duration_sec": 4.0, "caption_text": long_line}]
    )
    chk_long = engine.check_captions(None, plan_long)
    assert chk_long.status == QAStatus.WARN
    assert any("exceeds max characters" in f.message.lower() for f in chk_long.findings)


def test_check_provenance_and_rights(tmp_path):
    engine = QAEngine(CONFIG)

    # Valid verified asset
    sample_file = tmp_path / "valid_image.png"
    sample_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR...")
    import hashlib
    file_hash = hashlib.sha256(sample_file.read_bytes()).hexdigest()

    art_valid = AssetArtifact(
        artifact_id="art-valid",
        source_path=str(sample_file),
        normalized_path=str(sample_file),
        checksum_sha256=file_hash,
        provenance=AssetProvenance(provider="local", source_id="src-1"),
        license=AssetLicense(license_name="CC0", rights_status="VERIFIED"),
    )
    chk_prov = engine.check_provenance([art_valid])
    assert chk_prov.status == QAStatus.PASS

    chk_rights = engine.check_rights_gate([art_valid])
    assert chk_rights.status == QAStatus.PASS

    # Unknown license rights gate failure
    art_unknown = AssetArtifact(
        artifact_id="art-unknown",
        source_path=str(sample_file),
        license=AssetLicense(license_name="UNKNOWN", rights_status="UNKNOWN"),
    )
    chk_unknown = engine.check_rights_gate([art_unknown])
    assert chk_unknown.status == QAStatus.BLOCK
    assert any("unknown rights" in f.message.lower() for f in chk_unknown.findings)

    # Checksum mismatch
    art_bad_hash = AssetArtifact(
        artifact_id="art-bad-hash",
        source_path=str(sample_file),
        checksum_sha256="0000000000000000000000000000000000000000000000000000000000000000",
        provenance=AssetProvenance(provider="local"),
    )
    chk_bad_prov = engine.check_provenance([art_bad_hash])
    assert chk_bad_prov.status == QAStatus.BLOCK
    assert any("checksum mismatch" in f.message.lower() for f in chk_bad_prov.findings)


def test_export_qa_artifacts(tmp_path):
    report = QAReport(
        report_id="rep-art-test",
        job_id="job-art-test",
        content_id="content-art-test",
        status=QAStatus.PASS,
        publish_allowed=True,
        checks=[QACheck(check_id="c1", category="general", status=QAStatus.PASS, message="OK")],
        receipt=PublishReceipt(
            receipt_id="r-test",
            job_id="job-art-test",
            content_id="content-art-test",
            status=QAStatus.PASS,
            publish_allowed=True,
        ),
    )
    paths = export_qa_artifacts(report, tmp_path)
    assert Path(paths["quality_report"]).exists()
    assert Path(paths["receipt"]).exists()
    assert Path(paths["findings"]).exists()
    assert Path(paths["metrics"]).exists()

    receipt_data = json.loads(Path(paths["receipt"]).read_text(encoding="utf-8"))
    assert receipt_data["status"] == "PASS"
    assert receipt_data["publish_allowed"] is True
