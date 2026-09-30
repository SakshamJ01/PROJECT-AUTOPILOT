"""Composite Publish Readiness Gate — Phase 5.

Implements:
  - Composite multi-layer publication decision combining:
      1. Technical QA (Container integrity, streams, audio clipping, timeline invariants).
      2. Creative QA (Scorecard thresholds, hook, pacing, caption placement).
      3. Defect Classifier (Zero blocking defects).
      4. Rights & Commercial License Gates.
      5. Human Review Escalation State.
  - Strict boundary: Creative or analytics layers CANNOT override factual verification, rights checks, or hard render invariants.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from autopilot.core.creative_qa import CreativeQAReport, CreativeQAStatus
from autopilot.core.defect_classifier import DefectItem, DefectSeverity


class PublishReadinessStatus(str, Enum):
    READY = "READY"
    BLOCKED = "BLOCKED"
    PENDING_HUMAN_REVIEW = "PENDING_HUMAN_REVIEW"


class PublishReadinessDecision(BaseModel):
    is_ready_to_publish: bool
    status: PublishReadinessStatus
    overall_confidence: float = Field(default=0.90, description="Composite confidence 0.0 - 1.0")
    technical_qa_passed: bool = True
    creative_qa_passed: bool = True
    blocking_reasons: List[str] = Field(default_factory=list)
    warning_reasons: List[str] = Field(default_factory=list)
    human_review_required: bool = False
    human_review_approved: bool = False
    rights_verified: bool = True
    invariants_verified: bool = True
    can_override_with_human_approval: bool = True
    summary: Optional[str] = None


def _iter_score_items(report: CreativeQAReport) -> List[Any]:
    """Return every CreativeQAScoreItem dimension carried by the report."""
    items: List[Any] = []
    fields = report.model_fields.keys() if hasattr(report, "model_fields") else report.__fields__.keys()
    for field in fields:
        value = getattr(report, field, None)
        if value is None or isinstance(value, (str, int, float, bool, list, dict)):
            continue
        if getattr(value, "status", None) in (CreativeQAStatus.PASS, CreativeQAStatus.WARN, CreativeQAStatus.BLOCK):
            items.append(value)
    return items


class PublishReadinessGate:
    """Evaluates full composite quality, rights, and technical gates for publication approval."""

    def evaluate(
        self,
        creative_report: CreativeQAReport,
        defects: List[DefectItem],
        technical_report: Optional[Any] = None,
        rights_verified: bool = True,
        invariants_verified: bool = True,
        human_review_approved: bool = False,
    ) -> PublishReadinessDecision:
        """Compute the composite publish readiness decision."""
        blocking_reasons: List[str] = []
        warning_reasons: List[str] = []

        # 1. Hard Render Invariants & Legal Rights Gates (NON-OVERRIDABLE)
        if not rights_verified:
            blocking_reasons.append("Commercial rights or asset license verification failed.")
        if not invariants_verified:
            blocking_reasons.append("Hard render timeline invariants failed validation.")

        # 2. Technical QA Checks
        tech_passed = True
        if technical_report:
            t_status = str(getattr(technical_report, "status", "PASS")).upper()
            if "BLOCK" in t_status or "FAIL" in t_status:
                tech_passed = False
                blocking_reasons.append(f"Technical QA blocked: {getattr(technical_report, 'summary', 'Invariant failure')}")

        # 3. Creative QA Checks
        creative_passed = True
        if creative_report.overall_status == CreativeQAStatus.BLOCK:
            creative_passed = False
            # Name the dimensions that actually blocked, instead of quoting a
            # misleading overall-score threshold.
            blocked = [
                getattr(item, "name", "?")
                for item in _iter_score_items(creative_report)
                if getattr(item, "status", None) == CreativeQAStatus.BLOCK
            ]
            detail = ", ".join(blocked) if blocked else "no dimension-level detail available"
            blocking_reasons.append(
                f"Creative QA hard block (overall {creative_report.overall_score:.1f}); "
                f"blocking dimensions: {detail}"
            )

        # 4. Defect Classifier Evaluation
        for d in defects:
            if d.severity == DefectSeverity.BLOCK:
                blocking_reasons.append(f"Blocking Defect [{d.code}]: {d.evidence}")
            elif d.severity == DefectSeverity.WARN:
                warning_reasons.append(f"Warning [{d.code}]: {d.evidence}")

        # 5. Human Review Escalation
        human_review_required = (
            creative_report.human_review_required
            or creative_report.overall_status == CreativeQAStatus.HUMAN_REVIEW
            or any(d.code == "LOW_CONFIDENCE_CREATIVE_QA" for d in defects)
        )

        # Non-overridable boundary: If hard invariants or rights failed, human approval cannot override
        can_override = rights_verified and invariants_verified and tech_passed

        # Compute Final Publish Status
        if blocking_reasons:
            is_ready = False
            status = PublishReadinessStatus.BLOCKED
        elif human_review_required and not human_review_approved:
            is_ready = False
            status = PublishReadinessStatus.PENDING_HUMAN_REVIEW
        else:
            is_ready = True
            status = PublishReadinessStatus.READY

        summary_text = (
            "All technical, creative, rights, and invariant gates passed. Ready for automated publishing."
            if is_ready
            else f"Publishing not approved: {'; '.join(blocking_reasons) if blocking_reasons else 'Pending human review'}"
        )

        return PublishReadinessDecision(
            is_ready_to_publish=is_ready,
            status=status,
            overall_confidence=creative_report.confidence,
            technical_qa_passed=tech_passed,
            creative_qa_passed=creative_passed,
            blocking_reasons=blocking_reasons,
            warning_reasons=warning_reasons,
            human_review_required=human_review_required,
            human_review_approved=human_review_approved,
            rights_verified=rights_verified,
            invariants_verified=invariants_verified,
            can_override_with_human_approval=can_override,
            summary=summary_text,
        )
