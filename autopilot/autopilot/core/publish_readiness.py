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

import hashlib
import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from autopilot.core.config import CONFIG
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
    factual_review_required: bool = False
    creative_review_required: bool = False
    rights_verified: bool = True
    invariants_verified: bool = True
    can_override_with_human_approval: bool = True
    summary: Optional[str] = None
    # Media checksum this decision was computed against. The publisher
    # refuses to publish when this does not match the current render, so a
    # stale gate verdict can never bless a re-rendered video.
    media_checksum_sha256: Optional[str] = None


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
        claim_verification: Optional[Dict[str, Any]] = None,
    ) -> PublishReadinessDecision:
        """Compute the composite publish readiness decision."""
        blocking_reasons: List[str] = []
        warning_reasons: List[str] = []

        # 1. Hard Render Invariants & Legal Rights Gates (NON-OVERRIDABLE)
        if not rights_verified:
            blocking_reasons.append("Commercial rights or asset license verification failed.")
        if not invariants_verified:
            blocking_reasons.append("Hard render timeline invariants failed validation.")

        factual_review_required = False
        if claim_verification is not None:
            scenes = claim_verification.get("scenes") or []
            if claim_verification.get("error"):
                blocking_reasons.append("Factual claim verification failed to run.")
            for claim in scenes:
                status = str(claim.get("status", "UNVERIFIED")).upper()
                if status in {"CONFLICTING", "REJECTED"}:
                    blocking_reasons.append(
                        f"Factual claim {claim.get('scene_id', '?')} is {status.lower()} and must be corrected."
                    )
                elif status != "VERIFIED":
                    factual_review_required = True
            if claim_verification.get("status") not in {"VERIFIED", "REVIEW_REQUIRED"}:
                blocking_reasons.append("Factual claim verification report is invalid or incomplete.")

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
        creative_review_required = (
            creative_report.human_review_required
            or creative_report.overall_status == CreativeQAStatus.HUMAN_REVIEW
            or any(d.code == "LOW_CONFIDENCE_CREATIVE_QA" for d in defects)
        )
        human_review_required = creative_review_required or factual_review_required

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
            factual_review_required=factual_review_required,
            creative_review_required=creative_review_required,
            rights_verified=rights_verified,
            invariants_verified=invariants_verified,
            can_override_with_human_approval=can_override,
            summary=summary_text,
        )


def compute_file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _derive_rights_and_invariants(
    report, job_id: str, artifacts_dir: str | Path | None = None
) -> Tuple[bool, bool, Dict[str, Any]]:
    """Derive rights/invariants evidence for the composite gate from real checks.

    These two inputs used to be hardcoded True, which meant the publish gate
    claimed verification it never performed. They are now derived from the QA
    checks that actually ran, corroborated by the persisted perceptual render
    provenance, and they fail closed when evidence is missing.
    """
    from autopilot.core.artifacts import job_artifact_dir as _job_dir
    from autopilot.core.contracts import QAStatus as _QAStatus

    base_dir = Path(artifacts_dir) if artifacts_dir is not None else CONFIG.get_artifacts_dir()

    checks = {c.check_id: c for c in (getattr(report, "checks", None) or [])}
    evidence: Dict[str, Any] = {}

    rights_check = checks.get("check-rights-gate")
    rights_verified = rights_check is not None and rights_check.status == _QAStatus.PASS
    evidence["rights"] = {
        "check_id": "check-rights-gate",
        "check_ran": rights_check is not None,
        "check_status": str(rights_check.status) if rights_check is not None else None,
        "verified": rights_verified,
    }

    invariant_ids = (
        "check-render-plan-consistency",
        "check-provenance-integrity",
        "check-scene-coverage",
    )
    per_check: Dict[str, Any] = {}
    invariants_verified = True
    for cid in invariant_ids:
        found = checks.get(cid)
        ok = found is not None and found.status == _QAStatus.PASS
        per_check[cid] = {"check_ran": found is not None, "passed": ok}
        invariants_verified = invariants_verified and ok

    # Corroborate with the perceptual render provenance written during render.
    # Absence of the artifact is itself a failure: without it there is no
    # evidence that the rendered video actually contains the claimed assets.
    provenance_path = _job_dir(job_id, base_dir) / "render" / "render_provenance.json"
    artifact_present = provenance_path.exists()
    provenance_detail: Dict[str, Any] = {"artifact_present": artifact_present, "passed": False}
    prov_ok = False
    if artifact_present:
        try:
            prov = json.loads(provenance_path.read_text(encoding="utf-8"))
        except Exception:
            prov = {}
        claimed = prov.get("claimed_scene_count")
        verified_n = prov.get("verified_scene_count")
        prov_ok = bool(prov.get("valid")) and claimed is not None and claimed == verified_n
        provenance_detail.update({
            "valid": prov.get("valid"),
            "claimed_scene_count": claimed,
            "verified_scene_count": verified_n,
            "passed": prov_ok,
        })
    invariants_verified = invariants_verified and prov_ok

    evidence["invariants"] = {"checks": per_check, "render_provenance": provenance_detail,
                              "verified": invariants_verified}
    return rights_verified, invariants_verified, evidence


def _propagate_gate_verdict_to_receipt(report, publish_decision) -> None:
    """Mirror the composite publish gate onto the nested quality receipt.

    The publisher trusts `quality/receipt.json` as the authoritative QA
    artifact, while the composite gate (creative QA + publish readiness) only
    ever demotes the parent QAReport. Without this propagation a creative hard
    block leaves a stale, permissive receipt on disk and publishing fails open.
    """
    from autopilot.core.contracts import QAFinding, QASeverity, QAStatus

    receipt = getattr(report, "receipt", None)
    if receipt is None:
        return

    ready = bool(publish_decision.is_ready_to_publish)
    receipt.publish_allowed = ready
    receipt.status = report.status

    blocking_reasons = list(getattr(publish_decision, "blocking_reasons", []) or [])
    warning_reasons = list(getattr(publish_decision, "warning_reasons", []) or [])

    if not ready and blocking_reasons:
        detail = "; ".join(blocking_reasons)
        receipt.blocking_findings = [
            *receipt.blocking_findings,
            QAFinding(
                finding_id=f"gate-{uuid.uuid4().hex[:12]}",
                check_id="publish_readiness_gate",
                category="publish_readiness",
                severity=QASeverity.CRITICAL,
                status=QAStatus.BLOCK,
                message=f"Composite publish readiness gate blocked this job: {detail}",
                evidence={
                    "technical_qa_passed": bool(getattr(publish_decision, "technical_qa_passed", False)),
                    "creative_qa_passed": bool(getattr(publish_decision, "creative_qa_passed", False)),
                    "overall_confidence": getattr(publish_decision, "overall_confidence", None),
                    "blocking_reasons": blocking_reasons,
                },
            ),
        ]

    if warning_reasons:
        receipt.warnings = [
            *receipt.warnings,
            *[
                QAFinding(
                    finding_id=f"gate-warn-{uuid.uuid4().hex[:12]}",
                    check_id="publish_readiness_gate",
                    category="publish_readiness",
                    severity=QASeverity.MEDIUM,
                    status=QAStatus.WARN,
                    message=str(reason),
                    evidence={"warning_reasons": warning_reasons},
                )
                for reason in warning_reasons
            ],
        ]


def publish_readiness_allows(
    job_id: str,
    media_checksum: str | None,
    artifacts_dir: str | Path | None = None,
) -> Tuple[bool, str]:
    """Check the persisted composite gate evidence for a job.

    Returns ``(allowed, reason)``. Fail-closed: missing, unready, or
    checksum-mismatched evidence all return ``False``.
    """
    from autopilot.core.artifacts import job_artifact_dir

    base_dir = Path(artifacts_dir) if artifacts_dir is not None else CONFIG.get_artifacts_dir()
    path = job_artifact_dir(job_id, base_dir) / "qa" / "publish_readiness.json"
    if not path.exists():
        return False, "no composite publish-readiness evidence (run `autopilot qa` first)"
    try:
        decision = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"publish-readiness evidence unreadable: {exc}"
    if not decision.get("is_ready_to_publish", False):
        reasons = "; ".join(decision.get("blocking_reasons", []) or ["unspecified"])
        return False, f"composite gate not ready: {reasons}"
    if media_checksum is not None:
        bound = decision.get("media_checksum_sha256")
        if not bound:
            return False, "publish-readiness evidence is not checksum-bound (re-run `autopilot qa`)"
        if bound != media_checksum:
            return False, "publish-readiness evidence is stale (media checksum mismatch)"
    return True, "ok"


def approve_factual_human_review(
    job_id: str,
    media_checksum: str,
    reviewed_by: str,
    artifacts_dir: str | Path | None = None,
    creative_reviewed: bool = False,
) -> Dict[str, Any]:
    """Record explicit factual review, bound to current render and report.

    Only clears a review-only hold. Any factual conflict, QA hard blocker,
    missing report, or stale media checksum remains non-overridable.
    """
    from autopilot.core.artifacts import job_artifact_dir

    base_dir = Path(artifacts_dir) if artifacts_dir is not None else CONFIG.get_artifacts_dir()
    job_dir = job_artifact_dir(job_id, base_dir)
    decision_path = job_dir / "qa" / "publish_readiness.json"
    factual_path = job_dir / "qa" / "factual_verification.json"
    receipt_path = job_dir / "quality" / "receipt.json"
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    if decision.get("media_checksum_sha256") != media_checksum:
        raise ValueError("The render changed after QA. Re-run QA before approving factual review.")
    if decision.get("blocking_reasons"):
        raise ValueError("Hard QA or factual blockers cannot be cleared by human review.")
    if not decision.get("human_review_required"):
        raise ValueError("This job has no factual review hold to approve.")
    if decision.get("factual_review_required"):
        if not factual_path.exists():
            raise ValueError("Factual verification report is missing.")
        factual = json.loads(factual_path.read_text(encoding="utf-8"))
        if factual.get("error") or factual.get("status") != "REVIEW_REQUIRED":
            raise ValueError("Factual verification report is invalid or not reviewable.")
        if any(str(c.get("status", "UNVERIFIED")).upper() in {"CONFLICTING", "REJECTED"}
               for c in factual.get("scenes", [])):
            raise ValueError("Conflicting or rejected claims must be corrected before publishing.")
    if decision.get("creative_review_required") and not creative_reviewed:
        raise ValueError("Explicit creative-QA review is required before approval.")

    now = datetime.now(timezone.utc).isoformat()
    decision.update({
        "is_ready_to_publish": True,
        "status": PublishReadinessStatus.READY.value,
        "human_review_approved": True,
        "human_review_approved_by": reviewed_by,
        "human_review_approved_at": now,
        "human_review_approved_media_checksum_sha256": media_checksum,
        "summary": "Factual review approved; all hard QA, rights, invariant, and checksum gates passed.",
    })
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("media_checksum_sha256") != media_checksum:
        raise ValueError("The QA receipt is stale for the current render. Re-run QA before approval.")
    receipt["publish_allowed"] = True
    receipt["human_review_approved_by"] = reviewed_by
    receipt["human_review_approved_at"] = now
    receipt["human_review_approved_media_checksum_sha256"] = media_checksum

    # Write temp files and replace each artifact atomically to avoid partial JSON.
    for path, payload in ((decision_path, decision), (receipt_path, receipt)):
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp_path.replace(path)
    return decision


def run_composite_gate(
    job_id: str,
    media_path: str | Path,
    technical_report: Any,
    db: Any,
    artifacts_dir: str | Path | None = None,
) -> PublishReadinessDecision:
    """Run Creative QA + the composite publish gate and persist all evidence.

    Shared by `autopilot qa` (CLI) and the pipeline QA stage so both produce
    identical gate evidence. Fail-closed: any infrastructure error yields a
    BLOCKED decision, never a pass.
    """
    from autopilot.core.artifacts import job_artifact_dir
    from autopilot.core.creative_qa import CreativeQAEngine
    from autopilot.core.defect_classifier import DefectClassifierEngine
    from autopilot.core.logging import StructuredLogger
    from autopilot.core.timeline_builder import load_timeline_from_job_dir

    logger = StructuredLogger(job_id=job_id, stage="qa")
    media = Path(media_path)
    base_dir = Path(artifacts_dir) if artifacts_dir is not None else CONFIG.get_artifacts_dir()
    qa_dir = job_artifact_dir(job_id, base_dir) / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)

    try:
        timeline, _media_assets = load_timeline_from_job_dir(job_id, str(base_dir))
        cqa = CreativeQAEngine(sample_interval_sec=1.5)
        creative_report = cqa.evaluate_production(timeline, str(media))
        (qa_dir / "creative_qa_report.json").write_text(
            creative_report.model_dump_json(indent=2), encoding="utf-8"
        )
        if hasattr(db, "record_artifact"):
            db.record_artifact(job_id, str(qa_dir / "creative_qa_report.json"), "quality")

        defect_cls = DefectClassifierEngine()
        defects = defect_cls.classify_defects(creative_report=creative_report, technical_report=technical_report)

        rights_verified, invariants_verified, gate_evidence = _derive_rights_and_invariants(
            technical_report, job_id, base_dir
        )
        from autopilot.core.artifacts import script_path
        factual_path = script_path(job_id, "script.json", base_dir=base_dir)
        claim_verification = None
        if factual_path.exists():
            try:
                script_payload = json.loads(factual_path.read_text(encoding="utf-8"))
                claim_verification = (script_payload.get("generation_metadata") or {}).get("claim_verification")
            except Exception:
                claim_verification = {"status": "INVALID", "error": "Unable to read factual verification report."}
        else:
            claim_verification = {"status": "INVALID", "error": "Factual verification report is missing."}
        (qa_dir / "factual_verification.json").write_text(
            json.dumps(claim_verification, indent=2), encoding="utf-8"
        )
        if hasattr(db, "record_artifact"):
            db.record_artifact(job_id, str(qa_dir / "factual_verification.json"), "quality")
        (qa_dir / "gate_evidence.json").write_text(json.dumps(gate_evidence, indent=2), encoding="utf-8")
        if hasattr(db, "record_artifact"):
            db.record_artifact(job_id, str(qa_dir / "gate_evidence.json"), "quality")

        gate = PublishReadinessGate()
        decision = gate.evaluate(
            creative_report=creative_report,
            defects=defects,
            technical_report=technical_report,
            rights_verified=rights_verified,
            invariants_verified=invariants_verified,
            human_review_approved=False,
            claim_verification=claim_verification,
        )
        decision.media_checksum_sha256 = compute_file_sha256(media)

        (qa_dir / "publish_readiness.json").write_text(decision.model_dump_json(indent=2), encoding="utf-8")
        if hasattr(db, "record_artifact"):
            db.record_artifact(job_id, str(qa_dir / "publish_readiness.json"), "quality")

        _propagate_gate_verdict_to_receipt(technical_report, decision)
        receipt = getattr(technical_report, "receipt", None)
        if receipt is not None:
            receipt_path = job_artifact_dir(job_id, base_dir) / "quality" / "receipt.json"
            receipt_path.write_text(receipt.model_dump_json(indent=2), encoding="utf-8")
        return decision

    except Exception as exc:
        logger.warning("composite_gate_failed", details={"error": str(exc)})
        return PublishReadinessDecision(
            is_ready_to_publish=False,
            status=PublishReadinessStatus.BLOCKED,
            technical_qa_passed=False,
            creative_qa_passed=False,
            blocking_reasons=[f"Composite gate infrastructure failure: {exc}"],
            summary="Composite publish gate could not run; failing closed.",
        )
