import pytest
from pathlib import Path
from unittest.mock import patch

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
    TrendSignal,
    TopicCandidate,
    IdeaProposal,
    DecisionAction,
    BatchManifest,
    BatchItem,
    ContentPerformance,
)
from autopilot.core.channel import ChannelManager
from autopilot.core.feedback import StrategyManager
from autopilot.core.ideation import IdeationEngine
from autopilot.core.autonomy import AutonomyEngine
from autopilot.core.batch import BatchProcessor
from autopilot.db.manager import DatabaseManager


def build_channel(channel_id: str, niche_name: str, allowed_cats: list[str], max_jobs: int = 5) -> ChannelProfile:
    return ChannelProfile(
        channel_id=channel_id,
        channel_name=f"Channel {channel_id}",
        status=ChannelStatus.ACTIVE,
        version="1",
        niche=NicheConfig(
            niche_name=niche_name,
            description=f"{niche_name} niche channel",
            allowed_categories=allowed_cats,
            excluded_categories=[],
            terminology=[f"{niche_name}_term"],
            ideation_weighting={niche_name: 1.5},
        ),
        language="en",
        locale="en-US",
        persona=PersonaConfig(
            tone="analytical" if niche_name == "technology" else "narrative",
            vocabulary_level="advanced",
            narrator_personality="engaging narrator",
            cta_style=f"Subscribe to {channel_id}",
            hook_style="mystery hook",
            pacing_preference="fast",
        ),
        voice=VoiceProfile(
            provider="mock",
            voice_id=f"voice-{channel_id}",
            language="en",
            speaking_rate=1.0,
            pitch=1.0,
        ),
        visual=VisualBrandProfile(
            font_family="Roboto-Bold",
            caption_style="yellow_boxed",
            theme_color_primary="#00ff00" if niche_name == "technology" else "#ffaa00",
            theme_color_secondary="#000000",
            visual_motif=f"{niche_name} visual motif",
            watermark_enabled=False,
        ),
        content_formats=["short_vertical"],
        target_platforms=["youtube"],
        posting_policy=PostingPolicy(timezone="UTC", max_daily_posts=max_jobs),
        autonomy_policy=AutonomyPolicy(
            autonomy_level=2,
            max_ideas_per_cycle=5,
            max_auto_queue_per_cycle=2,
            max_jobs_per_day=max_jobs,
            topic_cooldown_days=7,
            require_evidence=False,
            allowed_profiles=["short_vertical"],
        ),
        analytics_config=AnalyticsConfig(tracking_enabled=True),
        monetization=MonetizationMetadata(),
        active_strategy_version=f"strat-{channel_id}",
    )


def test_channel_settings_isolation(tmp_path: Path):
    db = DatabaseManager(tmp_path / "test.db")
    db.init_schema()
    mgr = ChannelManager(db)

    ch_a = build_channel("chan-a", "technology", ["technology", "ai"])
    ch_b = build_channel("chan-b", "history", ["history", "ancient"])
    mgr.save_profile(ch_a)
    mgr.save_profile(ch_b)

    # Mutate Channel A
    ch_a.persona.tone = "dramatic"
    ch_a.voice.voice_id = "voice-a-new"
    mgr.save_profile(ch_a)

    # Assert Channel B remains unchanged
    fresh_b = mgr.get_channel("chan-b")
    assert fresh_b.persona.tone == "narrative"
    assert fresh_b.voice.voice_id == "voice-chan-b"
    assert fresh_b.niche.niche_name == "history"


def test_strategy_isolation(tmp_path: Path):
    db = DatabaseManager(tmp_path / "test.db")
    db.init_schema()
    mgr = ChannelManager(db)
    strat_mgr = StrategyManager(db)

    ch_a = build_channel("chan-tech", "technology", ["technology"])
    ch_b = build_channel("chan-hist", "history", ["history"])
    ch_a.active_strategy_version = "strat-tech-v1"
    ch_b.active_strategy_version = "strat-hist-v1"
    mgr.save_profile(ch_a)
    mgr.save_profile(ch_b)

    strat_a = strat_mgr.get_active_strategy(channel_id="chan-tech")
    strat_b = strat_mgr.get_active_strategy(channel_id="chan-hist")

    assert strat_a is not None
    assert strat_b is not None
    assert strat_a.version_id == "strat-tech-v1"
    assert strat_b.version_id == "strat-hist-v1"


def test_quota_isolation(tmp_path: Path):
    db = DatabaseManager(tmp_path / "test.db")
    db.init_schema()
    mgr = ChannelManager(db)

    # Channel A has max 2 jobs/day, Channel B has max 5 jobs/day
    ch_a = build_channel("chan-a", "technology", ["technology"], max_jobs=2)
    ch_b = build_channel("chan-b", "history", ["history"], max_jobs=5)
    mgr.save_profile(ch_a)
    mgr.save_profile(ch_b)

    # Channel A consumes 2 jobs
    assert mgr.check_channel_quota("chan-a") is True
    mgr.record_quota_consumption("chan-a", jobs_count=2)

    # Channel A should now be exhausted
    assert mgr.check_channel_quota("chan-a") is False

    # Channel B should still have full quota
    assert mgr.check_channel_quota("chan-b") is True
    usage_b = mgr.get_quota_usage("chan-b")
    assert usage_b["jobs_queued"] == 0


def test_channel_aware_ideation_niche_filtering(tmp_path: Path):
    db = DatabaseManager(tmp_path / "test.db")
    db.init_schema()
    mgr = ChannelManager(db)
    ideation = IdeationEngine(db)

    ch_tech = build_channel("tech-chan", "technology", ["technology", "ai"])
    mgr.save_profile(ch_tech)

    signals = [
        TrendSignal(
            signal_id="sig-1",
            topic="Open Source LLM Release",
            source="mock",
            category="technology",
            freshness_score=0.9,
            relevance_score=0.9,
        ),
        TrendSignal(
            signal_id="sig-2",
            topic="Ancient Roman Aqueduct Discovered",
            source="mock",
            category="history",
            freshness_score=0.9,
            relevance_score=0.9,
        ),
    ]

    candidates = ideation.generate_candidates(signals, run_id="run-test", channel_profile=ch_tech)
    # The history topic should be filtered out by niche allowed_categories
    assert len(candidates) == 1
    assert "Open Source LLM Release" in candidates[0].proposed_topic
    assert candidates[0].channel_id == "tech-chan"
    assert "Engineering" in candidates[0].angle or "Paradigm" in candidates[0].angle


def test_cross_channel_batch_enqueue(tmp_path: Path):
    db = DatabaseManager(tmp_path / "test.db")
    db.init_schema()
    mgr = ChannelManager(db)
    bp = BatchProcessor(db)

    ch_a = build_channel("chan-1", "technology", ["technology"])
    ch_b = build_channel("chan-2", "history", ["history"])
    mgr.save_profile(ch_a)
    mgr.save_profile(ch_b)

    manifest = BatchManifest(
        manifest_id="manifest-multi-chan",
        name="Multi-Channel Batch",
        items=[
            BatchItem(topic="Future of Quantum AI", channel_id="chan-1", priority=2),
            BatchItem(topic="Future of Quantum AI", channel_id="chan-2", priority=2), # Same topic, different channel
        ]
    )

    result = bp.submit_manifest(manifest)
    assert result.enqueued == 2
    assert result.duplicates == 0

    items = db.list_queue_items()
    assert len(items) == 2
    channels_in_queue = {it["channel_id"] for it in items}
    assert channels_in_queue == {"chan-1", "chan-2"}


def test_channel_analytics_isolation(tmp_path: Path):
    db = DatabaseManager(tmp_path / "test.db")
    db.init_schema()
    mgr = ChannelManager(db)

    ch_a = build_channel("c-tech", "technology", ["technology"])
    ch_b = build_channel("c-sci", "science", ["science"])
    mgr.save_profile(ch_a)
    mgr.save_profile(ch_b)

    # Add jobs and mock performance
    db.create_job("job-1", channel_id="c-tech", topic="Tech Topic 1")
    db.create_job("job-2", channel_id="c-sci", topic="Science Topic 1")

    # Record performance records
    from autopilot.core.contracts import AnalyticsSnapshot, MetricObservation
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    snap1 = AnalyticsSnapshot(
        snapshot_id="snap-1",
        job_id="job-1",
        platform="youtube",
        remote_id="yt-1",
        window="lifetime",
        observed_at=now,
        provider="mock",
        is_synthetic=False,
        metrics={
            "views": MetricObservation(
                observation_id="m-1",
                snapshot_id="snap-1",
                metric_name="views",
                metric_value=1000.0,
                observed_at=now,
            )
        },
    )
    db.record_analytics_snapshot(snap1)

    snap2 = AnalyticsSnapshot(
        snapshot_id="snap-2",
        job_id="job-2",
        platform="youtube",
        remote_id="yt-2",
        window="lifetime",
        observed_at=now,
        provider="mock",
        is_synthetic=False,
        metrics={
            "views": MetricObservation(
                observation_id="m-2",
                snapshot_id="snap-2",
                metric_name="views",
                metric_value=500.0,
                observed_at=now,
            )
        },
    )
    db.record_analytics_snapshot(snap2)

    # Compare channels
    comp = mgr.compare_channels()
    assert len(comp) == 2
    tech_comp = next(c for c in comp if c["channel_id"] == "c-tech")
    sci_comp = next(c for c in comp if c["channel_id"] == "c-sci")

    assert tech_comp["total_views"] == 1000
    assert sci_comp["total_views"] == 500
    assert tech_comp["content_count"] == 1
    assert sci_comp["content_count"] == 1
