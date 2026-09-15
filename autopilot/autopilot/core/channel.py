"""CANONICAL CHANNEL PROFILE & SCALING ENGINE — Milestone 10 (M10).
Manages channel profiles, versioning, persona, voice, visual identity,
niche configuration, quotas, and cross-channel isolation.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from autopilot.core.contracts import (
    ChannelProfile,
    ChannelProfileVersion,
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
from autopilot.db.manager import DBManager

DatabaseManager = DBManager


BUILTIN_CHANNEL_PRESETS: Dict[str, Dict[str, Any]] = {
    "science_shorts": {
        "channel_id": "science_shorts",
        "channel_name": "Science Shorts",
        "niche": {
            "niche_name": "science",
            "description": "Empirical discoveries, physics anomalies, and scientific breakthroughs",
            "allowed_categories": ["science", "physics", "biology", "astronomy"],
            "content_format_preferences": ["short_vertical"],
        },
        "persona": {
            "persona_name": "science_educator",
            "tone": "educational",
            "vocabulary_level": "accessible_expert",
            "narration_personality": "empirical_and_curious",
            "cta_style": "curiosity",
            "hook_style": "surprising_discovery",
        },
        "voice": {
            "provider": "kokoro",
            "voice_id": "af_bella",
            "language": "en",
            "speaking_rate": 1.05,
        },
        "visual": {
            "font_family": "Montserrat",
            "primary_color": "#00D2FF",
            "secondary_color": "#3A7BD5",
            "caption_style": "bold_center",
            "background_strategy": "blur_fill",
            "visual_motif": "scientific_photography",
        },
        "target_platforms": ["youtube"],
        "status": "active",
        "profile_version": "v1",
    },
    "history_shorts": {
        "channel_id": "history_shorts",
        "channel_name": "History Shorts",
        "niche": {
            "niche_name": "history",
            "description": "Chronological history, forgotten turning points, and archival evidence",
            "allowed_categories": ["history", "ancient_civilizations", "military_history", "biography"],
            "content_format_preferences": ["short_vertical"],
        },
        "persona": {
            "persona_name": "historical_narrator",
            "tone": "historical",
            "vocabulary_level": "formal",
            "narration_personality": "dramatic_storyteller",
            "cta_style": "educational",
            "hook_style": "historical_framing_and_date",
        },
        "voice": {
            "provider": "kokoro",
            "voice_id": "am_adam",
            "language": "en",
            "speaking_rate": 0.98,
        },
        "visual": {
            "font_family": "Georgia",
            "primary_color": "#E0C38C",
            "secondary_color": "#8B5A2B",
            "caption_style": "archival_cinematic",
            "background_strategy": "blur_fill",
            "visual_motif": "archival_photography",
        },
        "target_platforms": ["youtube"],
        "status": "active",
        "profile_version": "v1",
    },
    "tech_shorts": {
        "channel_id": "tech_shorts",
        "channel_name": "Tech Shorts",
        "niche": {
            "niche_name": "technology",
            "description": "Emerging hardware, artificial intelligence, and computing architectures",
            "allowed_categories": ["technology", "ai", "hardware", "computing"],
            "content_format_preferences": ["short_vertical"],
        },
        "persona": {
            "persona_name": "tech_analyst",
            "tone": "futuristic_technical",
            "vocabulary_level": "technical",
            "narration_personality": "enthusiastic_innovator",
            "cta_style": "action_oriented",
            "hook_style": "breakthrough_fact",
        },
        "voice": {
            "provider": "kokoro",
            "voice_id": "af_nicole",
            "language": "en",
            "speaking_rate": 1.1,
        },
        "visual": {
            "font_family": "Roboto",
            "primary_color": "#00FFA3",
            "secondary_color": "#001F3F",
            "caption_style": "modern_neon",
            "background_strategy": "blur_fill",
            "visual_motif": "hardware_and_electronics",
        },
        "target_platforms": ["youtube"],
        "status": "active",
        "profile_version": "v1",
    },
}


def load_profile_from_file(file_path: str | Path) -> ChannelProfile:
    """Parses and validates a channel profile definition from a JSON or YAML file."""
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"Channel profile file not found: {p}")

    raw_text = p.read_text(encoding="utf-8").strip()
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        try:
            import yaml
            data = yaml.safe_load(raw_text)
        except ImportError:
            raise ValueError(f"Could not parse profile file as JSON: {p}. (Install pyyaml for YAML files)")

    if not isinstance(data, dict):
        raise ValueError(f"Invalid profile file structure in {p}: root must be a dictionary")

    return ChannelProfile.model_validate(data)


class ChannelManager:
    """Core manager for ChannelProfiles, versioning, and channel lifecycle."""

    def __init__(self, db: Optional[DatabaseManager] = None):
        self.db = db or DatabaseManager()
        self.db.init_schema()

    def get_or_create_channel(self, channel_id: str) -> ChannelProfile:
        """Retrieves an existing channel or instantiates a preset / file profile automatically."""
        ch = self.get_channel(channel_id)
        if ch:
            return ch
        if channel_id == "default":
            return self.ensure_default_channel()
        if channel_id in BUILTIN_CHANNEL_PRESETS:
            prof = ChannelProfile.model_validate(BUILTIN_CHANNEL_PRESETS[channel_id])
            self.create_channel(prof, change_summary=f"Bootstrap built-in preset '{channel_id}'")
            return prof
        # Check if channel_id is a file path
        p = Path(channel_id)
        if p.exists() and p.is_file():
            prof = load_profile_from_file(p)
            self.create_channel(prof, change_summary=f"Imported from {p.name}")
            return prof
        # Check channels directory
        cand_json = Path("channels") / f"{channel_id}.json"
        if cand_json.exists():
            prof = load_profile_from_file(cand_json)
            self.create_channel(prof, change_summary=f"Imported from {cand_json.name}")
            return prof
        cand_yaml = Path("channels") / f"{channel_id}.yaml"
        if cand_yaml.exists():
            prof = load_profile_from_file(cand_yaml)
            self.create_channel(prof, change_summary=f"Imported from {cand_yaml.name}")
            return prof

        # Fallback: create a customized channel profile on the fly
        fallback_prof = ChannelProfile(
            channel_id=channel_id,
            channel_name=channel_id.replace("_", " ").title(),
            niche=NicheConfig(niche_name="general"),
            profile_version="v1",
        )
        self.create_channel(fallback_prof, change_summary=f"Auto-generated profile for '{channel_id}'")
        return fallback_prof

    def get_or_create_default_channel(self) -> ChannelProfile:
        """Alias for ensure_default_channel."""
        return self.ensure_default_channel()

    def save_profile(self, profile: ChannelProfile, comment: str = "Profile update") -> ChannelProfile:
        """Saves a profile, creating if new, or updating with version progression if existing."""
        existing = self.get_channel(profile.channel_id)
        if existing:
            return self.update_channel(profile, bump_version=True, change_summary=comment)
        else:
            return self.create_channel(profile, change_summary=comment)

    def validate_profile(self, profile: ChannelProfile) -> List[str]:
        """Alias for validate_channel."""
        return self.validate_channel(profile)

    def set_channel_status(self, channel_id: str, status: ChannelStatus) -> bool:
        """Sets status of a channel (ACTIVE or DISABLED)."""
        if status == ChannelStatus.ACTIVE:
            return self.enable_channel(channel_id)
        else:
            return self.disable_channel(channel_id)

    def ensure_default_channel(self) -> ChannelProfile:
        """Ensures a baseline default channel exists in the database."""
        existing = self.db.get_channel_profile("default")
        if existing and existing.get("profile"):
            return ChannelProfile.model_validate(existing["profile"])

        default_profile = ChannelProfile(
            channel_id="default",
            channel_name="Default Channel",
            niche=NicheConfig(
                niche_name="general",
                description="General educational and informative content",
                allowed_categories=["technology", "science", "history", "general"],
                content_format_preferences=["short_vertical"],
            ),
            persona=PersonaConfig(
                persona_name="default",
                tone="informative",
                vocabulary_level="accessible",
                narration_personality="objective",
                cta_style="subtle",
                hook_style="intriguing_question",
            ),
            voice=VoiceProfile(
                provider="mock",
                voice_id="en-US-Standard",
                language="en",
                speaking_rate=1.0,
            ),
            visual=VisualBrandProfile(
                font_family="Arial",
                primary_color="#FFFFFF",
                secondary_color="#FFD700",
                caption_style="bold_center",
                background_strategy="blur_fill",
            ),
            target_platforms=["youtube"],
            status=ChannelStatus.ACTIVE,
            active_strategy_version_id="strat-v1",
            profile_version="v1",
        )
        self.create_channel(default_profile, change_summary="Bootstrap default channel")
        return default_profile

    def create_channel(
        self,
        profile: ChannelProfile,
        change_summary: str = "Initial creation",
    ) -> ChannelProfile:
        """Validates and persists a new channel profile and initial version snapshot."""
        errors = self.validate_channel(profile)
        if errors:
            raise ValueError(f"Channel profile validation failed: {'; '.join(errors)}")

        existing = self.db.get_channel_profile(profile.channel_id)
        if existing:
            raise ValueError(f"Channel with id '{profile.channel_id}' already exists.")

        if profile.active_strategy_version:
            profile.active_strategy_version_id = profile.active_strategy_version
        if profile.version:
            profile.profile_version = f"v{profile.version}" if not str(profile.version).startswith("v") else str(profile.version)

        # Persist active profile
        self.db.record_channel_profile(profile)

        # Persist immutable version snapshot
        version_id = f"{profile.channel_id}:{profile.profile_version}"
        version_record = ChannelProfileVersion(
            version_id=version_id,
            channel_id=profile.channel_id,
            profile_version=profile.profile_version,
            profile_snapshot=profile.model_dump(),
            strategy_version_id=profile.active_strategy_version_id,
            change_summary=change_summary,
            created_at=profile.created_at,
        )
        self.db.record_channel_version(version_record)
        return profile

    def get_channel(self, channel_id: str) -> Optional[ChannelProfile]:
        """Retrieves active channel profile by ID."""
        data = self.db.get_channel_profile(channel_id)
        if not data or not data.get("profile"):
            return None
        try:
            return ChannelProfile.model_validate(data["profile"])
        except Exception:
            p_data = dict(data["profile"])
            if "voice" in p_data and isinstance(p_data["voice"], dict):
                p_data["voice"] = VoiceProfile.model_construct(**p_data["voice"])
            if "niche" in p_data and isinstance(p_data["niche"], dict):
                p_data["niche"] = NicheConfig.model_construct(**p_data["niche"])
            if "persona" in p_data and isinstance(p_data["persona"], dict):
                p_data["persona"] = PersonaConfig.model_construct(**p_data["persona"])
            if "visual" in p_data and isinstance(p_data["visual"], dict):
                p_data["visual"] = VisualBrandProfile.model_construct(**p_data["visual"])
            if "posting_policy" in p_data and isinstance(p_data["posting_policy"], dict):
                p_data["posting_policy"] = PostingPolicy.model_construct(**p_data["posting_policy"])
            if "autonomy_policy" in p_data and isinstance(p_data["autonomy_policy"], dict):
                p_data["autonomy_policy"] = AutonomyPolicy.model_construct(**p_data["autonomy_policy"])
            return ChannelProfile.model_construct(**p_data)

    def list_channels(self, status: Optional[str] = None) -> List[ChannelProfile]:
        """Lists all channel profiles, optionally filtered by status."""
        records = self.db.list_channel_profiles(status=status)
        channels: List[ChannelProfile] = []
        for r in records:
            if r.get("profile"):
                try:
                    channels.append(ChannelProfile.model_validate(r["profile"]))
                except Exception:
                    pass
        return channels

    def update_channel(
        self,
        profile: ChannelProfile,
        bump_version: bool = True,
        change_summary: str = "Profile update",
    ) -> ChannelProfile:
        """Updates channel configuration with version progression."""
        errors = self.validate_channel(profile)
        if errors:
            raise ValueError(f"Channel profile validation failed: {'; '.join(errors)}")

        existing = self.get_channel(profile.channel_id)
        if not existing:
            raise ValueError(f"Channel with id '{profile.channel_id}' not found.")

        # Calculate next version if requested
        if bump_version:
            current_ver_match = re.match(r"^v?(\d+)$", profile.profile_version)
            if current_ver_match:
                next_num = int(current_ver_match.group(1)) + 1
                profile.profile_version = f"v{next_num}"
                profile.version = str(next_num)
            else:
                profile.profile_version = f"{profile.profile_version}_v2"
                profile.version = profile.profile_version

        if profile.active_strategy_version:
            profile.active_strategy_version_id = profile.active_strategy_version
        if profile.version and not bump_version:
            profile.profile_version = f"v{profile.version}" if not str(profile.version).startswith("v") else str(profile.version)

        profile.updated_at = datetime.now(timezone.utc).isoformat()

        # Update active profile in DB
        self.db.record_channel_profile(profile)

        # Create immutable version record for lineage
        version_id = f"{profile.channel_id}:{profile.profile_version}"
        version_record = ChannelProfileVersion(
            version_id=version_id,
            channel_id=profile.channel_id,
            profile_version=profile.profile_version,
            profile_snapshot=profile.model_dump(),
            strategy_version_id=profile.active_strategy_version_id,
            change_summary=change_summary,
            created_at=profile.updated_at,
        )
        self.db.record_channel_version(version_record)
        return profile

    def enable_channel(self, channel_id: str) -> bool:
        """Enables a channel profile."""
        profile = self.get_channel(channel_id)
        if not profile:
            return False
        profile.status = ChannelStatus.ACTIVE
        self.db.record_channel_profile(profile)
        return self.db.update_channel_status(channel_id, ChannelStatus.ACTIVE.value)

    def disable_channel(self, channel_id: str) -> bool:
        """Disables a channel profile."""
        profile = self.get_channel(channel_id)
        if not profile:
            return False
        profile.status = ChannelStatus.DISABLED
        self.db.record_channel_profile(profile)
        return self.db.update_channel_status(channel_id, ChannelStatus.DISABLED.value)

    def get_version_history(self, channel_id: str) -> List[ChannelProfileVersion]:
        """Returns chronological versions for a channel."""
        rows = self.db.list_channel_versions(channel_id)
        versions: List[ChannelProfileVersion] = []
        for r in rows:
            try:
                versions.append(ChannelProfileVersion.model_validate(r))
            except Exception:
                pass
        return versions

    def get_version(self, channel_id: str, profile_version: str) -> Optional[ChannelProfileVersion]:
        """Returns specific historical version snapshot."""
        row = self.db.get_channel_version(channel_id, profile_version)
        if not row:
            return None
        return ChannelProfileVersion.model_validate(row)

    def validate_channel(self, profile: ChannelProfile) -> List[str]:
        """Validates all aspects of a ChannelProfile before activation."""
        errors: List[str] = []
        if not profile.channel_id or not profile.channel_id.strip():
            errors.append("channel_id cannot be empty")
        elif not re.match(r"^[a-zA-Z0-9_\-]+$", profile.channel_id):
            errors.append("channel_id contains invalid characters (allowed: alphanumeric, -, _)")

        if not profile.channel_name or not profile.channel_name.strip():
            errors.append("channel_name cannot be empty")

        if not profile.target_platforms:
            errors.append("at least one target platform must be specified")
        else:
            supported = {"youtube", "tiktok", "instagram"}
            for p in profile.target_platforms:
                if p.lower() not in supported:
                    errors.append(f"unsupported platform '{p}'. Allowed: {supported}")

        if not profile.niche or not profile.niche.niche_name:
            errors.append("niche configuration must specify niche_name")

        if not profile.voice or not profile.voice.voice_id:
            errors.append("voice profile must specify voice_id")

        if profile.autonomy_policy.max_jobs_per_day < 1:
            errors.append("autonomy_policy.max_jobs_per_day must be at least 1")

        if profile.posting_policy.max_daily_posts < 1:
            errors.append("posting_policy.max_daily_posts must be at least 1")

        return errors

    def check_daily_quota(
        self,
        channel_id: str,
        date_str: Optional[str] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """Checks if the channel has reached its daily quota."""
        channel = self.get_channel(channel_id)
        if not channel:
            return False, f"Channel '{channel_id}' not found.", {}

        if channel.status != ChannelStatus.ACTIVE:
            return False, f"Channel '{channel_id}' is disabled.", {}

        usage = self.db.get_channel_quota_usage(channel_id, date_str=date_str)
        queued_count = usage.get("queued_count", 0)
        published_count = usage.get("published_count", 0)

        max_daily_jobs = channel.autonomy_policy.max_jobs_per_day
        max_daily_posts = channel.posting_policy.max_daily_posts

        quota_info = {
            "channel_id": channel_id,
            "date": usage.get("date_str"),
            "queued_today": queued_count,
            "max_daily_jobs": max_daily_jobs,
            "published_today": published_count,
            "max_daily_posts": max_daily_posts,
            "jobs_remaining": max(0, max_daily_jobs - queued_count),
        }

        if queued_count >= max_daily_jobs:
            return False, f"Channel '{channel_id}' reached daily queue limit ({queued_count}/{max_daily_jobs}).", quota_info

        return True, "Quota check passed.", quota_info

    def check_channel_quota(self, channel_id: str) -> bool:
        """Returns True if the channel has available daily quota, False otherwise."""
        allowed, _, _ = self.check_daily_quota(channel_id)
        return allowed

    def record_quota_consumption(
        self,
        channel_id: str,
        queued_increment: int = 0,
        published_increment: int = 0,
        jobs_count: Optional[int] = None,
        date_str: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Increments today's quota usage for a channel."""
        if jobs_count is not None and queued_increment == 0:
            queued_increment = jobs_count
        return self.db.record_channel_quota_usage(
            channel_id=channel_id,
            queued_increment=queued_increment,
            published_increment=published_increment,
            date_str=date_str,
        )

    def get_quota_usage(self, channel_id: str, date_str: Optional[str] = None) -> Dict[str, Any]:
        """Returns today's quota usage summary for a channel."""
        return self.db.get_channel_quota_usage(channel_id=channel_id, date_str=date_str)

    def validate_brand_consistency(
        self,
        channel_id: str,
        content_item: Dict[str, Any],
    ) -> Tuple[bool, List[str]]:
        """Validates that generated content adheres to the channel's profile."""
        channel = self.get_channel(channel_id)
        if not channel:
            return False, [f"Channel '{channel_id}' not found."]

        warnings: List[str] = []

        # Check language
        item_lang = content_item.get("language")
        if item_lang and item_lang != channel.language:
            warnings.append(f"Content language '{item_lang}' differs from channel language '{channel.language}'.")

        # Check target platforms
        item_platforms = content_item.get("platform_targets", [])
        for p in item_platforms:
            if p not in channel.target_platforms:
                warnings.append(f"Content target '{p}' is not in channel allowed platforms {channel.target_platforms}.")

        # Check category alignment with niche
        item_category = content_item.get("category")
        if item_category:
            if channel.niche.excluded_categories and item_category in channel.niche.excluded_categories:
                return False, [f"Content category '{item_category}' is explicitly excluded by channel niche."]
            if channel.niche.allowed_categories and item_category not in channel.niche.allowed_categories:
                warnings.append(f"Content category '{item_category}' is outside channel niche categories {channel.niche.allowed_categories}.")

        return len(warnings) == 0, warnings

    def compare_channels(
        self,
        channel_ids: Optional[List[str]] = None,
        platform: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Compares performance and production volume across channels deterministically."""
        channels = self.list_channels()
        if channel_ids:
            channels = [c for c in channels if c.channel_id in channel_ids]
        if platform:
            channels = [c for c in channels if platform.lower() in [p.lower() for p in c.target_platforms]]

        comparison: List[Dict[str, Any]] = []
        with self.db._connect() as conn:
            for ch in channels:
                cid = ch.channel_id
                # Count total jobs
                jobs_row = conn.execute(
                    "SELECT count(*) as total_jobs, "
                    "sum(case when status = 'PUBLISHED' then 1 else 0 end) as published_jobs, "
                    "sum(case when status = 'FAILED' then 1 else 0 end) as failed_jobs "
                    "FROM jobs WHERE channel_id = ?",
                    (cid,),
                ).fetchone()

                # Count queued items
                queue_row = conn.execute(
                    "SELECT count(*) as queued_items, "
                    "sum(case when status = 'succeeded' then 1 else 0 end) as successful_queue, "
                    "sum(case when status = 'failed' then 1 else 0 end) as failed_queue "
                    "FROM queue_items WHERE channel_id = ?",
                    (cid,),
                ).fetchone()

                # Performance metrics
                perf_row = conn.execute(
                    "SELECT count(*) as feedback_count, "
                    "avg(observed_views) as avg_views, "
                    "avg(observed_engagement_rate) as avg_engagement "
                    "FROM feedback_observations WHERE channel_id = ?",
                    (cid,),
                ).fetchone()

                total_jobs = jobs_row["total_jobs"] if jobs_row else 0
                published_jobs = jobs_row["published_jobs"] or 0 if jobs_row else 0
                failed_jobs = jobs_row["failed_jobs"] or 0 if jobs_row else 0
                avg_views = round(float(perf_row["avg_views"] or 0.0), 1) if perf_row else 0.0
                avg_engagement = round(float(perf_row["avg_engagement"] or 0.0), 4) if perf_row else 0.0

                views_row = conn.execute(
                    "SELECT sum(mo.normalized_value) as total_views "
                    "FROM metric_observations mo "
                    "JOIN analytics_snapshots s ON mo.snapshot_id = s.snapshot_id "
                    "JOIN jobs j ON s.job_id = j.job_id "
                    "WHERE j.channel_id = ? AND mo.metric_name = 'views'",
                    (cid,),
                ).fetchone()
                total_views_metric = int(views_row["total_views"] or 0) if views_row else 0
                total_views = total_views_metric or int((perf_row["feedback_count"] if perf_row else 0) * (perf_row["avg_views"] or 0) if perf_row else 0)

                comparison.append(
                    {
                        "channel_id": cid,
                        "channel_name": ch.channel_name,
                        "niche": ch.niche.niche_name,
                        "status": ch.status.value,
                        "profile_version": ch.profile_version,
                        "strategy_version": ch.active_strategy_version_id,
                        "target_platforms": ch.target_platforms,
                        "total_jobs": total_jobs,
                        "content_count": total_jobs,
                        "published_jobs": published_jobs,
                        "published_count": published_jobs,
                        "failed_jobs": failed_jobs,
                        "queued_items": queue_row["queued_items"] if queue_row else 0,
                        "total_views": total_views,
                        "avg_views": avg_views,
                        "avg_engagement": avg_engagement,
                        "avg_engagement_rate": avg_engagement,
                        "daily_quota": ch.autonomy_policy.max_jobs_per_day,
                    }
                )
        return comparison
