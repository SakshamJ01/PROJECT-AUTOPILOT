"""Autonomy Orchestrator & Governance Engine — Milestone 9.
Orchestrates trend discovery, ideation, scoring, diversity gating, policy evaluation,
M7 queue integration, and M8 analytics feedback into an autonomous loop.
"""
from __future__ import annotations
import uuid
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from autopilot.core.config import Config, CONFIG
from autopilot.core.logging import StructuredLogger
from autopilot.core.contracts import (
    AutonomyLevel,
    ProposalStatus,
    DecisionAction,
    TrendSignal,
    TopicCandidate,
    TopicScore,
    AutonomyPolicy,
    PolicyCheckResult,
    IdeaDecision,
    IdeaProposal,
    FeedbackSignal,
    StrategyVersion,
    AutonomyCycleSummary,
    AutoProduceSummary,
    ChannelProfile,
    ChannelStatus,
)
from autopilot.db.manager import DBManager
from autopilot.providers.contracts import TrendProvider, REGISTRY
from autopilot.providers.mock_trend import MockTrendProvider
from autopilot.core.ideation import IdeationEngine, DiversityFilter
from autopilot.core.topic_scoring import TopicScorer
from autopilot.core.feedback import FeedbackAnalyzer, StrategyManager
from autopilot.core.channel import ChannelManager
from autopilot.core.auto_publish import effective_auto_publish


class PolicyGate:
    """Evaluates candidates against configured quality, risk, budget, and quota limits."""

    def __init__(self, policy: Optional[AutonomyPolicy] = None):
        self.policy = policy or AutonomyPolicy()

    def evaluate_candidate(
        self,
        candidate: TopicCandidate,
        score: TopicScore,
        autonomy_level: int = 2,
        recent_topics: Optional[List[str]] = None,
        queue_pending_count: int = 0,
        daily_job_count: int = 0,
    ) -> IdeaDecision:
        """Convenience method matching test interface for evaluating candidates."""
        if recent_topics:
            sim = max(
                (DiversityFilter.calculate_similarity(candidate.proposed_topic, t) for t in recent_topics),
                default=0.0,
            )
            candidate.duplicate_risk = max(candidate.duplicate_risk, sim)
        return self.evaluate(
            candidate=candidate,
            score=score,
            active_queued_count=queue_pending_count,
            queued_today_count=daily_job_count,
            autonomy_level=autonomy_level,
        )

    def evaluate(
        self,
        candidate: TopicCandidate,
        score: TopicScore,
        active_queued_count: int = 0,
        queued_today_count: int = 0,
        autonomy_level: int = 2,
    ) -> IdeaDecision:
        """Evaluates all safety, policy, quota, and risk rules for a candidate."""
        now_iso = datetime.now(timezone.utc).isoformat()
        checks: List[PolicyCheckResult] = []
        passed_all = True
        primary_reason = "Passed all autonomy policy checks"

        # 1. Content Safety & Prohibited Keywords
        topic_lower = candidate.proposed_topic.lower()
        hook_lower = candidate.hook_hypothesis.lower()
        prohibited_match = None
        for bad_word in self.policy.prohibited_topics:
            if bad_word in topic_lower or bad_word in hook_lower:
                prohibited_match = bad_word
                break

        if prohibited_match:
            passed_all = False
            primary_reason = f"Prohibited content keyword detected: '{prohibited_match}'"
            checks.append(PolicyCheckResult(check_name="content_safety", passed=False, reason=primary_reason))
        else:
            checks.append(PolicyCheckResult(check_name="content_safety", passed=True, reason="No prohibited keywords"))

        # 2. Malformed Topic Check
        words = candidate.proposed_topic.strip().split()
        if not candidate.proposed_topic.strip() or len(words) > 25:
            passed_all = False
            primary_reason = f"Malformed topic length ({len(words)} words)"
            checks.append(PolicyCheckResult(check_name="malformed_topic", passed=False, reason=primary_reason))
        else:
            checks.append(PolicyCheckResult(check_name="malformed_topic", passed=True, reason="Valid title structure"))

        # 3. Evidence Requirement
        if self.policy.require_evidence and not candidate.supporting_signal_ids:
            passed_all = False
            primary_reason = "Missing verifiable supporting trend signal evidence"
            checks.append(PolicyCheckResult(check_name="evidence_requirement", passed=False, reason=primary_reason))
        else:
            checks.append(PolicyCheckResult(check_name="evidence_requirement", passed=True, reason="Supporting evidence present"))

        # 4. Cooldown & Duplicate Check
        if candidate.duplicate_risk > self.policy.similarity_threshold:
            passed_all = False
            primary_reason = f"Duplicate risk {candidate.duplicate_risk:.2f} exceeds threshold {self.policy.similarity_threshold:.2f}"
            checks.append(PolicyCheckResult(check_name="duplicate_cooldown", passed=False, reason=primary_reason))
        else:
            checks.append(PolicyCheckResult(check_name="duplicate_cooldown", passed=True, reason="Within novelty threshold"))

        # 5. Minimum Score Threshold
        if score.total_score < self.policy.min_score_threshold:
            passed_all = False
            primary_reason = f"Score {score.total_score:.2f} below threshold {self.policy.min_score_threshold:.2f}"
            checks.append(PolicyCheckResult(check_name="minimum_score", passed=False, reason=primary_reason))
        else:
            checks.append(PolicyCheckResult(check_name="minimum_score", passed=True, reason=f"Score {score.total_score:.2f} passes"))

        # 6. Queue Capacity Check
        if active_queued_count >= self.policy.max_concurrent_jobs:
            passed_all = False
            primary_reason = f"Queue capacity reached ({active_queued_count}/{self.policy.max_concurrent_jobs})"
            checks.append(PolicyCheckResult(check_name="queue_capacity", passed=False, reason=primary_reason))
        else:
            checks.append(PolicyCheckResult(check_name="queue_capacity", passed=True, reason="Queue has capacity"))

        # 7. Daily Limit Check
        if queued_today_count >= self.policy.max_jobs_per_day:
            passed_all = False
            primary_reason = f"Daily autonomous limit reached ({queued_today_count}/{self.policy.max_jobs_per_day})"
            checks.append(PolicyCheckResult(check_name="daily_limit", passed=False, reason=primary_reason))
        else:
            checks.append(PolicyCheckResult(check_name="daily_limit", passed=True, reason="Daily quota available"))

        # Decision Action
        if not passed_all:
            action = DecisionAction.BLOCK
        elif autonomy_level >= AutonomyLevel.LEVEL_3_AUTO_QUEUE:
            action = DecisionAction.QUEUE
        elif autonomy_level == AutonomyLevel.LEVEL_2_PROPOSAL:
            action = DecisionAction.APPROVE
        else:
            action = DecisionAction.APPROVE

        dec_id = f"dec-{uuid.uuid4().hex[:10]}"
        return IdeaDecision(
            decision_id=dec_id,
            proposal_id="",  # Linked upon proposal creation
            action=action,
            autonomy_level=autonomy_level,
            reason=primary_reason,
            checks=checks,
            decided_at=now_iso,
        )


class AutonomyEngine:
    """Coordinates autonomous ideation cycles, policy evaluation, queue injection, and learning."""

    def __init__(
        self,
        config: Optional[Config] = None,
        db: Optional[DBManager] = None,
        trend_provider: Optional[TrendProvider] = None,
        policy: Optional[AutonomyPolicy] = None,
    ):
        self.config = config or CONFIG
        self.db = db or DBManager(self.config.db_path)
        self.db.init_schema()
        self.trend_provider = trend_provider or MockTrendProvider()
        self.discovery = self.trend_provider
        self.policy = policy or AutonomyPolicy(
            max_ideas_per_cycle=self.config.autonomy_max_ideas_per_cycle,
            max_auto_queue_per_cycle=self.config.autonomy_max_auto_queue_per_cycle,
            max_jobs_per_day=self.config.autonomy_max_daily_jobs,
            topic_cooldown_days=self.config.autonomy_topic_cooldown_days,
            similarity_threshold=self.config.autonomy_similarity_threshold,
            min_score_threshold=self.config.autonomy_min_score_threshold,
        )
        self.diversity_filter = DiversityFilter()
        self.ideation_engine = IdeationEngine(diversity_filter=self.diversity_filter)
        self.scorer = TopicScorer(strategy_influence_scale=self.config.strategy_influence_scale)
        self.feedback_analyzer = FeedbackAnalyzer(self.db)
        self.strategy_manager = StrategyManager(self.db)
        self.policy_gate = PolicyGate(self.policy)
        self.channel_manager = ChannelManager(self.db)
        self.logger = StructuredLogger(job_id="autonomy", stage="ideation")

    @property
    def learning_engine(self):
        """Lazily-constructed shared learning engine.

        Import is deferred so the autonomy module never hard-depends on the
        learning subsystem at import time; the engine itself is forbidden from
        importing any production/publishing machinery (asserted by test).
        """
        from autopilot.core.learning import LearningEngine

        return LearningEngine(
            config=self.config,
            db=self.db,
            feedback_analyzer=self.feedback_analyzer,
            strategy_manager=self.strategy_manager,
        )

    def run_learning_cycle(
        self,
        channel_id: Optional[str] = None,
        dry_run: bool = False,
        min_samples: Optional[int] = None,
        window_days: Optional[int] = None,
        min_age_days: Optional[int] = None,
        exclude_job_ids: Optional[List[str]] = None,
    ) -> Any:
        """Deterministic, idempotent analytics learning entry point.

        Delegates entirely to the shared ``LearningEngine`` so that CLI,
        scheduler, and this engine never duplicate learning logic.  Learning
        only influences future ideation via bounded strategy weights; it never
        publishes, produces, or alters policy.
        """
        cid = channel_id or "default"
        return self.learning_engine.update_strategy(
            channel_id=cid,
            dry_run=dry_run,
            min_samples=min_samples,
            window_days=window_days,
            min_age_days=min_age_days,
            exclude_job_ids=set(exclude_job_ids) if exclude_job_ids else None,
        )

    def run_cycle(
        self,
        autonomy_level: Optional[int] = None,
        dry_run: bool = False,
        category: Optional[str] = None,
        limit: int = 10,
        channel_id: Optional[str] = None,
    ) -> AutonomyCycleSummary:
        """Executes a full autonomy ideation cycle, optionally bound to a specific channel."""
        level = autonomy_level if autonomy_level is not None else self.config.autonomy_level
        now_dt = datetime.now(timezone.utc)
        run_id = f"run-{now_dt.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
        cid = channel_id or "default"

        self.logger.info(
            "autonomy_cycle_started",
            details={"run_id": run_id, "channel_id": cid, "level": level, "dry_run": dry_run, "category": category},
        )

        channel = self.channel_manager.get_channel(cid)
        if channel and channel.status == ChannelStatus.DISABLED:
            summary = AutonomyCycleSummary(
                run_id=run_id,
                channel_id=cid,
                autonomy_level=level,
                dry_run=dry_run,
                status="blocked",
                error_message=f"Channel '{cid}' is disabled.",
                active_strategy_version="none",
            )
            return summary

        active_policy = channel.autonomy_policy if channel else self.policy
        active_strategy = self.strategy_manager.get_active_strategy(channel_id=cid)

        if not dry_run:
            self.db.record_autonomy_run(
                run_id=run_id,
                autonomy_level=level,
                strategy_version=active_strategy.version_id,
                config_json=json.dumps({"dry_run": dry_run, "limit": limit, "category": category, "channel_id": cid}),
                channel_id=cid,
            )

        # Level 0 (Manual): Exit without autonomous ideation
        if level == AutonomyLevel.LEVEL_0_MANUAL:
            summary = AutonomyCycleSummary(
                run_id=run_id,
                channel_id=cid,
                autonomy_level=level,
                dry_run=dry_run,
                active_strategy_version=active_strategy.version_id,
                status="completed_manual_mode",
            )
            if not dry_run:
                self.db.update_autonomy_run(run_id=run_id, status="completed")
            return summary

        try:
            # 1. Trend Discovery
            signals = self.trend_provider.discover_trends(category=category, limit=limit)
            if not dry_run:
                for s in signals:
                    self.db.record_trend_signal(s, run_id=run_id)

            # 2. Historical Analytics Feedback (Channel-isolated)
            feedback_signals = self.feedback_analyzer.extract_feedback_signals(limit=25, channel_id=cid)

            # 3. Recent Topics for Cooldown (Channel-isolated)
            recent_topics = self.db.get_recent_topics(days=active_policy.topic_cooldown_days, channel_id=cid)

            # 4. Ideation (Channel-aware)
            candidates = self.ideation_engine.generate_candidates(
                signals=signals,
                strategy=active_strategy,
                recent_topics=recent_topics,
                run_id=run_id,
                limit=active_policy.max_ideas_per_cycle,
                channel=channel,
            )

            # 5. Channel Quota Check
            quota_allowed = True
            quota_reason = ""
            if channel:
                quota_allowed, quota_reason, _ = self.channel_manager.check_daily_quota(cid)

            # 6. Scoring & Policy Evaluation
            policy_gate = PolicyGate(active_policy)
            queue_summary = self.db.get_queue_status_summary(channel_id=cid)
            active_queued = queue_summary.get("queued", 0) + queue_summary.get("running", 0)
            queued_today = self.db.count_autonomous_jobs_queued_today(channel_id=cid)

            proposals_created = 0
            jobs_queued = 0
            jobs_blocked = 0

            for cand in candidates:
                # Find matching signal if any
                sig = next((s for s in signals if s.signal_id in cand.supporting_signal_ids), None)
                score = self.scorer.score_candidate(
                    cand, signal=sig, feedback_signals=feedback_signals, strategy=active_strategy
                )

                decision = policy_gate.evaluate(
                    candidate=cand,
                    score=score,
                    active_queued_count=active_queued + jobs_queued,
                    queued_today_count=queued_today + jobs_queued,
                    autonomy_level=level,
                )

                prop_id = f"prop-{cand.candidate_id[3:]}"
                decision.proposal_id = prop_id

                if decision.action == DecisionAction.BLOCK:
                    jobs_blocked += 1
                    prop_status = ProposalStatus.REJECTED
                elif decision.action == DecisionAction.QUEUE and level >= AutonomyLevel.LEVEL_3_AUTO_QUEUE:
                    if not quota_allowed:
                        prop_status = ProposalStatus.PROPOSED
                        decision.reason = f"Channel quota reached: {quota_reason}; saved as proposal"
                        jobs_blocked += 1
                        proposals_created += 1
                    elif jobs_queued < active_policy.max_auto_queue_per_cycle:
                        prop_status = ProposalStatus.QUEUED
                        jobs_queued += 1
                    else:
                        prop_status = ProposalStatus.PROPOSED
                        decision.reason = f"Per-cycle auto-queue quota reached ({active_policy.max_auto_queue_per_cycle}); saved as proposal"
                        proposals_created += 1
                else:
                    prop_status = ProposalStatus.PROPOSED
                    proposals_created += 1

                proposal = IdeaProposal(
                    proposal_id=prop_id,
                    run_id=run_id,
                    channel_id=cid,
                    candidate=cand,
                    score=score,
                    status=prop_status,
                    decision=decision,
                    decided_at=datetime.now(timezone.utc).isoformat() if prop_status != ProposalStatus.PROPOSED else None,
                )

                if not dry_run:
                    self.db.record_idea_proposal(proposal)

                    # If auto-queued, insert into M7 queue
                    if prop_status == ProposalStatus.QUEUED:
                        job_id = f"job-auto-{cand.candidate_id[3:]}"
                        queue_id = f"q-{job_id}"
                        payload = {
                            "topic": cand.proposed_topic,
                            "channel_id": cid,
                            "angle": cand.angle,
                            "hook": cand.hook_hypothesis,
                            "profile": cand.content_format,
                            "origin": "autonomous",
                            "ideation_run_id": run_id,
                            "topic_candidate_id": cand.candidate_id,
                            "strategy_version": active_strategy.version_id,
                            "decision_reason": decision.reason,
                            "parent_signal_ids": cand.supporting_signal_ids,
                        }
                        self.db.enqueue_item(
                            queue_id=queue_id,
                            job_id=job_id,
                            content_id=job_id,
                            channel_id=cid,
                            priority=2,
                            stage="RESEARCH",
                            payload=payload,
                        )
                        if channel:
                            self.channel_manager.record_quota_consumption(cid, queued_increment=1)
                        self.logger.info(
                            "job_auto_queued",
                            details={"queue_id": queue_id, "job_id": job_id, "channel_id": cid, "topic": cand.proposed_topic},
                        )

            if not dry_run:
                self.db.update_autonomy_run(
                    run_id=run_id,
                    status="completed",
                    signals_discovered=len(signals),
                    candidates_generated=len(candidates),
                    proposals_created=proposals_created,
                    jobs_queued=jobs_queued,
                )

            summary = AutonomyCycleSummary(
                run_id=run_id,
                channel_id=cid,
                autonomy_level=level,
                dry_run=dry_run,
                signals_discovered=len(signals),
                candidates_generated=len(candidates),
                proposals_created=proposals_created,
                jobs_queued=jobs_queued,
                jobs_blocked=jobs_blocked,
                status="completed",
                error_message=f"Channel quota reached: {quota_reason}" if not quota_allowed else None,
                active_strategy_version=active_strategy.version_id,
            )

            self.logger.info(
                "autonomy_cycle_completed",
                details=summary.model_dump(),
            )
            return summary

        except Exception as exc:
            self.logger.error("autonomy_cycle_failed", error=str(exc), details={"run_id": run_id})
            if not dry_run:
                try:
                    self.db.update_autonomy_run(run_id=run_id, status="failed", error_message=str(exc)[:500])
                except Exception:
                    pass
            return AutonomyCycleSummary(
                run_id=run_id,
                channel_id=cid,
                autonomy_level=level,
                dry_run=dry_run,
                status="failed",
                error_message=str(exc),
                active_strategy_version=active_strategy.version_id,
            )

    def run_auto_produce_cycle(
        self,
        channel_id: Optional[str] = None,
        limit: int = 10,
        dry_run: bool = False,
        policy: str = "local_only",
        provider_overrides: Optional[Dict[str, str]] = None,
        orchestrator: Any = None,
    ) -> AutoProduceSummary:
        """Level 4 GUARDED AUTO-PRODUCE cycle.

        Consumes eligible queue items (auto-queued by Level 3 or manually
        enqueued) through the existing real worker/pipeline up to the terminal
        pre-publish ``APPROVED`` (READY_TO_PUBLISH) state.  It never invokes a
        public publisher: each claimed job is processed with
        ``force_auto_publish=False``.

        Guardrail boundaries:
        * No autonomous publishing (public boundary preserved).
        * No scheduling, posting-window logic, or analytics learning here.
        * Each item is re-validated against the *current* channel policy before
          production (prohibited topics, malformed topic, required evidence,
          allowed content profiles, niche categories, active channel).
          Cooldown/duplicate-risk and minimum-score are NOT re-applied — those
          were decided when the item was queued (Level 3).
        * Daily/concurrency/cycle production budgets are enforced via
          ``max_jobs_per_day`` / ``max_concurrent_jobs`` and ``limit``.
        """
        cid = channel_id or "default"
        now_dt = datetime.now(timezone.utc)
        run_id = f"ap-{now_dt.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
        logger = StructuredLogger(job_id=run_id, stage="auto_produce")

        summary = AutoProduceSummary(
            run_id=run_id,
            channel_id=cid,
            autonomy_level=4,
            dry_run=dry_run,
            policy=policy,
        )

        channel = self.channel_manager.get_channel(cid)
        if channel and channel.status == ChannelStatus.DISABLED:
            summary.status = "blocked"
            summary.error_message = f"Channel '{cid}' is disabled."
            logger.warning("level4_channel_disabled", details={"run_id": run_id, "channel_id": cid})
            return summary

        active_policy = channel.autonomy_policy if channel else self.policy
        active_strategy = self.strategy_manager.get_active_strategy(channel_id=cid)
        summary.active_strategy_version = active_strategy.version_id

        from autopilot.core.worker import LocalWorker
        worker = LocalWorker(
            config=self.config,
            db=self.db,
            orchestrator=orchestrator,
        )

        if not dry_run:
            self.db.record_autonomy_run(
                run_id=run_id,
                autonomy_level=4,
                strategy_version=active_strategy.version_id,
                config_json=json.dumps({
                    "dry_run": dry_run, "limit": limit, "policy": policy, "channel_id": cid,
                }),
                channel_id=cid,
            )

        logger.info(
            "level4_cycle_started",
            details={"run_id": run_id, "channel_id": cid, "limit": limit, "dry_run": dry_run, "policy": policy},
        )

        try:
            # Enumerate claimable/eligible items once for this cycle.
            discovered = self.db.list_eligible_queue_items(
                channel_id=cid,
                limit=max(limit * 2, 200),
            )
            summary.queued_jobs_discovered = len(discovered)

            in_flight_today = self.db.count_autonomous_jobs_queued_today(channel_id=cid)
            produced_today = self.db.count_daily_produced_jobs(channel_id=cid)
            running_now = int(self.db.get_queue_status_summary(channel_id=cid).get("running", 0) or 0)

            for item in discovered:
                # --- Cycle budget ---
                if summary.jobs_producing + summary.jobs_completed >= limit:
                    summary.jobs_cycle_limit_blocked += 1
                    summary.decision_reasons.append(
                        {"queue_id": item["queue_id"], "reason": f"Per-cycle production limit ({limit}) reached"}
                    )
                    continue

                # --- Daily budget ---
                if produced_today + summary.jobs_completed + in_flight_today >= active_policy.max_jobs_per_day:
                    summary.jobs_daily_limit_blocked += 1
                    summary.decision_reasons.append(
                        {"queue_id": item["queue_id"], "reason": f"Daily production limit ({active_policy.max_jobs_per_day}) reached"}
                    )
                    continue

                # --- Concurrency budget (includes other daemon workers) ---
                in_cycle_running = summary.jobs_producing - summary.jobs_completed - summary.jobs_qa_failed - summary.jobs_retry_wait
                if running_now + in_cycle_running >= active_policy.max_concurrent_jobs:
                    summary.jobs_concurrency_blocked += 1
                    summary.decision_reasons.append(
                        {"queue_id": item["queue_id"], "reason": f"Concurrency limit ({active_policy.max_concurrent_jobs}) reached"}
                    )
                    continue

                # --- Pre-flight policy recheck (current policy, fail-closed) ---
                validation = self._validate_item_for_production(item, channel, active_policy)
                if not validation["ok"]:
                    summary.jobs_blocked += 1
                    summary.decision_reasons.append(
                        {"queue_id": item["queue_id"], "reason": validation["reason"]}
                    )
                    if not dry_run:
                        try:
                            self.db.block_queue_item(item["queue_id"], f"Level 4 pre-flight: {validation['reason']}")
                        except Exception as block_exc:
                            logger.error("level4_block_failed", error=str(block_exc), details={"queue_id": item["queue_id"]})
                    continue

                summary.jobs_eligible += 1
                if dry_run:
                    summary.decision_reasons.append(
                        {"queue_id": item["queue_id"], "reason": "Dry-run: eligible, would produce (no publish)"}
                    )
                    continue

                # --- Atomic claim (race-safe vs. daemon worker / concurrent cycles) ---
                claimed = self.db.claim_queue_item(
                    queue_id=item["queue_id"],
                    worker_id=worker.worker_id,
                    lease_duration_sec=self.config.queue_lease_duration_seconds,
                )
                if not claimed:
                    summary.jobs_skipped_duplicate += 1
                    summary.decision_reasons.append(
                        {"queue_id": item["queue_id"], "reason": "Already claimed/processed by another worker"}
                    )
                    continue

                summary.jobs_producing += 1
                try:
                    effective_overrides = dict(provider_overrides or {})
                    if "policy" not in effective_overrides:
                        # Pin the operator-selected policy tier onto every claimed
                        # item.  This is fail-closed for real tiers (a queued item
                        # must never silently fall back to mock providers) AND
                        # closes the deterministic-test hole: without it, a
                        # Level 3-queued item (no policy in its payload) under a
                        # "mock" tier would resolve real providers.
                        effective_overrides["policy"] = policy
                    result = worker.process_claimed_item(
                        claimed,
                        provider_overrides=(effective_overrides or None),
                        force_auto_publish=False,
                    )
                except Exception as proc_exc:
                    logger.error("level4_process_error", error=str(proc_exc), details={"queue_id": item["queue_id"]})
                    result = {"queue_id": item["queue_id"], "job_id": item["job_id"], "status": "failed", "error": str(proc_exc)}
                    try:
                        self.db.fail_queue_item(
                            queue_id=item["queue_id"],
                            error_message=str(proc_exc),
                            retryable=True,
                            backoff_base_sec=self.config.queue_retry_backoff_base_seconds,
                        )
                    except Exception:
                        pass

                result_status = result.get("status", "failed")
                decision = {"queue_id": item["queue_id"], "job_id": item.get("job_id"), "status": result_status}
                if result.get("error"):
                    decision["error"] = str(result["error"])
                summary.decision_reasons.append(decision)

                if result_status == "succeeded":
                    summary.jobs_completed += 1
                    qa_status = str(result.get("qa_status") or "").upper()
                    published = bool(result.get("published"))
                    if published:
                        # Guard breach detection: Level 4 must never publish.
                        summary.jobs_qa_failed += 1
                        summary.decision_reasons.append(
                            {"queue_id": item["queue_id"], "reason": "Publish guard breached (autonomous publish detected)"}
                        )
                    elif qa_status in ("PASS", "WARN", "APPROVED", "READY", "SUCCESS"):
                        summary.jobs_ready_to_publish += 1
                elif result_status == "retry_wait":
                    summary.jobs_retry_wait += 1
                else:
                    summary.jobs_qa_failed += 1

                self.db.update_autonomy_run(
                    run_id=run_id,
                    jobs_queued=summary.jobs_completed + summary.jobs_qa_failed + summary.jobs_retry_wait,
                )

            self.db.update_autonomy_run(run_id=run_id, status="completed")
            summary.status = "completed"
            logger.info("level4_cycle_completed", details=summary.model_dump())

        except Exception as exc:
            logger.error("level4_cycle_failed", error=str(exc), details={"run_id": run_id})
            summary.status = "failed"
            summary.error_message = str(exc)[:500]
            if not dry_run:
                try:
                    self.db.update_autonomy_run(run_id=run_id, status="failed", error_message=str(exc)[:500])
                except Exception:
                    pass

        return summary

    def _validate_item_for_production(
        self,
        item: Dict[str, Any],
        channel: Any,
        active_policy: AutonomyPolicy,
    ) -> Dict[str, Any]:
        """Pre-flight policy recheck for a Level 4 candidate queue item.

        Focused on *current* policy/safety constraints that gate production:
        active channel, well-formed topic, prohibited content, required evidence
        (autonomous-origin items), allowed content profiles and niche category
        fit.  Deliberately does NOT re-apply cooldown/duplicate-risk or
        minimum-score — those decisions were made at queue time (Level 3).
        """
        cid = item.get("channel_id") or "default"
        if channel and channel.status == ChannelStatus.DISABLED:
            return {"ok": False, "reason": f"Channel '{cid}' is disabled."}

        try:
            payload = json.loads(item.get("payload_json") or "{}") if isinstance(item.get("payload_json"), str) else (item.get("payload_json") or {})
        except Exception:
            payload = {}

        topic = str(payload.get("topic") or item.get("topic") or "").strip()
        hook = str(payload.get("hook") or payload.get("hook_hypothesis") or "").strip()
        angle = str(payload.get("angle") or "").strip()
        content_format = payload.get("profile") or payload.get("content_format") or "short_vertical"
        origin = payload.get("origin", "manual")
        category = payload.get("category") or getattr(channel.niche, "niche_name", "general") if channel else payload.get("category", "general")

        if not topic:
            return {"ok": False, "reason": "Queue item has no topic text."}
        words = topic.split()
        if len(words) > 25:
            return {"ok": False, "reason": f"Malformed topic length ({len(words)} words); rejected by current policy."}

        combined_lower = f"{topic} {hook} {angle}".lower()
        for bad_word in active_policy.prohibited_topics:
            if bad_word and bad_word.lower() in combined_lower:
                return {"ok": False, "reason": f"Prohibited content keyword detected: '{bad_word}'."}

        if active_policy.require_evidence and origin in ("autonomous", "autonomous_manual_approved"):
            evidence_ids = payload.get("parent_signal_ids") or payload.get("supporting_signal_ids") or []
            if not evidence_ids:
                return {"ok": False, "reason": "Missing verifiable supporting trend signal evidence for autonomous job."}

        if active_policy.allowed_profiles:
            allowed = [p.lower() for p in active_policy.allowed_profiles]
            if content_format.lower() not in allowed:
                return {"ok": False, "reason": f"Content profile '{content_format}' not allowed by current policy."}

        if channel:
            niche = getattr(channel, "niche", None)
            if niche and getattr(niche, "allowed_categories", None):
                if category and str(category).lower() not in {c.lower() for c in niche.allowed_categories}:
                    return {"ok": False, "reason": f"Category '{category}' not in allowed niche categories."}
            if niche and getattr(niche, "excluded_categories", None):
                if category and str(category).lower() in {c.lower() for c in niche.excluded_categories}:
                    return {"ok": False, "reason": f"Category '{category}' is excluded by niche policy."}

        return {"ok": True, "reason": "Passed Level 4 pre-flight policy recheck."}

    def approve_proposal(self, proposal_id: str) -> dict:
        """Manually approves a proposal and enqueues it into the M7 queue."""
        prop = self.db.get_idea_proposal(proposal_id)
        if not prop:
            return {"status": "error", "reason": f"Proposal '{proposal_id}' not found"}
        # Gate-blocked proposals (reason starts with 'gate:') are operator-overridable.
        # Operator-rejected proposals are also re-approvable — operators can change their mind.
        if prop["status"] in ("queued", "approved"):
            return {
                "status": "already_approved",
                "proposal_id": proposal_id,
                "reason": f"Proposal is already in status '{prop['status']}'",
            }

        job_id = f"job-auto-{prop['candidate_id'][3:]}"
        queue_id = f"q-{job_id}"
        cid = prop.get("channel_id") or "default"
        payload = {
            "topic": prop["proposed_topic"],
            "channel_id": cid,
            "angle": prop.get("angle", ""),
            "hook": prop.get("hook_hypothesis", ""),
            "profile": prop.get("content_format", "short_vertical"),
            "origin": "autonomous_manual_approved",
            "proposal_id": proposal_id,
        }

        self.db.enqueue_item(
            queue_id=queue_id,
            job_id=job_id,
            content_id=job_id,
            channel_id=cid,
            priority=2,
            stage="RESEARCH",
            payload=payload,
        )
        ch = self.channel_manager.get_channel(cid)
        if ch:
            self.channel_manager.record_quota_consumption(cid, queued_increment=1)
        self.db.update_proposal_status(proposal_id, "approved", reason="Approved by operator")
        return {
            "status": "approved",
            "proposal_id": proposal_id,
            "job_id": job_id,
            "queue_id": queue_id,
        }

    def reject_proposal(self, proposal_id: str, reason: str = "Rejected by operator") -> dict:
        """Manually rejects an idea proposal. Cancels any associated queue item."""
        prop = self.db.get_idea_proposal(proposal_id)
        if not prop:
            return {"status": "error", "reason": f"Proposal '{proposal_id}' not found"}
        # Tag with "operator:" prefix so approve_proposal (P1-04) can distinguish
        # operator rejection from policy-gate auto-rejection (which stays overridable).
        self.db.update_proposal_status(proposal_id, "rejected", reason=f"operator: {reason if reason else 'Rejected by operator'}")
        # P0-03 fix: Cancel any queue item that was enqueued for this proposal.
        cancelled_queue = None
        candidate_id = prop.get("candidate_id")
        if candidate_id:
            job_id = f"job-auto-{candidate_id[3:]}"
            queue_id = f"q-{job_id}"
            cancelled_queue = self.db.cancel_queue_item(queue_id)
        return {
            "status": "rejected",
            "proposal_id": proposal_id,
            "reason": reason,
            "queue_item_cancelled": cancelled_queue,
        }

    def recover_stale_runs(self, max_age_seconds: int = 3600) -> List[str]:
        """Recovers interrupted autonomy runs left in 'running' state."""
        recovered = []
        with self.db._connect() as conn:
            if max_age_seconds <= 0:
                rows = conn.execute(
                    "SELECT run_id FROM autonomy_runs WHERE status = 'running'"
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT run_id FROM autonomy_runs
                    WHERE status = 'running'
                      AND datetime(started_at) <= datetime('now', '-' || ? || ' seconds')
                    """,
                    (max_age_seconds,),
                ).fetchall()
            for r in rows:
                rid = r["run_id"]
                conn.execute(
                    "UPDATE autonomy_runs SET status = 'interrupted', completed_at = CURRENT_TIMESTAMP WHERE run_id = ?",
                    (rid,),
                )
                recovered.append(rid)
            conn.commit()
        return recovered

    def approve_publish(self, job_id: str, decided_by: str = "operator", notes: str = "") -> dict:
        """Approves publication for a job held in assisted mode approval gate."""
        ok = self.db.decide_publish_approval(job_id, approved=True, decided_by=decided_by, notes=notes)
        if not ok:
            return {"status": "error", "message": f"No approval record found for job '{job_id}'."}
        from autopilot.core.publisher import PublishingEngine
        pe = PublishingEngine(self.config, self.db)
        res = pe.publish_job(job_id=job_id, require_approval=True)
        return {
            "status": "approved_and_published" if getattr(res, "success", False) else "approved_publish_pending",
            "job_id": job_id,
            "publish_result": res.model_dump() if hasattr(res, "model_dump") else dict(res),
        }

    def reject_publish(self, job_id: str, decided_by: str = "operator", notes: str = "") -> dict:
        """Rejects publication for a job held in assisted mode approval gate."""
        ok = self.db.decide_publish_approval(job_id, approved=False, decided_by=decided_by, notes=notes)
        return {
            "status": "rejected" if ok else "not_found",
            "job_id": job_id,
        }

    def run_autonomous_cycle(
        self,
        seed_topic: Optional[str] = None,
        channel_id: str = "default",
        mode: str = "assisted",  # manual, assisted, autonomous
        limit: int = 5,
        production_engine: Optional[str] = None,
        tts_provider: str = "mock",
        llm_provider: str = "mock",
        research_provider: str = "wikipedia",
        publish_platform: str = "youtube",
        publish_visibility: str = "unlisted",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Executes a full end-to-end autonomous cycle:
        IDEA -> SELECT -> RESEARCH -> SCRIPT -> PRODUCTION -> TRANSCRIPTION -> QA -> APPROVAL/PUBLISH GATE -> ANALYTICS -> STRATEGY -> NEXT CANDIDATE.
        """
        cid = channel_id or "default"
        cycle_id = f"cyc-{uuid.uuid4().hex[:8]}"
        now_iso = datetime.now(timezone.utc).isoformat()
        strat = self.strategy_manager.get_active_strategy(channel_id=cid)
        channel = self.channel_manager.get_channel(cid)
        # Resolve the active policy for this channel (mirrors run_cycle logic).
        active_policy = channel.autonomy_policy if channel else self.policy

        # 1. Candidate Ideation / Selection
        candidate_info = {}
        if seed_topic:
            selected_topic = seed_topic.strip()
            selection_reason = "Operator provided seed topic"
            candidate_info = {
                "topic": selected_topic,
                "rationale": selection_reason,
                "novelty_score": 0.8,
                "relevance_score": 0.9,
                "evidence_availability": 0.85,
                "estimated_cost": 0.5,
                "predicted_performance_score": 0.85,
            }
        else:
            signals = self.trend_provider.discover_trends(limit=limit)
            recent = self.db.get_recent_topics(days=active_policy.topic_cooldown_days, channel_id=cid)
            candidates = self.ideation_engine.generate_candidates(
                signals=signals,
                strategy=strat,
                recent_topics=recent,
                run_id=cycle_id,
                channel=self.channel_manager.get_channel(cid),
            )
            if candidates:
                best_cand = candidates[0]
                sig = next((s for s in signals if s.signal_id in best_cand.supporting_signal_ids), None)
                score = self.scorer.score_candidate(best_cand, signal=sig)
                selected_topic = best_cand.proposed_topic
                selection_reason = best_cand.rationale
                cand_score = score.total_score
                candidate_info = {
                    "topic": selected_topic,
                    "rationale": selection_reason,
                    "novelty_score": score.content_novelty,
                    "relevance_score": score.relevance,
                    "evidence_availability": 1.0 if best_cand.supporting_signal_ids else 0.5,
                    "estimated_cost": score.production_effort_factor,
                    "predicted_performance_score": score.total_score,
                }
            else:
                selected_topic = "Autonomous Discovery Topic"
                selection_reason = "Fallback generic topic"
                candidate_info = {
                    "topic": selected_topic,
                    "rationale": selection_reason,
                    "novelty_score": 0.0,
                    "relevance_score": 0.0,
                    "evidence_availability": 0.0,
                    "estimated_cost": 0.5,
                    "predicted_performance_score": 0.0,
                }

        # 2. Production Pipeline
        job_id = f"job-{cycle_id}-{uuid.uuid4().hex[:4]}"
        from autopilot.core.pipeline import PipelineOrchestrator
        orchestrator = PipelineOrchestrator(self.config, self.db)

        pipe_res = orchestrator.run_pipeline(
            job_id=job_id,
            topic=selected_topic,
            channel_id=cid,
            production_engine=production_engine,
            tts_provider=tts_provider,
            llm_provider=llm_provider,
            research_provider=research_provider,
            auto_publish=False,  # Governance handles publishing explicitly
            max_regeneration_attempts=active_policy.max_regeneration_attempts,
        )

        qa_status = pipe_res.get("status", "UNKNOWN")
        reported_qa = pipe_res.get("qa_status", "UNKNOWN")
        qa_report = pipe_res.get("qa_report")

        # 3. Approval / Publishing Boundary
        pub_result = None
        approval_status = "none"
        is_success = (
            qa_status.upper() in ("READY", "APPROVED", "SUCCESS", "PASS", "WARN")
            or reported_qa.upper() in ("PASS", "WARN", "APPROVED", "READY")
            or pipe_res.get("publish_allowed", False)
        )
        if is_success:
            if mode == "assisted":
                # Require human approval
                approval_id = self.db.create_publish_approval(
                    job_id=job_id,
                    channel_id=cid,
                    notes=f"Assisted mode gate for topic '{selected_topic}'",
                )
                approval_status = "pending"
            elif mode == "autonomous":
                # Protected publishing loop: autonomous mode never silently
                # publishes. It creates a pending approval and halts, exactly like
                # assisted mode. Publication only happens when the operator opted
                # into `autonomy_auto_publish` AND a previously approved approval
                # record already exists (i.e. this run merely executes it).
                pre_auth = self.db.get_publish_approval(job_id)
                switch_on = effective_auto_publish(self.config, self.db)
                if switch_on and pre_auth and pre_auth.get("status") == "approved":
                    from autopilot.core.publisher import PublishingEngine
                    pe = PublishingEngine(self.config, self.db)
                    pub_result = pe.publish_job(
                        job_id=job_id,
                        platform=publish_platform,
                        visibility=publish_visibility,
                        dry_run=dry_run,
                        require_approval=True,
                        autonomous=True,
                    )
                    approval_status = "auto_approved"
                else:
                    self.db.create_publish_approval(
                        job_id=job_id,
                        channel_id=cid,
                        notes=f"Autonomous mode approval gate for topic '{selected_topic}'",
                    )
                    approval_status = "pending"
            else:
                approval_status = "manual_held"

        # 4. Analytics Snapshot
        analytics_info = {}
        try:
            from autopilot.core.analytics import AnalyticsEngine
            ae = AnalyticsEngine(self.config, self.db)
            analytics_info = ae.sync_job(job_id=job_id, platform=publish_platform, dry_run=dry_run)
        except Exception as exc:
            analytics_info = {"status": "error", "error": str(exc)}

        # 5. Feedback / Strategy Learning (idempotent, bounded, shared engine)
        strategy_update_info = {"updated": False, "version": strat.version_id}
        try:
            learn_summary = self.learning_engine.update_strategy(
                channel_id=cid,
                dry_run=dry_run,
                exclude_job_ids={job_id} if job_id else None,
            )
            strategy_update_info = {
                "updated": learn_summary.status == "applied",
                "version": learn_summary.resulting_strategy_version or strat.version_id,
                "learning_run_id": learn_summary.run_id,
                "learning_status": learn_summary.status,
                "observations_used": learn_summary.observations_used,
            }
        except Exception as exc:  # pragma: no cover - defensive
            strategy_update_info = {"updated": False, "version": strat.version_id, "error": str(exc)[:200]}

        return {
            "cycle_id": cycle_id,
            "channel_id": cid,
            "mode": mode,
            "candidate": candidate_info,
            "job_id": job_id,
            "pipeline_status": qa_status,
            "approval_status": approval_status,
            "publish_result": pub_result.model_dump() if hasattr(pub_result, "model_dump") else (dict(pub_result) if pub_result else None),
            "analytics": analytics_info,
            "strategy": strategy_update_info,
            "timestamp": now_iso,
        }
