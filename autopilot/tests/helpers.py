"""Shared test helpers."""
import json
from pathlib import Path


def write_ready_gate_evidence(artifacts_root: Path, job_id: str, media_checksum: str) -> None:
    """Write a ready, checksum-bound composite publish-readiness decision.

    Publishing requires the composite gate evidence to exist and match the
    render being published. Test fixtures that seed a QA receipt must also seed
    this artifact or the publisher (correctly) fails closed.
    """
    gate_dir = artifacts_root / "jobs" / job_id / "qa"
    gate_dir.mkdir(parents=True, exist_ok=True)
    (gate_dir / "publish_readiness.json").write_text(
        json.dumps({
            "is_ready_to_publish": True,
            "status": "READY",
            "overall_confidence": 0.95,
            "technical_qa_passed": True,
            "creative_qa_passed": True,
            "blocking_reasons": [],
            "warning_reasons": [],
            "human_review_required": False,
            "human_review_approved": False,
            "rights_verified": True,
            "invariants_verified": True,
            "can_override_with_human_approval": True,
            "media_checksum_sha256": media_checksum,
        }),
        encoding="utf-8",
    )


def install_ready_gate(monkeypatch):
    """Patch the pipeline's composite gate with a deterministic READY decision.

    Structural pipeline tests that seed stub content (which cannot clear the
    real CLIP/caption/freeze creative-QA checks) use this to isolate themselves
    from gate logic, which is exercised by the dedicated gate tests. The fake
    persists checksum-bound gate evidence exactly like the real gate.
    """
    from autopilot.core.publish_readiness import (
        PublishReadinessDecision,
        PublishReadinessStatus,
    )
    from autopilot.core.publisher import compute_file_sha256

    def _ready_gate(job_id, media_path, technical_report, db, artifacts_dir=None):
        decision = PublishReadinessDecision(
            is_ready_to_publish=True,
            status=PublishReadinessStatus.READY,
        )
        decision.media_checksum_sha256 = compute_file_sha256(media_path)
        if artifacts_dir is None:
            from autopilot.core.config import CONFIG

            base = Path(str(CONFIG.get_artifacts_dir()))
        else:
            base = Path(artifacts_dir)
        qa_dir = base / "jobs" / job_id / "qa"
        qa_dir.mkdir(parents=True, exist_ok=True)
        (qa_dir / "publish_readiness.json").write_text(
            decision.model_dump_json(indent=2), encoding="utf-8"
        )
        return decision

    monkeypatch.setattr("autopilot.core.pipeline.run_composite_gate", _ready_gate)
    return _ready_gate
