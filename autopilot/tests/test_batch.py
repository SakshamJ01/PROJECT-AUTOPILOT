"""Tests for Batch Manifest Processor — Milestone 7.
Verifies JSON/YAML parsing, schema validation, dry-run mode, and duplicate submission prevention.
"""
import json
import pytest
from pathlib import Path

from autopilot.db.manager import DBManager
from autopilot.core.batch import BatchProcessor
from autopilot.core.contracts import BatchManifest, BatchItem


def test_batch_processor_valid_json_manifest(tmp_path):
    """Verifies valid manifest is parsed and enqueued correctly."""
    manifest_data = {
        "profile": "short_vertical",
        "priority": "normal",
        "items": [
            {"topic": "The Great Wall of China", "priority": "high"},
            {"topic": "The Roman Colosseum", "priority": "normal"},
            {"topic": "The Pyramids of Giza", "priority": "low"},
        ],
    }
    manifest_file = tmp_path / "batch.json"
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")

    db = DBManager(tmp_path / "batch.db")
    db.init_schema()

    processor = BatchProcessor(db=db)
    manifest = processor.parse_manifest_file(manifest_file)
    res = processor.submit_manifest(manifest)

    assert res.total_items == 3
    assert res.submitted_count == 3
    assert res.skipped_duplicate_count == 0
    assert len(res.queued_ids) == 3
    assert len(res.errors) == 0

    # Verify priority in DB
    high_item = db.get_queue_item(res.queued_ids[0])
    assert high_item["priority"] == 3


def test_batch_processor_malformed_manifest_rejected(tmp_path):
    """Verifies invalid items (empty topic or malformed timestamp) reject the batch without partial enqueuing."""
    manifest_data = {
        "profile": "short_vertical",
        "items": [
            {"topic": ""},  # Empty topic
        ],
    }
    manifest_file = tmp_path / "invalid_batch.json"
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")

    db = DBManager(tmp_path / "batch_inv.db")
    db.init_schema()

    processor = BatchProcessor(db=db)
    with pytest.raises(ValueError, match="Invalid batch manifest schema"):
        processor.parse_manifest_file(manifest_file)

    # Also verify semantic rejection in submit_manifest for invalid scheduled_at
    manifest_data2 = {
        "profile": "short_vertical",
        "items": [
            {"topic": "Valid Topic", "scheduled_at": "not-a-timestamp"},
        ],
    }
    manifest_file2 = tmp_path / "invalid_ts_batch.json"
    manifest_file2.write_text(json.dumps(manifest_data2), encoding="utf-8")
    m2 = processor.parse_manifest_file(manifest_file2)
    res2 = processor.submit_manifest(m2)
    assert res2.submitted_count == 0
    assert len(res2.errors) == 1
    assert db.list_queue_items() == []


def test_batch_processor_duplicate_prevention_unless_force(tmp_path):
    """Verifies re-submitting the same manifest skips existing items unless force=True."""
    manifest_data = {
        "profile": "short_vertical",
        "items": [
            {"topic": "Deep Sea Exploration"},
        ],
    }
    manifest_file = tmp_path / "dupe_batch.json"
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")

    db = DBManager(tmp_path / "batch_dupe.db")
    db.init_schema()

    processor = BatchProcessor(db=db)
    manifest = processor.parse_manifest_file(manifest_file)

    # First submission
    res1 = processor.submit_manifest(manifest)
    assert res1.submitted_count == 1
    assert res1.skipped_duplicate_count == 0

    # Second submission without force
    res2 = processor.submit_manifest(manifest, force=False)
    assert res2.submitted_count == 0
    assert res2.skipped_duplicate_count == 1

    # Third submission with force=True
    res3 = processor.submit_manifest(manifest, force=True)
    assert res3.submitted_count == 1


def test_batch_processor_dry_run_mode(tmp_path):
    """Verifies dry run validates input without writing to database."""
    manifest_data = {
        "profile": "short_vertical",
        "items": [
            {"topic": "Dry Run Topic 1"},
            {"topic": "Dry Run Topic 2"},
        ],
    }
    manifest_file = tmp_path / "dry_batch.json"
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")

    db = DBManager(tmp_path / "batch_dry.db")
    db.init_schema()

    processor = BatchProcessor(db=db)
    manifest = processor.parse_manifest_file(manifest_file)
    res = processor.submit_manifest(manifest, dry_run=True)

    assert res.total_items == 2
    assert res.submitted_count == 2
    assert db.list_queue_items() == []  # DB remains untouched
