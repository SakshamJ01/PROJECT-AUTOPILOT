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
    AutonomyLevel,
    TrendSignal,
    TopicCandidate,
    IdeaProposal,
    DecisionAction,
    BatchManifest,
    BatchItem,
    ContentPerformance,
    AnalyticsSnapshot,
    MetricObservation,
)
from autopilot.core.channel import ChannelManager
from autopilot.core.autonomy import AutonomyEngine
from autopilot.core.worker import Worker
from autopilot.core.analytics import AnalyticsEngine
from autopilot.db.manager import DatabaseManager


def create_3_channels(mgr: ChannelManager) -> tuple[ChannelProfile, ChannelProfile, ChannelProfile]:
    # Channel A: Technology
    ch_tech = ChannelProfile(
        channel_id="tech-pulse",
        channel_name="Tech Pulse",
        status=ChannelStatus.ACTIVE,
        version="1",
        niche=NicheConfig(
            niche_name="technology",
            description="Deep tech breakthroughs and architecture",
            allowed_categories=["technology", "ai", "hardware"],
            excluded_categories=["history", "biology"],
            terminology=["low-level", "latency", "benchmark", "compiler"],
            ideation_weighting={"technology": 1.5, "ai": 1.8},
        ),
        language="en",
        locale="en-US",
        persona=PersonaConfig(
            tone="analytical",
            vocabulary_level="advanced",
            narrator_personality="authoritative engineer",
            cta_style="Subscribe for systems engineering breakdowns",
            hook_style="technical mystery",
            pacing_preference="fast",
        ),
        voice=VoiceProfile(
            provider="mock",
            voice_id="tech-voice-alpha",
            language="en",
            speaking_rate=1.1,
        ),
        visual=VisualBrandProfile(
            font_family="Roboto-Bold",
            caption_style="cyan_highlight",
            theme_color_primary="#00e5ff",
            theme_color_secondary="#111111",
            visual_motif="matrix grid circuitry",
        ),
        content_formats=["short_vertical"],
        target_platforms=["youtube"],
        posting_policy=PostingPolicy(timezone="UTC", max_daily_posts=3),
        autonomy_policy=AutonomyPolicy(
            autonomy_level=3, # Guarded auto-queue
            max_ideas_per_cycle=3,
            max_auto_queue_per_cycle=2,
            max_jobs_per_day=3,
            topic_cooldown_days=7,
            require_evidence=False,
            allowed_profiles=["short_vertical"],
        ),
        analytics_config=AnalyticsConfig(tracking_enabled=True),
        monetization=MonetizationMetadata(),
        active_strategy_version="strat-tech-v1",
    )

    # Channel B: Science
    ch_sci = ChannelProfile(
        channel_id="cosmos-lab",
        channel_name="Cosmos Lab",
        status=ChannelStatus.ACTIVE,
        version="1",
        niche=NicheConfig(
            niche_name="science",
            description="Astrophysics and quantum phenomenon",
            allowed_categories=["science", "physics", "astronomy"],
            excluded_categories=["technology", "history"],
            terminology=["singularity", "quantum state", "event horizon"],
            ideation_weighting={"science": 1.6, "physics": 1.9},
        ),
        language="en",
        locale="en-US",
        persona=PersonaConfig(
            tone="wonder",
            vocabulary_level="accessible",
            narrator_personality="passionate scientist",
            cta_style="Explore the mysteries of the universe with us",
            hook_style="mind-bending paradox",
            pacing_preference="steady",
        ),
        voice=VoiceProfile(
            provider="mock",
            voice_id="science-voice-beta",
            language="en",
            speaking_rate=1.0,
        ),
        visual=VisualBrandProfile(
            font_family="Outfit-Bold",
            caption_style="nebula_glow",
            theme_color_primary="#9c27b0",
            theme_color_secondary="#000000",
            visual_motif="deep space nebula stars",
        ),
        content_formats=["short_vertical"],
        target_platforms=["youtube"],
        posting_policy=PostingPolicy(timezone="UTC", max_daily_posts=3),
        autonomy_policy=AutonomyPolicy(
            autonomy_level=3, # Guarded auto-queue
            max_ideas_per_cycle=3,
            max_auto_queue_per_cycle=2,
            max_jobs_per_day=3,
            topic_cooldown_days=7,
            require_evidence=False,
            allowed_profiles=["short_vertical"],
        ),
        analytics_config=AnalyticsConfig(tracking_enabled=True),
        monetization=MonetizationMetadata(),
        active_strategy_version="strat-sci-v1",
    )

    # Channel C: History
    ch_hist = ChannelProfile(
        channel_id="chronicles-past",
        channel_name="Chronicles of the Past",
        status=ChannelStatus.ACTIVE,
        version="1",
        niche=NicheConfig(
            niche_name="history",
            description="Untold stories and pivotal historical moments",
            allowed_categories=["history", "ancient", "warfare"],
            excluded_categories=["technology", "science"],
            terminology=["manuscript", "dynasty", "archaeological", "tactics"],
            ideation_weighting={"history": 1.7},
        ),
        language="en",
        locale="en-US",
        persona=PersonaConfig(
            tone="dramatic",
            vocabulary_level="evocative",
            narrator_personality="cinematic historical storyteller",
            cta_style="Subscribe for forgotten chapters of world history",
            hook_style="moment-of-crisis hook",
            pacing_preference="deliberate",
        ),
        voice=VoiceProfile(
            provider="mock",
            voice_id="history-voice-gamma",
            language="en",
            speaking_rate=0.95,
        ),
        visual=VisualBrandProfile(
            font_family="Cinzel-Bold",
            caption_style="parchment_gold",
            theme_color_primary="#d4af37",
            theme_color_secondary="#1a0f00",
            visual_motif="parchment map vintage relics",
        ),
        content_formats=["short_vertical"],
        target_platforms=["youtube"],
        posting_policy=PostingPolicy(timezone="UTC", max_daily_posts=3),
        autonomy_policy=AutonomyPolicy(
            autonomy_level=3, # Guarded auto-queue
            max_ideas_per_cycle=3,
            max_auto_queue_per_cycle=2,
            max_jobs_per_day=3,
            topic_cooldown_days=7,
            require_evidence=False,
            allowed_profiles=["short_vertical"],
        ),
        analytics_config=AnalyticsConfig(tracking_enabled=True),
        monetization=MonetizationMetadata(),
        active_strategy_version="strat-hist-v1",
    )

    mgr.save_profile(ch_tech, comment="Tech channel setup")
    mgr.save_profile(ch_sci, comment="Science channel setup")
    mgr.save_profile(ch_hist, comment="History channel setup")

    return ch_tech, ch_sci, ch_hist


def test_three_channel_deterministic_e2e_run(tmp_path: Path):
    """Sections 31 & 39: Complete deterministic multi-channel run across 3 channels."""
    db_file = tmp_path / "autopilot_multi.db"
    db = DatabaseManager(db_file)
    db.init_schema()
    mgr = ChannelManager(db)

    # 1. Setup Profiles
    ch_tech, ch_sci, ch_hist = create_3_channels(mgr)
    assert len(mgr.list_channels()) == 3

    engine = AutonomyEngine(db=db)

    # Mock signals across categories
    trend_signals = [
        TrendSignal(
            signal_id="sig-tech-1",
            topic="Novel GPU Microarchitecture Benchmark",
            source="mock",
            category="technology",
            freshness_score=0.95,
            relevance_score=0.9,
        ),
        TrendSignal(
            signal_id="sig-sci-1",
            topic="Webb Telescope Discovers Ancient Galaxy Cluster",
            source="mock",
            category="science",
            freshness_score=0.92,
            relevance_score=0.95,
        ),
        TrendSignal(
            signal_id="sig-hist-1",
            topic="Lost Roman Legion Secret Encampment Found",
            source="mock",
            category="history",
            freshness_score=0.88,
            relevance_score=0.9,
        ),
    ]

    # Run deterministic discovery
    with patch.object(engine.discovery, "discover_trends", return_value=trend_signals):
        # Channel A Cycle
        sum_a = engine.run_cycle(channel_id="tech-pulse", autonomy_level=AutonomyLevel.LEVEL_3_AUTO_QUEUE.value)
        assert sum_a.channel_id == "tech-pulse"
        assert sum_a.jobs_queued >= 1
        assert sum_a.active_strategy_version == "strat-tech-v1"

        # Channel B Cycle
        sum_b = engine.run_cycle(channel_id="cosmos-lab", autonomy_level=AutonomyLevel.LEVEL_3_AUTO_QUEUE.value)
        assert sum_b.channel_id == "cosmos-lab"
        assert sum_b.jobs_queued >= 1
        assert sum_b.active_strategy_version == "strat-sci-v1"

        # Channel C Cycle
        sum_c = engine.run_cycle(channel_id="chronicles-past", autonomy_level=AutonomyLevel.LEVEL_3_AUTO_QUEUE.value)
        assert sum_c.channel_id == "chronicles-past"
        assert sum_c.jobs_queued >= 1
        assert sum_c.active_strategy_version == "strat-hist-v1"

    # Verify queue has jobs for all 3 channels
    queue_items = db.list_queue_items()
    assert len(queue_items) == 3
    channels_queued = {it["channel_id"] for it in queue_items}
    assert channels_queued == {"tech-pulse", "cosmos-lab", "chronicles-past"}

    # Process all 3 through local worker with mocked deterministic pipeline
    worker = Worker(db=db, worker_id="worker-deterministic")
    with patch("autopilot.core.worker.run_pipeline") as mock_pipeline:
        def pipeline_side_effect(job_id, topic, profile, tts_provider, **kwargs):
            return {
                "job_id": job_id,
                "qa_passed": True,
                "status": "QA_PASSED",
                "rendered_media_path": str(tmp_path / f"{job_id}.mp4"),
            }
        mock_pipeline.side_effect = pipeline_side_effect

        # Process all 3 jobs
        processed = worker.run_all()
        assert processed == 3

    # Verify all 3 succeeded
    after_items = db.list_queue_items()
    for it in after_items:
        assert it["status"] == "succeeded"

    # Simulate Mock Analytics for each job
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    views_map = {"tech-pulse": 1200.0, "cosmos-lab": 2400.0, "chronicles-past": 3100.0}

    for it in after_items:
        cid = it["channel_id"]
        jid = it["job_id"]
        snap = AnalyticsSnapshot(
            snapshot_id=f"snap-{jid}",
            job_id=jid,
            platform="youtube",
            remote_id=f"yt-{jid}",
            window="lifetime",
            observed_at=now,
            provider="mock",
            is_synthetic=False,
            metrics={
                "views": MetricObservation(
                    observation_id=f"m-views-{jid}",
                    snapshot_id=f"snap-{jid}",
                    metric_name="views",
                    metric_value=views_map[cid],
                    observed_at=now,
                )
            },
        )
        db.record_analytics_snapshot(snap)

    # Comparative Analytics Verification
    comparison = mgr.compare_channels()
    assert len(comparison) == 3

    tech_stat = next(c for c in comparison if c["channel_id"] == "tech-pulse")
    sci_stat = next(c for c in comparison if c["channel_id"] == "cosmos-lab")
    hist_stat = next(c for c in comparison if c["channel_id"] == "chronicles-past")

    assert tech_stat["total_views"] == 1200
    assert sci_stat["total_views"] == 2400
    assert hist_stat["total_views"] == 3100
    assert tech_stat["content_count"] == 1
    assert sci_stat["content_count"] == 1
    assert hist_stat["content_count"] == 1

    # Verify Quota Consumption Isolation
    quota_tech = mgr.get_quota_usage("tech-pulse")
    quota_sci = mgr.get_quota_usage("cosmos-lab")
    quota_hist = mgr.get_quota_usage("chronicles-past")
    assert quota_tech["jobs_queued"] >= 1
    assert quota_sci["jobs_queued"] >= 1
    assert quota_hist["jobs_queued"] >= 1
