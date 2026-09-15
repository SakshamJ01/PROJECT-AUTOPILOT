import pytest
from pathlib import Path
from pydantic import ValidationError

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
)
from autopilot.core.channel import ChannelManager
from autopilot.db.manager import DatabaseManager


def create_sample_profile(channel_id: str = "tech-bytes", channel_name: str = "Tech Bytes") -> ChannelProfile:
    return ChannelProfile(
        channel_id=channel_id,
        channel_name=channel_name,
        status=ChannelStatus.ACTIVE,
        version="1",
        niche=NicheConfig(
            niche_name="technology",
            description="Short tech insights and AI news",
            allowed_categories=["technology", "ai", "software"],
            excluded_categories=["politics", "crypto_hype"],
            terminology=["neural network", "transformer", "latency"],
            ideation_weighting={"technology": 1.2, "ai": 1.5},
        ),
        language="en",
        locale="en-US",
        persona=PersonaConfig(
            tone="analytical",
            vocabulary_level="advanced",
            narrator_personality="authoritative and crisp",
            cta_style="Subscribe for daily engineering insights",
            hook_style="provocative question",
            pacing_preference="fast",
        ),
        voice=VoiceProfile(
            provider="mock",
            voice_id="tech-voice-1",
            language="en",
            speaking_rate=1.1,
            pitch=1.0,
        ),
        visual=VisualBrandProfile(
            font_family="Roboto-Bold",
            caption_style="yellow_boxed",
            theme_color_primary="#00ffff",
            theme_color_secondary="#ff00ff",
            visual_motif="neon circuit tech",
            watermark_enabled=False,
        ),
        content_formats=["short_vertical"],
        target_platforms=["youtube"],
        posting_policy=PostingPolicy(
            timezone="UTC",
            max_daily_posts=5,
            preferred_windows=["12:00-14:00", "18:00-20:00"],
        ),
        autonomy_policy=AutonomyPolicy(
            autonomy_level=2,
            max_ideas_per_cycle=5,
            max_auto_queue_per_cycle=2,
            max_jobs_per_day=5,
            topic_cooldown_days=7,
            require_evidence=True,
            allowed_profiles=["short_vertical"],
        ),
        analytics_config=AnalyticsConfig(
            tracking_enabled=True,
            sync_window="lifetime",
            primary_metric="views",
        ),
        monetization=MonetizationMetadata(
            monetization_category="technology",
            commercial_flags=["no_sponsorships"],
            cta_configuration={"default": "Like and subscribe"},
        ),
        active_strategy_version="strat-v1",
    )


def test_channel_profile_round_trip(tmp_path: Path):
    profile = create_sample_profile()
    data = profile.model_dump()
    reconstructed = ChannelProfile.model_validate(data)
    assert reconstructed.channel_id == "tech-bytes"
    assert reconstructed.niche.niche_name == "technology"
    assert reconstructed.voice.provider == "mock"
    assert reconstructed.target_platforms == ["youtube"]
    assert reconstructed.status == ChannelStatus.ACTIVE


def test_channel_profile_validation_success(tmp_path: Path):
    db = DatabaseManager(tmp_path / "test.db")
    db.init_schema()
    mgr = ChannelManager(db)
    profile = create_sample_profile()
    errors = mgr.validate_profile(profile)
    assert len(errors) == 0


def test_channel_profile_validation_unsupported_platform(tmp_path: Path):
    with pytest.raises(ValidationError):
        ChannelProfile(
            channel_id="test-unsupported",
            channel_name="Test",
            target_platforms=["unsupported_network_xyz"],
        )


def test_channel_profile_validation_invalid_voice_rate(tmp_path: Path):
    db = DatabaseManager(tmp_path / "test.db")
    db.init_schema()
    mgr = ChannelManager(db)
    # VoiceProfile speaking_rate must be <= 2.5
    with pytest.raises(ValidationError):
        VoiceProfile(speaking_rate=5.0)


def test_channel_profile_persistence_and_retrieval(tmp_path: Path):
    db_file = tmp_path / "test.db"
    db = DatabaseManager(db_file)
    db.init_schema()
    mgr = ChannelManager(db)

    profile = create_sample_profile("chan-science", "Science Wonders")
    saved = mgr.save_profile(profile, comment="Initial creation")
    assert saved.version == "1"

    retrieved = mgr.get_channel("chan-science")
    assert retrieved is not None
    assert retrieved.channel_id == "chan-science"
    assert retrieved.channel_name == "Science Wonders"
    assert retrieved.niche.niche_name == "technology"
    assert retrieved.status == ChannelStatus.ACTIVE


def test_channel_profile_versioning_and_immutability(tmp_path: Path):
    db_file = tmp_path / "test.db"
    db = DatabaseManager(db_file)
    db.init_schema()
    mgr = ChannelManager(db)

    profile = create_sample_profile("chan-v-test", "Version Test")
    v1 = mgr.save_profile(profile, comment="v1 initial")
    assert v1.version == "1"

    # Mutate profile and save again
    profile.persona.tone = "humorous"
    profile.persona.cta_style = "Leave a funny comment!"
    v2 = mgr.save_profile(profile, comment="v2 updated tone to humorous")
    assert v2.version == "2"

    # Check that latest active profile is v2
    current = mgr.get_channel("chan-v-test")
    assert current is not None
    assert current.version == "2"
    assert current.persona.tone == "humorous"

    # Check version history
    history = mgr.get_version_history("chan-v-test")
    assert len(history) == 2
    v_map = {v.version: v for v in history}
    assert "1" in v_map and "2" in v_map
    assert v_map["1"].change_comment == "v1 initial"
    snap_1 = v_map["1"].profile_snapshot
    tone_1 = snap_1["persona"]["tone"] if isinstance(snap_1, dict) else snap_1.persona.tone
    assert tone_1 == "analytical"
    assert v_map["2"].change_comment == "v2 updated tone to humorous"
    snap_2 = v_map["2"].profile_snapshot
    tone_2 = snap_2["persona"]["tone"] if isinstance(snap_2, dict) else snap_2.persona.tone
    assert tone_2 == "humorous"


def test_channel_enable_disable(tmp_path: Path):
    db_file = tmp_path / "test.db"
    db = DatabaseManager(db_file)
    db.init_schema()
    mgr = ChannelManager(db)

    profile = create_sample_profile("chan-toggle", "Toggle Channel")
    mgr.save_profile(profile)

    # Disable channel
    assert mgr.set_channel_status("chan-toggle", ChannelStatus.DISABLED)
    ch = mgr.get_channel("chan-toggle")
    assert ch.status == ChannelStatus.DISABLED

    # Re-enable channel
    assert mgr.set_channel_status("chan-toggle", ChannelStatus.ACTIVE)
    ch = mgr.get_channel("chan-toggle")
    assert ch.status == ChannelStatus.ACTIVE


def test_channel_bootstrap_default(tmp_path: Path):
    db = DatabaseManager(tmp_path / "test.db")
    db.init_schema()
    mgr = ChannelManager(db)

    # Before bootstrap
    assert len(mgr.list_channels()) == 0

    # Run bootstrap
    default_ch = mgr.get_or_create_default_channel()
    assert default_ch.channel_id == "default"
    assert default_ch.status == ChannelStatus.ACTIVE

    # List channels now contains default
    all_ch = mgr.list_channels()
    assert len(all_ch) == 1
    assert all_ch[0].channel_id == "default"
