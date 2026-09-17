"""Analytics-Driven Feedback & Strategy Learning — Milestone 11.

Closes the feedback loop:

    published/completed content
            ↓
        analytics (stored facts)
            ↓
      performance signals (normalized, scale-free)
            ↓
       bounded aggregation (by niche category)
            ↓
     bounded strategy update (niche_weights ONLY)
            ↓
       versioned strategy -> future ideation / scoring

ARCHITECTURE BOUNDARIES (enforced by construction and verified by test):
  * Learning NEVER imports or calls publisher, worker, pipeline, renderer, TTS,
    asset-engine, QA, or OAuth modules.  It reads stored analytics facts and
    writes strategy versions + learning-run records only.
  * Only ``StrategyVersion.niche_weights`` is tunable.  Safety policy, evidence
    requirements, publishing permissions and production settings are untouched.
  * A failed analytics sync is never treated as poor performance: learning
    consumes stable *stored* snapshots, not sync outcomes.
  * Learning is idempotent: identical analytics input cannot continuously mint
    new strategy versions.
"""
from __future__ import annotations

import hashlib
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from autopilot.core.config import Config, CONFIG
from autopilot.core.constants import (
    LEARNING_FORBIDDEN_MODULES,
)
from autopilot.core.contracts import (
    FeedbackSignal,
    LearningRunStatus,
    LearningRunSummary,
    PerformanceTier,
    StrategyDelta,
    StrategyVersion,
)
from autopilot.core.feedback import FeedbackAnalyzer, StrategyManager
from autopilot.db.manager import DBManager

# Explicit import allow-list: the learning engine may only depend on data/config
# layers, never on production or publishing machinery.  A test asserts this set.
_LEARNING_IMPORT_ALLOWLIST: Set[str] = {
    "autopilot.core.config",
    "autopilot.core.constants",
    "autopilot.core.contracts",
    "autopilot.core.feedback",
    "autopilot.core.logging",
    "autopilot.db.manager",
}

# View-count tier values for the robust (median-relative) performance signal.
_TIER_VALUE: Dict[str, float] = {
    PerformanceTier.TOP.value: 1.0,
    PerformanceTier.AVERAGE.value: 0.5,
    PerformanceTier.LOW.value: 0.0,
}

# Audience-performance learning only consumes genuinely published content.
PUBLISHED_STATUSES = ("SUCCESS", "PUBLISHED")


class LearningEngine:
    """Deterministic, bounded, explainable, channel-isolated strategy learning.

    A single entry point (``update_strategy``) implements the whole loop:
    eligibility -> normalization -> segmentation -> bounded update -> versioning
    -> idempotent persistence.  It publishes nothing and produces nothing.
    """

    def __init__(
        self,
        config: Optional[Config] = None,
        db: Optional[DBManager] = None,
        feedback_analyzer: Optional[FeedbackAnalyzer] = None,
        strategy_manager: Optional[StrategyManager] = None,
    ):
        self.config = config or CONFIG
        self.db = db or DBManager(self.config.db_path)
        self.db.init_schema()
        self.feedback_analyzer = feedback_analyzer or FeedbackAnalyzer(self.db)
        self.strategy_manager = strategy_manager or StrategyManager(self.db)

    # ------------------------------------------------------------------
    # Bounds (single source of truth = Config)
    # ------------------------------------------------------------------

    def _bounds(self) -> Dict[str, Any]:
        c = self.config
        return {
            "min_samples": int(c.learning_min_samples),
            "min_category_observations": int(c.learning_min_category_observations),
            "window_days": int(c.learning_window_days),
            "recency_weight": float(c.learning_recency_weight),
            "full_confidence_samples": int(c.learning_full_confidence_samples),
            "max_weight_delta": float(c.strategy_max_weight_delta),
            "max_params_per_update": int(c.strategy_max_params_per_update),
            "weight_floor": float(c.strategy_weight_floor),
            "weight_ceiling": float(c.strategy_weight_ceiling),
            "min_age_days": int(c.strategy_min_age_days),
        }

    # ------------------------------------------------------------------
    # Eligibility & normalization
    # ------------------------------------------------------------------

    def _content_age_days(self, published_at: Optional[str], now: datetime) -> float:
        if not published_at:
            return 0.0
        try:
            dt = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0.0, (now - dt).total_seconds() / 86400.0)
        except Exception:
            return 0.0

    def collect_observations(
        self,
        channel_id: str = "default",
        window_days: Optional[int] = None,
        min_age_days: Optional[int] = None,
        exclude_job_ids: Optional[Set[str]] = None,
    ) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
        """Enumerate valid, channel-isolated audience observations.

        Returns (observations, exclusion_reasons).  Every observation carries
        full lineage (job_id, video_id, snapshot_id, channel, topic, category,
        category_source, signals).  Invalid data is excluded with a reason and
        NEVER converted into a guessed metric.
        """
        b = self._bounds()
        window_days = int(window_days) if window_days is not None else b["window_days"]
        min_age_days = int(min_age_days) if min_age_days is not None else b["min_age_days"]
        now = datetime.now(timezone.utc)
        since_iso = (now - timedelta(days=window_days)).isoformat() if window_days > 0 else None

        excluded: Dict[str, int] = {}
        exclude_job_ids = set(exclude_job_ids or [])

        candidates = self.db.list_published_jobs(
            channel_id=channel_id,
            since_iso=since_iso,
            published_statuses=PUBLISHED_STATUSES,
        )

        observations: List[Dict[str, Any]] = []
        for row in candidates:
            job_id = row["job_id"]
            if job_id in exclude_job_ids:
                excluded["same_operation_run"] = excluded.get("same_operation_run", 0) + 1
                continue

            age_days = self._content_age_days(row.get("published_at"), now)
            if age_days < min_age_days:
                excluded["too_recent"] = excluded.get("too_recent", 0) + 1
                continue

            # Stable stored performance facts (a failed sync yields no snapshot
            # and is excluded here as 'no_analytics' — never as poor performance).
            perf = self.db.get_content_performance(job_id)
            if not perf or not perf.latest_snapshot:
                excluded["no_analytics"] = excluded.get("no_analytics", 0) + 1
                continue

            snap = perf.latest_snapshot
            views = float(snap.metrics["views"].normalized_value) if "views" in snap.metrics else 0.0
            likes = float(snap.metrics["likes"].normalized_value) if "likes" in snap.metrics else 0.0
            comments = float(snap.metrics["comments"].normalized_value) if "comments" in snap.metrics else 0.0
            shares = float(snap.metrics["shares"].normalized_value) if "shares" in snap.metrics else 0.0

            # Zero impressions/views => intentionally limited metrics; skip.
            if views <= 0:
                excluded["zero_views"] = excluded.get("zero_views", 0) + 1
                continue

            # Normalized, scale-free signals (documented formulas).
            engagement_rate = round((likes + comments + shares) / views, 6)
            denom = max(age_days, 1.0)
            views_per_day = round(views / denom, 4)

            category, category_source = self.db.resolve_job_category(job_id)
            if category is None:
                # Unresolved is explicit: bucketed as 'general' and flagged so it
                # can never silently impersonate a real niche category.
                category, category_source = "general", "unresolved"
                excluded["unresolved_category"] = excluded.get("unresolved_category", 0) + 1

            observations.append(
                {
                    "job_id": job_id,
                    "video_id": perf.remote_id or row.get("remote_video_id"),
                    "snapshot_id": snap.snapshot_id,
                    "channel_id": channel_id,
                    "topic": perf.topic or row.get("topic") or "",
                    "category": category,
                    "category_source": category_source,
                    "views": views,
                    "engagement_rate": engagement_rate,
                    "views_per_day": views_per_day,
                    "age_days": round(age_days, 3),
                    "is_synthetic": bool(snap.is_synthetic),
                    "published_at": row.get("published_at"),
                }
            )

        return observations, excluded

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    def compute_tiers(self, observations: List[Dict[str, Any]]) -> Dict[str, PerformanceTier]:
        """Median-relative tier classification (outlier-damped by construction)."""
        thresholds = FeedbackAnalyzer.compute_view_thresholds([o["views"] for o in observations])
        return {
            o["job_id"]: FeedbackAnalyzer.classify_tier(o["views"], thresholds)
            for o in observations
        }

    def _recency_weight(self, age_days: float, window_days: int, recency_weight: float) -> float:
        """Bounded linear recency weight in [1-recency_weight, 1.0]."""
        if window_days <= 0 or recency_weight <= 0:
            return 1.0
        frac = max(0.0, min(1.0, age_days / float(window_days)))
        return (1.0 - recency_weight) + (recency_weight * (1.0 - frac))

    def aggregate_category_signals(
        self,
        observations: List[Dict[str, Any]],
        tiers: Dict[str, PerformanceTier],
        bounds: Dict[str, Any],
    ) -> Dict[str, Dict[str, Any]]:
        """Recency-weighted per-category performance signals.

        signal in [-1, +1]: 0 = channel-neutral, +1 = consistently top, -1 = consistently low.
        Confidence scales with sample size (damped for small samples).
        """
        window_days = int(bounds["window_days"])
        recency_w = float(bounds["recency_weight"])
        full_conf = max(1, int(bounds["full_confidence_samples"]))

        by_cat: Dict[str, List[Dict[str, Any]]] = {}
        for o in observations:
            by_cat.setdefault(o["category"], []).append(o)

        signals: Dict[str, Dict[str, Any]] = {}
        for cat, items in by_cat.items():
            w_sum = 0.0
            w_score = 0.0
            for o in items:
                w = self._recency_weight(float(o["age_days"]), window_days, recency_w)
                tier_val = _TIER_VALUE.get(tiers.get(o["job_id"], PerformanceTier.AVERAGE).value, 0.5)
                w_sum += w
                w_score += w * tier_val
            tier_score = (w_score / w_sum) if w_sum > 0 else 0.5
            n = len(items)
            confidence = min(1.0, n / float(full_conf))
            signal = (tier_score - 0.5) * 2.0
            signals[cat] = {
                "category": cat,
                "sample_size": n,
                "tier_score": round(tier_score, 4),
                "signal": round(max(-1.0, min(1.0, signal)), 4),
                "confidence": round(confidence, 4),
                "avg_engagement_rate": round(sum(i["engagement_rate"] for i in items) / n, 6),
                "avg_views_per_day": round(sum(i["views_per_day"] for i in items) / n, 4),
                "source_job_ids": [i["job_id"] for i in items],
                "synthetic_count": sum(1 for i in items if i.get("is_synthetic")),
            }
        return signals

    # ------------------------------------------------------------------
    # Bounded update
    # ------------------------------------------------------------------

    def compute_bounded_deltas(
        self,
        parent: StrategyVersion,
        category_signals: Dict[str, Dict[str, Any]],
        bounds: Dict[str, Any],
    ) -> List[StrategyDelta]:
        """Select the top-K most-evidenced category deltas, each capped.

        Guarantees:
          * categories below ``min_category_observations`` are never adjusted;
          * at most ``max_params_per_update`` parameters change per run;
          * each applied delta is capped to +- ``max_weight_delta``;
          * final weights are floored at ``weight_floor`` so no category can
            collapse to zero influence (exploration is always retained).
        """
        min_cat_obs = int(bounds["min_category_observations"])
        max_delta = float(bounds["max_weight_delta"])
        max_params = int(bounds["max_params_per_update"])
        floor = float(bounds["weight_floor"])
        ceiling = float(bounds["weight_ceiling"])

        eligible = [
            (cat, sig)
            for cat, sig in category_signals.items()
            if int(sig["sample_size"]) >= min_cat_obs and abs(sig["signal"]) > 1e-9
        ]
        # Rank by magnitude of evidence-backed signal (strongest first), then
        # by sample size as a deterministic tie-break.
        eligible.sort(key=lambda kv: (abs(kv[1]["signal"]), kv[1]["sample_size"]), reverse=True)

        deltas: List[StrategyDelta] = []
        for cat, sig in eligible[:max_params]:
            base = float(parent.niche_weights.get(cat, 1.0))
            raw = sig["signal"] * max_delta * sig["confidence"]
            applied = round(max(-max_delta, min(max_delta, raw)), 4)
            new_val = round(min(ceiling, max(floor, base + applied)), 4)
            if abs(new_val - base) < 1e-9:
                continue  # already at a bound; no-op change
            deltas.append(
                StrategyDelta(
                    parameter=cat,
                    old_value=base,
                    new_value=new_val,
                    raw_delta=round(raw, 4),
                    applied_delta=applied,
                    sample_size=int(sig["sample_size"]),
                    confidence=float(sig["confidence"]),
                    signal=float(sig["signal"]),
                    source_job_ids=list(sig["source_job_ids"]),
                    reason=(
                        f"Category '{cat}' tier_score={sig['tier_score']:.2f} "
                        f"(signal {sig['signal']:+.2f}, confidence {sig['confidence']:.2f}, "
                        f"n={sig['sample_size']}) -> weight {base:.3f}->{new_val:.3f}"
                    ),
                )
            )
        return deltas

    # ------------------------------------------------------------------
    # Idempotency
    # ------------------------------------------------------------------

    def _input_fingerprint(self, channel_id: str, observations: List[Dict[str, Any]], bounds: Dict[str, Any]) -> str:
        """Deterministic fingerprint of the learning inputs.

        Keyed on the exact evidence set (job + category + views + engagement),
        the channel, and the active bounds.  Re-aggregating the same analytics
        under the same configuration yields the same fingerprint.
        """
        payload = [
            f"{o['job_id']}:{o['category']}:{o['views']}:{o['engagement_rate']}:{o['age_days']}"
            for o in sorted(observations, key=lambda x: x["job_id"])
        ]
        h = hashlib.sha256()
        h.update(channel_id.encode("utf-8"))
        h.update("|".join(payload).encode("utf-8"))
        h.update(repr(sorted((k, v) for k, v in bounds.items())).encode("utf-8"))
        return h.hexdigest()

    def _already_applied(self, channel_id: str, fingerprint: str, active: StrategyVersion) -> bool:
        """True iff this exact evidence already produced a strategy version that
        lies in the ancestry of the currently active strategy.

        This is the restart/repeat-safety check: after a completed run (or a
        restart), the same data will not mint a new version.  A rollback breaks
        ancestry, so re-applying known learning is legitimately allowed.
        """
        prior = self.db.find_applied_learning_run(channel_id, fingerprint)
        if not prior or not prior.get("resulting_strategy_version"):
            return False
        chain = self.db.get_strategy_ancestor_chain(active.version_id)
        return prior["resulting_strategy_version"] in chain

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def update_strategy(
        self,
        channel_id: str = "default",
        dry_run: bool = False,
        min_samples: Optional[int] = None,
        window_days: Optional[int] = None,
        min_age_days: Optional[int] = None,
        exclude_job_ids: Optional[Set[str]] = None,
    ) -> LearningRunSummary:
        """One deterministic, idempotent, bounded learning cycle.

        Never publishes, never triggers production, never touches policy.
        """
        run_id = (
            f"learn-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-"
            f"{hashlib.sha256(channel_id.encode()).hexdigest()[:6]}-"
            f"{uuid.uuid4().hex[:6]}"
        )
        now_iso = datetime.now(timezone.utc).isoformat()
        bounds = self._bounds()
        min_samples = int(min_samples) if min_samples is not None else bounds["min_samples"]

        active = self.strategy_manager.get_active_strategy(channel_id=channel_id)
        summary = LearningRunSummary(
            run_id=run_id,
            channel_id=channel_id,
            dry_run=dry_run,
            window_days=int(window_days) if window_days is not None else bounds["window_days"],
            parent_strategy_version=active.version_id,
            started_at=now_iso,
        )

        try:
            # 1. Eligible observations (channel-isolated, actually published).
            observations, excluded = self.collect_observations(
                channel_id=channel_id,
                window_days=window_days,
                min_age_days=min_age_days,
                exclude_job_ids=exclude_job_ids,
            )
            summary.observations_considered = len(observations) + sum(excluded.values())
            summary.observations_used = len(observations)
            summary.observations_excluded = sum(excluded.values())
            summary.excluded_reasons = excluded
            summary.is_synthetic_input = any(o.get("is_synthetic") for o in observations)

            # 2. Minimum evidence gate.
            if len(observations) < min_samples:
                summary.status = LearningRunStatus.INSUFFICIENT.value
                summary.reason = (
                    f"Insufficient audience evidence: {len(observations)} valid observations "
                    f"(< required {min_samples}). No strategy change."
                )
                summary.completed_at = datetime.now(timezone.utc).isoformat()
                if not dry_run:
                    self.db.record_learning_run(summary)
                return summary

            # 3. Normalized signals + segmentation.
            tiers = self.compute_tiers(observations)
            category_signals = self.aggregate_category_signals(observations, tiers, bounds)
            summary.category_signals = {
                cat: {k: v for k, v in sig.items() if k != "source_job_ids"}
                for cat, sig in category_signals.items()
            }
            summary.signals_summary = {
                "tier_counts": {
                    t.value: sum(1 for jid in tiers if tiers[jid] == t)
                    for t in PerformanceTier
                },
                "categories": sorted(category_signals.keys()),
                "bounds": bounds,
            }
            summary.observation_ids = [o["job_id"] for o in observations]

            # 4. Idempotency: same evidence already incorporated?
            fingerprint = self._input_fingerprint(channel_id, observations, bounds)
            summary.input_fingerprint = fingerprint
            if not dry_run and self._already_applied(channel_id, fingerprint, active):
                summary.status = LearningRunStatus.NO_CHANGE.value
                summary.reason = (
                    "Identical analytics evidence already applied to the active strategy "
                    f"lineage (fingerprint {fingerprint[:12]}...). No new version."
                )
                summary.completed_at = datetime.now(timezone.utc).isoformat()
                self.db.record_learning_run(summary)
                return summary

            # 5. Bounded deltas (niche_weights only).
            deltas = self.compute_bounded_deltas(active, category_signals, bounds)
            summary.deltas = deltas

            if not deltas:
                summary.status = LearningRunStatus.NO_CHANGE.value
                summary.reason = (
                    "Evidence sufficient but no category met the adjustment threshold "
                    "(min category observations / bounded delta). No strategy change."
                )
                summary.completed_at = datetime.now(timezone.utc).isoformat()
                if not dry_run:
                    self.db.record_learning_run(summary)
                return summary

            # 6. Versioned strategy update.
            rationale = (
                f"Learning run {run_id} for channel '{channel_id}': "
                f"{len(observations)} published observations, {len(deltas)} bounded "
                f"niche_weight adjustments. Evidence fingerprint {fingerprint[:12]}...."
            )
            if dry_run:
                # Propose only; persist nothing (no strategy version, no applied
                # learning run).  The proposal is still recorded as a dry run so
                # operators can inspect it without mutating strategy.
                summary.status = LearningRunStatus.DRY_RUN.value
                summary.reason = f"DRY RUN: would create strategy version from {len(deltas)} bounded deltas."
                summary.completed_at = datetime.now(timezone.utc).isoformat()
                self.db.record_learning_run(summary)
                return summary

            new_strat = self.strategy_manager.propose_bounded_strategy_version(
                parent_version=active,
                deltas=deltas,
                rationale=rationale,
                channel_id=channel_id,
                weight_floor=bounds["weight_floor"],
                weight_ceiling=bounds["weight_ceiling"],
            )
            if not new_strat:
                summary.status = LearningRunStatus.NO_CHANGE.value
                summary.reason = "Strategy proposal produced no new version."
                summary.completed_at = datetime.now(timezone.utc).isoformat()
                self.db.record_learning_run(summary)
                return summary

            activated = self.strategy_manager.activate_strategy(new_strat.version_id, channel_id=channel_id)
            summary.resulting_strategy_version = new_strat.version_id
            summary.status = LearningRunStatus.APPLIED.value if activated else LearningRunStatus.NO_CHANGE.value
            summary.reason = (
                f"Applied bounded strategy update {active.version_id} -> {new_strat.version_id} "
                f"({len(deltas)} parameter(s): {', '.join(d.parameter for d in deltas)})."
            )
            summary.completed_at = datetime.now(timezone.utc).isoformat()
            self.db.record_learning_run(summary)
            return summary

        except Exception as exc:  # pragma: no cover - defensive
            summary.status = LearningRunStatus.FAILED.value
            summary.error_message = str(exc)[:500]
            summary.reason = f"Learning run failed: {str(exc)[:200]}"
            summary.completed_at = datetime.now(timezone.utc).isoformat()
            try:
                if not dry_run:
                    self.db.record_learning_run(summary)
            except Exception:
                pass
            return summary
