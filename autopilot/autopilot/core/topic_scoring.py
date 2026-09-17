"""Topic Scoring Engine — Milestone 9.
Computes explainable, multi-factor decision-support heuristics for TopicCandidates.
Clearly demarcated as a decision heuristic rather than a virality prediction.
"""
from __future__ import annotations
import hashlib
from datetime import datetime, timezone
from typing import Dict, List, Optional

from autopilot.core.contracts import TopicCandidate, TopicScore, TrendSignal, FeedbackSignal, PerformanceTier, StrategyVersion


class TopicScorer:
    """Calculates deterministic multi-factor scores for TopicCandidates."""

    def __init__(
        self,
        weight_freshness: float = 0.25,
        weight_relevance: float = 0.25,
        weight_historical: float = 0.25,
        weight_novelty: float = 0.25,
        penalty_duplicate: float = 0.35,
        strategy_influence_scale: float = 0.25,
    ):
        self.w_fresh = weight_freshness
        self.w_rel = weight_relevance
        self.w_hist = weight_historical
        self.w_nov = weight_novelty
        self.p_dup = penalty_duplicate
        self.strategy_scale = float(strategy_influence_scale)

    @staticmethod
    def compute_strategy_bonus(
        candidate: TopicCandidate,
        strategy: Optional[StrategyVersion],
        scale: float = 0.25,
    ) -> tuple[float, Optional[str]]:
        """Explicit, bounded adjustment from the active strategy.

        ``bonus = (niche_weight[candidate.category] - 1.0) * scale``

        The (weight - 1.0) term is *excess* preference only: a neutral strategy
        (all weights 1.0) contributes exactly 0.  Strategy weights are bounded to
        [strategy_weight_floor, strategy_weight_ceiling] by the learning layer,
        so the bonus is bounded to roughly +-0.5*scale and can never dominate the
        base multi-factor score.  Returns (bonus, strategy_version).
        """
        if not strategy:
            return 0.0, None
        category = (candidate.category or "").strip().lower()
        if not category:
            return 0.0, strategy.version_id
        weight = float(strategy.niche_weights.get(category, 1.0))
        bonus = round((weight - 1.0) * float(scale), 4)
        return bonus, strategy.version_id

    def calculate_historical_factor(
        self,
        candidate: TopicCandidate,
        feedback_signals: List[FeedbackSignal],
    ) -> float:
        """Computes associational historical performance factor with outlier damping."""
        if not feedback_signals:
            return 0.5  # Neutral baseline when no historical data exists

        # Look for tokens in common
        cand_tokens = set(candidate.proposed_topic.lower().split())
        matched_signals = []
        for sig in feedback_signals:
            sig_tokens = set(sig.topic.lower().split())
            if cand_tokens & sig_tokens:
                matched_signals.append(sig)

        if not matched_signals:
            return 0.5

        # Aggregate tier scores with sample size damping
        tier_values = {
            PerformanceTier.TOP: 0.85,
            PerformanceTier.AVERAGE: 0.50,
            PerformanceTier.LOW: 0.25,
        }

        sample_size = len(matched_signals)
        raw_avg = sum(tier_values.get(s.performance_tier, 0.50) for s in matched_signals) / sample_size

        # Damping towards neutral 0.5 when sample size is small (< 3)
        confidence_weight = min(1.0, sample_size / 3.0)
        damped_factor = round((raw_avg * confidence_weight) + (0.50 * (1.0 - confidence_weight)), 3)
        return damped_factor

    def score_candidate(
        self,
        candidate: TopicCandidate,
        signal: Optional[TrendSignal] = None,
        feedback_signals: Optional[List[FeedbackSignal]] = None,
        supporting_signals: Optional[List[TrendSignal]] = None,
        strategy: Optional[StrategyVersion] = None,
    ) -> TopicScore:
        """Scores a TopicCandidate deterministically and produces an explainable breakdown."""
        now_iso = datetime.now(timezone.utc).isoformat()
        feedback = feedback_signals or []
        sig = signal or (supporting_signals[0] if supporting_signals else None)

        freshness = sig.freshness_score if sig else 0.70
        relevance = candidate.commercial_relevance
        historical_factor = self.calculate_historical_factor(candidate, feedback)
        novelty = max(0.0, 1.0 - candidate.duplicate_risk)
        effort_factor = max(0.0, 1.0 - (candidate.estimated_effort - 1) / 4.0)
        dup_penalty = candidate.duplicate_risk * self.p_dup

        # Explicit, bounded, auditable strategy influence.  This is the ONLY
        # place learned strategy enters the score, so it is always visible in
        # the breakdown rather than hidden inside a heuristic.
        strategy_bonus, strategy_version = self.compute_strategy_bonus(
            candidate, strategy, scale=self.strategy_scale
        )

        weighted_sum = (
            (self.w_fresh * freshness)
            + (self.w_rel * relevance)
            + (self.w_hist * historical_factor)
            + (self.w_nov * novelty)
            - dup_penalty
            + strategy_bonus
        )
        total = round(max(0.0, min(1.0, weighted_sum)), 3)

        score_id = f"score-{hashlib.sha256(f'{candidate.candidate_id}:{total}'.encode('utf-8')).hexdigest()[:10]}"

        breakdown = {
            "freshness": round(freshness, 3),
            "relevance": round(relevance, 3),
            "historical_performance_factor": round(historical_factor, 3),
            "content_novelty": round(novelty, 3),
            "production_effort_factor": round(effort_factor, 3),
            "duplicate_risk_penalty": round(dup_penalty, 3),
            "strategy_bonus": round(strategy_bonus, 4),
            "strategy_version": strategy_version,
            "weight_freshness": self.w_fresh,
            "weight_relevance": self.w_rel,
            "weight_historical": self.w_hist,
            "weight_novelty": self.w_nov,
        }

        explanation = (
            f"Score {total:.2f}: Freshness={freshness:.2f}, Relevance={relevance:.2f}, "
            f"Novelty={novelty:.2f}, HistoricalAssociation={historical_factor:.2f}, "
            f"DuplicatePenalty={dup_penalty:.2f}, StrategyBonus={strategy_bonus:+.3f}"
            f"{' (strategy ' + strategy_version + ')' if strategy_version else ''}."
        )

        return TopicScore(
            score_id=score_id,
            candidate_id=candidate.candidate_id,
            freshness=freshness,
            relevance=relevance,
            historical_performance_factor=historical_factor,
            content_novelty=novelty,
            production_effort_factor=effort_factor,
            duplicate_risk_penalty=dup_penalty,
            strategy_bonus=strategy_bonus,
            strategy_version=strategy_version,
            total_score=total,
            breakdown=breakdown,
            explanation=explanation,
            calculated_at=now_iso,
        )
