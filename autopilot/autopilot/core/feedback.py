"""Historical Feedback, Associational Learning & Strategy Management — Milestone 9.
Analyzes M8 analytics outcomes to identify patterns associated with performance.
Produces feedback signals, learning observations, and versioned strategy adjustments.
"""
from __future__ import annotations
import hashlib
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from autopilot.core.contracts import (
    FeedbackSignal,
    LearningObservation,
    StrategyVersion,
    StrategyStatus,
    PerformanceTier,
)
from autopilot.core.config import CONFIG
from autopilot.db.manager import DBManager


class FeedbackAnalyzer:
    """Ingests M8 analytics and extracts associational feedback signals with outlier damping."""

    def __init__(self, db: Optional[DBManager] = None):
        self.db = db or DBManager(CONFIG.db_path)
        self.db.init_schema()

    def extract_feedback_signals(self, limit: int = 50, channel_id: Optional[str] = None) -> List[FeedbackSignal]:
        """Extracts feedback signals from historical job performance, isolating by channel if provided."""
        signals: List[FeedbackSignal] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        # Query jobs with performance records, respecting channel isolation
        with self.db._connect() as conn:
            if channel_id:
                jobs = conn.execute(
                    "SELECT job_id, channel_id, topic FROM jobs WHERE channel_id = ? ORDER BY updated_at DESC LIMIT ?",
                    (channel_id, limit),
                ).fetchall()
            else:
                jobs = conn.execute(
                    "SELECT job_id, channel_id, topic FROM jobs ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()

        perf_records = []
        for j in jobs:
            perf = self.db.get_content_performance(j["job_id"])
            if perf and perf.latest_snapshot:
                # Ensure channel_id is recorded
                if not perf.channel_id or perf.channel_id == "default":
                    perf.channel_id = j["channel_id"] or "default"
                perf_records.append(perf)

        if not perf_records:
            return signals

        # Calculate median views to establish relative performance baseline
        all_views = sorted(
            [p.latest_snapshot.metrics.get("views").normalized_value for p in perf_records if "views" in p.latest_snapshot.metrics]
        )
        n_views = len(all_views)
        median_views = all_views[n_views // 2] if all_views else 1000.0
        if median_views <= 0:
            median_views = 1000.0

        if n_views >= 3:
            p66 = all_views[int(n_views * 0.6)]
            p33 = all_views[int(n_views * 0.3)]
        else:
            p66 = median_views if median_views >= 2000 else median_views * 1.2
            p33 = median_views * 0.5

        for perf in perf_records:
            snap = perf.latest_snapshot
            views = snap.metrics.get("views").normalized_value if "views" in snap.metrics else 0.0
            eng_rate = snap.derived_metrics.get("engagement_rate").value if "engagement_rate" in snap.derived_metrics else 0.0

            # Classify into tier
            if views >= p66 and views > p33:
                tier = PerformanceTier.TOP
                note = f"Topic '{perf.topic}' was associated with stronger observed views ({int(views)} vs baseline {int(median_views)})."
            elif views <= p33:
                tier = PerformanceTier.LOW
                note = f"Topic '{perf.topic}' was associated with lower observed views ({int(views)} vs baseline {int(median_views)})."
            else:
                tier = PerformanceTier.AVERAGE
                note = f"Topic '{perf.topic}' performed in line with baseline observed views ({int(views)})."

            cid = perf.channel_id or channel_id or "default"
            sig_id = f"fb-{hashlib.sha256(f'{cid}:{perf.job_id}:{snap.snapshot_id}'.encode('utf-8')).hexdigest()[:10]}"
            sig = FeedbackSignal(
                signal_id=sig_id,
                job_id=perf.job_id,
                channel_id=cid,
                topic=perf.topic or "N/A",
                snapshot_id=snap.snapshot_id,
                observed_views=int(views),
                observed_engagement_rate=eng_rate,
                performance_tier=tier,
                association_note=note,
                recorded_at=now_iso,
            )
            self.db.record_feedback_observation(sig)
            signals.append(sig)

        return signals

    def analyze_learning_observations(
        self,
        signals: List[FeedbackSignal],
        min_sample_size: int = 3,
        channel_id: Optional[str] = None,
    ) -> List[LearningObservation]:
        """Synthesizes learning observations from feedback signals requiring minimum sample size."""
        now_iso = datetime.now(timezone.utc).isoformat()
        observations: List[LearningObservation] = []
        cid = channel_id or (signals[0].channel_id if signals else "default")

        # Filter by channel if provided
        chan_signals = [s for s in signals if s.channel_id == cid] if cid != "default" else signals

        # Group by topic keyword / category
        top_topics = [s for s in chan_signals if s.performance_tier == PerformanceTier.TOP]
        low_topics = [s for s in chan_signals if s.performance_tier == PerformanceTier.LOW]

        if len(top_topics) >= min_sample_size:
            obs_id = f"learn-{cid}-top-{now_iso[:10]}"
            obs = LearningObservation(
                observation_id=obs_id,
                source_job_ids=[s.job_id for s in top_topics],
                channel_id=cid,
                pattern_type="high_performing_cluster",
                observation_text=f"A sample of {len(top_topics)} topics for channel '{cid}' was associated with stronger observed performance (above median).",
                sample_size=len(top_topics),
                confidence=min(0.9, 0.5 + (len(top_topics) * 0.05)),
                recommended_adjustment={"niche_boost": 0.10},
                observed_at=now_iso,
            )
            observations.append(obs)

        if len(low_topics) >= min_sample_size:
            obs_id = f"learn-{cid}-low-{now_iso[:10]}"
            obs = LearningObservation(
                observation_id=obs_id,
                source_job_ids=[s.job_id for s in low_topics],
                channel_id=cid,
                pattern_type="low_performing_cluster",
                observation_text=f"A sample of {len(low_topics)} topics for channel '{cid}' was associated with lower observed performance (below median).",
                sample_size=len(low_topics),
                confidence=min(0.9, 0.5 + (len(low_topics) * 0.05)),
                recommended_adjustment={"cooldown_extension_days": 7},
                observed_at=now_iso,
            )
            observations.append(obs)

        return observations

    generate_learning_observations = analyze_learning_observations


class StrategyManager:
    """Manages versioned, rollback-capable generation strategies with channel-level binding."""

    def __init__(self, db: Optional[DBManager] = None):
        self.db = db or DBManager(CONFIG.db_path)
        self.db.init_schema()

    def get_active_strategy(self, channel_id: Optional[str] = None) -> StrategyVersion:
        """Retrieves active strategy for a specific channel or global default."""
        if channel_id and channel_id != "default":
            ch = self.db.get_channel_profile(channel_id)
            if ch and ch.get("active_strategy_version_id"):
                strat = self.db.get_strategy_version(ch["active_strategy_version_id"])
                if strat:
                    return strat
        return self.db.get_active_strategy()

    def propose_strategy_version(
        self,
        parent_version: StrategyVersion,
        observations: List[LearningObservation],
        rationale: str,
        channel_id: Optional[str] = None,
    ) -> Optional[StrategyVersion]:
        """Creates a new proposed strategy version based on evidence observations."""
        if not observations:
            return None

        prefix = f"strat-{channel_id}-v" if channel_id and channel_id != "default" else "strat-v"
        # Determine next version ID
        try:
            clean_id = parent_version.version_id.split("-v")[-1]
            curr_num = int(clean_id)
        except Exception:
            curr_num = 1
        new_version_id = f"{prefix}{curr_num + 1}"

        weights = dict(parent_version.niche_weights)
        for obs in observations:
            adj = obs.recommended_adjustment
            if "niche_boost" in adj:
                for k in weights:
                    weights[k] = round(min(1.5, weights[k] + adj["niche_boost"]), 2)
            if "niche_weight_boost" in adj:
                for k, v in adj["niche_weight_boost"].items():
                    weights[k] = round(min(1.5, weights.get(k, 1.0) + v), 2)

        new_strat = StrategyVersion(
            version_id=new_version_id,
            parent_version_id=parent_version.version_id,
            status=StrategyStatus.PROPOSED,
            niche_weights=weights,
            hook_patterns=list(parent_version.hook_patterns),
            topic_rules=dict(parent_version.topic_rules),
            supporting_evidence_ids=[obs.observation_id for obs in observations],
            rationale=rationale,
        )
        self.db.record_strategy_version(new_strat)
        return new_strat

    def activate_strategy(self, version_id: str, channel_id: Optional[str] = None) -> bool:
        """Activates a proposed or existing strategy version, binding to channel if specified."""
        if channel_id and channel_id != "default":
            ch = self.db.get_channel_profile(channel_id)
            if ch and ch.get("profile"):
                from autopilot.core.contracts import ChannelProfile
                profile = ChannelProfile.model_validate(ch["profile"])
                profile.active_strategy_version_id = version_id
                from autopilot.core.channel import ChannelManager
                cm = ChannelManager(self.db)
                cm.update_channel(profile, bump_version=True, change_summary=f"Activate strategy {version_id}")
                return True
        return self.db.set_active_strategy(version_id)

    def rollback(self, target_version_id: str, channel_id: Optional[str] = None) -> bool:
        """Rolls back the active strategy to a previous historical version."""
        return self.activate_strategy(target_version_id, channel_id=channel_id)

