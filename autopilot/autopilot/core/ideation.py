"""Ideation & Diversity Engine — Milestone 9.
Transforms trend signals and strategy rules into concrete TopicCandidates.
Applies deterministic diversity controls and topic deduplication.
"""
from __future__ import annotations
import re
import hashlib
from typing import List, Tuple, Optional, Set
from datetime import datetime, timezone

from autopilot.core.contracts import TrendSignal, TopicCandidate, StrategyVersion, ChannelProfile


STOP_WORDS: Set[str] = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "up", "about", "into", "over", "after",
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "how", "why", "what", "when", "where", "who", "which",
}


class DiversityFilter:
    """Deterministic topic similarity and cooldown filter."""

    @staticmethod
    def tokenize(text: str) -> Set[str]:
        """Extracts normalized significant tokens from a topic string."""
        clean = re.sub(r"[^a-zA-Z0-9\s]", " ", text.lower())
        tokens = {t.strip() for t in clean.split() if len(t.strip()) > 2}
        return tokens - STOP_WORDS

    @classmethod
    def calculate_similarity(cls, topic_a: str, topic_b: str) -> float:
        """Computes token Jaccard similarity between two topic strings."""
        if not topic_a or not topic_b:
            return 0.0
        if topic_a.strip().lower() == topic_b.strip().lower():
            return 1.0

        tok_a = cls.tokenize(topic_a)
        tok_b = cls.tokenize(topic_b)

        if not tok_a or not tok_b:
            return 0.0

        intersection = len(tok_a & tok_b)
        union = len(tok_a | tok_b)
        return round(intersection / union, 4) if union > 0 else 0.0

    @classmethod
    def check_duplicate_risk(
        cls,
        proposed_topic: str,
        recent_topics: List[str],
    ) -> Tuple[float, Optional[str]]:
        """Finds maximum similarity score against a list of recent topics."""
        max_sim = 0.0
        most_similar = None
        for prev in recent_topics:
            sim = cls.calculate_similarity(proposed_topic, prev)
            if sim > max_sim:
                max_sim = sim
                most_similar = prev
        return max_sim, most_similar

    @classmethod
    def calculate_duplicate_risk(cls, proposed_topic: str, recent_topics: List[str]) -> float:
        risk, _ = cls.check_duplicate_risk(proposed_topic, recent_topics)
        return risk


class IdeationEngine:
    """Generates TopicCandidates from TrendSignals guided by active StrategyVersion."""

    def __init__(self, db: Optional[Any] = None, diversity_filter: Optional[DiversityFilter] = None):
        self.db = db
        self.diversity = diversity_filter or DiversityFilter()

    def generate_candidates(
        self,
        signals: List[TrendSignal],
        strategy: Optional[StrategyVersion] = None,
        recent_topics: Optional[List[str]] = None,
        run_id: str = "default_run",
        profile: str = "short_vertical",
        limit: int = 10,
        existing_topics: Optional[List[str]] = None,
        channel: Optional[ChannelProfile] = None,
        channel_profile: Optional[ChannelProfile] = None,
    ) -> List[TopicCandidate]:
        """Formulates and diversifies topic candidates from input trend signals, guided by channel profile."""
        channel = channel or channel_profile
        strat = strategy or StrategyVersion(version_id="strat-v1")
        prior_topics = existing_topics if existing_topics is not None else (recent_topics or [])
        candidates: List[TopicCandidate] = []
        seen_in_cycle: List[str] = list(prior_topics)
        now_iso = datetime.now(timezone.utc).isoformat()
        cid = channel.channel_id if channel else "default"

        # Extract patterns and weights from strategy
        patterns = strat.hook_patterns or [
            "Did you know {fact}? Here is what happens next.",
            "The truth behind {topic} will shock you.",
        ]
        niche_weights = strat.niche_weights or {}

        for idx, sig in enumerate(signals):
            if len(candidates) >= limit:
                break

            topic_text = sig.topic.strip()
            cat = sig.category.lower().strip()
            niche_weight = niche_weights.get(cat, 0.7)

            # Channel niche filtering
            if channel and channel.niche:
                allowed = [c.lower() for c in channel.niche.allowed_categories]
                excluded = [c.lower() for c in channel.niche.excluded_categories]
                if excluded and cat in excluded:
                    continue
                if allowed and cat not in allowed and "general" not in allowed:
                    continue
                if channel.niche.ideation_weighting and cat in channel.niche.ideation_weighting:
                    niche_weight = channel.niche.ideation_weighting[cat]

            # Persona & niche-driven angle transformation
            persona_tone = channel.persona.tone.lower() if channel and channel.persona else "informative"
            niche_name = channel.niche.niche_name.lower() if channel and channel.niche else "general"

            if niche_name == "technology" or cat == "technology":
                if persona_tone == "authoritative":
                    angle = "Architectural Deep Dive & Enterprise Impact"
                    title = f"Inside {topic_text}: Technical Breakdown"
                else:
                    angle = "The Engineering & Security Paradigm Shift"
                    title = f"The Truth Behind {topic_text}"
                hook_tmpl = patterns[idx % len(patterns)]
                hook = hook_tmpl.format(fact=sig.evidence_text[:60], topic=topic_text)
            elif niche_name == "science" or cat == "science":
                if persona_tone == "dramatic":
                    angle = "Cataclysmic Discovery That Defies Known Physics"
                    title = f"The {topic_text} Mystery Scientists Cannot Explain"
                else:
                    angle = "Breakthrough Scientific Discovery Explained"
                    title = f"How {topic_text} Changes Science"
                hook_tmpl = patterns[(idx + 1) % len(patterns)]
                hook = hook_tmpl.format(fact=sig.evidence_text[:60], topic=topic_text)
            elif niche_name == "history" or cat == "history":
                if persona_tone == "intriguing":
                    angle = "Classified Historical Secrets Unearthed"
                    title = f"What They Hid About {topic_text}"
                else:
                    angle = "Untold Ancient Secret & Archaeological Evidence"
                    title = f"The Lost History of {topic_text}"
                hook_tmpl = patterns[(idx + 2) % len(patterns)]
                hook = hook_tmpl.format(fact=sig.evidence_text[:60], topic=topic_text)
            elif niche_name == "finance" or cat == "finance" or niche_name == "business":
                angle = "Macroeconomic Implication & Market Impact"
                hook_tmpl = patterns[(idx + 3) % len(patterns)]
                hook = hook_tmpl.format(fact=sig.evidence_text[:60], topic=topic_text)
                title = f"What {topic_text} Means For You"
            else:
                angle = f"Key Insights & Evidence Analysis ({persona_tone.capitalize()})"
                hook = f"Here is why {topic_text} matters right now."
                title = f"Understanding {topic_text}"

            # If already generated in this cycle with high similarity, consolidate into existing candidate
            matching_cand = None
            for ec in candidates:
                if ec.proposed_topic == title or self.diversity.calculate_similarity(title, ec.proposed_topic) >= 0.80:
                    matching_cand = ec
                    break

            if matching_cand:
                if sig.signal_id not in matching_cand.supporting_signal_ids:
                    matching_cand.supporting_signal_ids.append(sig.signal_id)
                continue

            # Calculate duplicate risk against recent history and this cycle
            dup_risk, most_sim = self.diversity.check_duplicate_risk(title, seen_in_cycle)

            candidate_hash = hashlib.sha256(f"{run_id}:{cid}:{title}".encode("utf-8")).hexdigest()[:12]
            cand_id = f"tc-{candidate_hash}"

            cand = TopicCandidate(
                candidate_id=cand_id,
                run_id=run_id,
                channel_id=cid,
                proposed_topic=title,
                angle=angle,
                hook_hypothesis=hook,
                content_format=profile,
                rationale=f"Derived for channel '{cid}' from trend '{sig.topic}' in '{cat}' with freshness {sig.freshness_score:.2f}.",
                supporting_signal_ids=[sig.signal_id],
                confidence=round(sig.confidence * (1.0 - (dup_risk * 0.4)), 3),
                estimated_effort=2,
                duplicate_risk=round(dup_risk, 3),
                commercial_relevance=round(min(1.0, max(0.0, niche_weight)), 2),
                created_at=now_iso,
            )

            candidates.append(cand)
            seen_in_cycle.append(title)

        return candidates
