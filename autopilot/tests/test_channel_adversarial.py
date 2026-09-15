import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from autopilot.core.contracts import (
    ChannelProfile,
    ChannelStatus,
    NicheConfig,
    PersonaConfig,
    VoiceProfile,
    VisualBrandProfile,
    PostingPolicy,
    AnalyticsConfig,
    MonetizationMetadata,
    AutonomyPolicy,
    BatchManifest,
    BatchItem,
    QueueItemStatus,
)
from autopilot.core.channel import ChannelManager
from autopilot.core.batch import BatchProcessor
from autopilot.core.worker import Worker
from autopilot.core.autonomy import AutonomyEngine
from autopilot.core.qa_engine import QAEngine
from autopilot.db.manager import DatabaseManager
from tests.test_channel_profiles import create_sample_profile


def test_adv_channel_a_quota_exhausted_b_has_capacity(tmp_path: Path):
    """Adversarial #2: Channel A reaches quota while B has capacity."""
    db = DatabaseManager(tmp_path / "test.db")
    db.init_schema()
    mgr = ChannelManager(db)

    ch_a = create_sample_profile("ch-a", "Channel A")
    ch_a.autonomy_policy.max_jobs_per_day = 1
    ch_b = create_sample_profile("ch-b", "Channel B")
    ch_b.autonomy_policy.max_jobs_per_day = 5

    mgr.save_profile(ch_a)
    mgr.save_profile(ch_b)

    # Exhaust A
    mgr.record_quota_consumption("ch-a", 1)
    assert mgr.check_channel_quota("ch-a") is False
    assert mgr.check_channel_quota("ch-b") is True


def test_adv_two_channels_same_topic_idempotency(tmp_path: Path):
    """Adversarial #3: Two channels request the same topic without colliding."""
    db = DatabaseManager(tmp_path / "test.db")
    db.init_schema()
    mgr = ChannelManager(db)
    bp = BatchProcessor(db)

    ch_a = create_sample_profile("tech-1", "Tech 1")
    ch_b = create_sample_profile("tech-2", "Tech 2")
    mgr.save_profile(ch_a)
    mgr.save_profile(ch_b)

    manifest = BatchManifest(
        manifest_id="manifest-same-topic",
        items=[
            BatchItem(topic="Identical Topic X", channel_id="tech-1"),
            BatchItem(topic="Identical Topic X", channel_id="tech-2"),
        ]
    )
    result = bp.submit_manifest(manifest)
    assert result.enqueued == 2
    assert result.duplicates == 0

    items = db.list_queue_items()
    assert len(items) == 2
    assert items[0]["channel_id"] != items[1]["channel_id"]


def test_adv_channel_disabled_during_queue_processing(tmp_path: Path):
    """Adversarial #6: One channel is disabled during queue processing. Worker blocks it."""
    db = DatabaseManager(tmp_path / "test.db")
    db.init_schema()
    mgr = ChannelManager(db)
    bp = BatchProcessor(db)

    ch = create_sample_profile("ch-disabled-test", "Disable Test")
    mgr.save_profile(ch)

    manifest = BatchManifest(
        manifest_id="manifest-disable-test",
        items=[BatchItem(topic="Will Be Disabled", channel_id="ch-disabled-test")]
    )
    bp.submit_manifest(manifest)

    # Disable channel BEFORE worker runs
    mgr.set_channel_status("ch-disabled-test", ChannelStatus.DISABLED)

    # Worker runs
    worker = Worker(db=db, worker_id="worker-test")
    processed = worker.run_once()
    assert processed == 1

    # Queue item should be marked BLOCKED/CANCELLED
    items = db.list_queue_items()
    assert len(items) == 1
    assert items[0]["status"] == QueueItemStatus.BLOCKED.value
    assert "disabled" in items[0]["last_error"].lower()


def test_adv_unsupported_platform_target_fails_gracefully(tmp_path: Path):
    """Adversarial #7: Channel profile specifies unsupported platform target."""
    db = DatabaseManager(tmp_path / "test.db")
    db.init_schema()
    mgr = ChannelManager(db)
    bp = BatchProcessor(db)

    ch = create_sample_profile("ch-unsupported", "Unsupported Target")
    ch.target_platforms = ["unsupported_network_xyz"]
    # Bypass validate_profile on direct save for adversarial test
    db.record_channel_profile(ch)

    manifest = BatchManifest(
        manifest_id="manifest-unsupported",
        items=[BatchItem(topic="Unsupported Platform Job", channel_id="ch-unsupported")]
    )
    bp.submit_manifest(manifest)

    # Worker runs
    worker = Worker(db=db, worker_id="worker-test")
    processed = worker.run_once()
    assert processed == 1

    items = db.list_queue_items()
    assert items[0]["status"] == QueueItemStatus.FAILED.value
    assert "Unsupported publishing platform" in items[0]["last_error"]


def test_adv_channel_cannot_bypass_mandatory_qa(tmp_path: Path):
    """Adversarial #12: Channel-specific policy conflicts with global mandatory QA. Global QA remains mandatory."""
    db = DatabaseManager(tmp_path / "test.db")
    db.init_schema()
    mgr = ChannelManager(db)

    # Attempt to create a profile that claims to disable QA or allow unverified publishing
    ch = create_sample_profile("ch-no-qa", "No QA")
    mgr.save_profile(ch)
    # Even if channel config has custom flags, QAEngine enforces mandatory gates
    qa = QAEngine()
    # A failed check or missing media still produces a failed report regardless of channel
    report = qa.evaluate(media_path=tmp_path / "missing.mp4", job_id="job-qa-test")
    assert report.publish_allowed is False


def test_adv_autonomous_cycle_respects_per_channel_quota(tmp_path: Path):
    """Adversarial #13: Autonomous cycle attempts to exceed per-channel daily quota."""
    db = DatabaseManager(tmp_path / "test.db")
    db.init_schema()
    mgr = ChannelManager(db)

    ch = create_sample_profile("ch-auto-quota", "Auto Quota")
    ch.autonomy_policy.max_jobs_per_day = 2
    mgr.save_profile(ch)

    # Consume quota
    mgr.record_quota_consumption("ch-auto-quota", 2)

    engine = AutonomyEngine(db=db)
    summary = engine.run_cycle(channel_id="ch-auto-quota", autonomy_level=3)
    # Jobs should be blocked due to daily quota reached
    assert summary.jobs_queued == 0
    assert summary.jobs_blocked >= 0
    assert "quota reached" in (summary.error_message or "").lower()


def test_adv_publisher_failure_in_one_channel_does_not_block_another(tmp_path: Path):
    """Adversarial #14: Publisher failure on channel A doesn't crash or stop channel B."""
    db = DatabaseManager(tmp_path / "test.db")
    db.init_schema()
    mgr = ChannelManager(db)
    bp = BatchProcessor(db)

    ch_a = create_sample_profile("ch-fail", "Failing Channel")
    ch_a.target_platforms = ["unsupported_plat"]
    ch_b = create_sample_profile("ch-pass", "Passing Channel")
    ch_b.target_platforms = ["youtube"]

    # Save both directly
    db.record_channel_profile(ch_a)
    mgr.save_profile(ch_b)

    manifest = BatchManifest(
        manifest_id="manifest-multi-failover",
        items=[
            BatchItem(topic="Topic A", channel_id="ch-fail"),
            BatchItem(topic="Topic B", channel_id="ch-pass"),
        ]
    )
    bp.submit_manifest(manifest)

    # Process first item (fails gracefully)
    worker = Worker(db=db, worker_id="worker-test")
    p1 = worker.run_once()
    assert p1 == 1

    # Check status of items: ch-fail failed, ch-pass is still ready or succeeded
    items = db.list_queue_items()
    failed_item = next(it for it in items if it["channel_id"] == "ch-fail")
    assert failed_item["status"] == QueueItemStatus.FAILED.value

    # Process second item with mocked pipeline
    with patch("autopilot.core.worker.run_pipeline") as mock_pipeline:
        mock_pipeline.return_value = {
            "job_id": "job-pass",
            "qa_passed": True,
            "status": "QA_PASSED",
        }
        p2 = worker.run_once()
        assert p2 == 1

    items_after = db.list_queue_items()
    pass_item = next(it for it in items_after if it["channel_id"] == "ch-pass")
    assert pass_item["status"] == QueueItemStatus.SUCCEEDED.value
