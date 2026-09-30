"""Defect Classifier & Machine-Readable Quality Diagnostics — Phase 5.

Implements:
  - Machine-readable defect codes (e.g. VISUAL_MISMATCH_SCENE_03, BAD_CROP_SCENE_02, etc.)
  - Severity classification (BLOCK, WARN, INFO)
  - Actionable regeneration targets mapping (VISUAL_ASSET, CROP_FRAMING, CAPTION_LAYOUT, VOICE_AUDIO, AUDIO_MIX)
  - Integration with both Technical QA and Creative QA reports.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from autopilot.core.creative_qa import CreativeQAReport, CreativeQAStatus


class DefectSeverity(str, Enum):
    BLOCK = "BLOCK"
    WARN = "WARN"
    INFO = "INFO"


class RegenerationTargetType(str, Enum):
    VISUAL_ASSET = "VISUAL_ASSET"
    CROP_FRAMING = "CROP_FRAMING"
    CAPTION_LAYOUT = "CAPTION_LAYOUT"
    VOICE_AUDIO = "VOICE_AUDIO"
    AUDIO_MIX = "AUDIO_MIX"
    SCRIPT = "SCRIPT"
    FULL_TIMELINE = "FULL_TIMELINE"
    NONE = "NONE"


class DefectItem(BaseModel):
    code: str = Field(..., description="Standardized machine-readable defect code")
    severity: DefectSeverity
    confidence: float = Field(default=0.90, description="Confidence in defect classification 0.0 - 1.0")
    scene_id: Optional[str] = Field(default=None, description="Affected scene identifier if localized")
    metric: str
    evidence: str
    recommended_action: str
    regeneration_target: RegenerationTargetType = RegenerationTargetType.NONE


class DefectClassifierEngine:
    """Classifies technical and creative QA anomalies into actionable defect codes."""

    def classify_defects(
        self,
        creative_report: CreativeQAReport,
        technical_report: Optional[Any] = None,
    ) -> List[DefectItem]:
        """Convert QA findings into structured, machine-readable defect items."""
        defects: List[DefectItem] = []

        # 1. Low Confidence / Ambiguity Escalation
        if creative_report.confidence < 0.65 or creative_report.overall_status == CreativeQAStatus.HUMAN_REVIEW:
            defects.append(
                DefectItem(
                    code="LOW_CONFIDENCE_CREATIVE_QA",
                    severity=DefectSeverity.WARN,
                    confidence=creative_report.confidence,
                    metric="Confidence",
                    evidence=creative_report.human_review_reason or f"Overall QA confidence is {creative_report.confidence:.2f} < 0.65",
                    recommended_action="Escalate to Human Review before proceeding.",
                    regeneration_target=RegenerationTargetType.NONE,
                )
            )

        # 2. Visual Match Defects
        if creative_report.visual_match.status in (CreativeQAStatus.BLOCK, CreativeQAStatus.WARN):
            for s_id, data in creative_report.visual_match.scene_breakdown.items():
                if data.get("score", 100) < 65.0:
                    sev = DefectSeverity.BLOCK if data.get("score", 100) < 45.0 else DefectSeverity.WARN
                    defects.append(
                        DefectItem(
                            code=f"VISUAL_MISMATCH_{s_id.upper()}",
                            severity=sev,
                            confidence=0.88,
                            scene_id=s_id,
                            metric="Visual-to-Script Alignment",
                            evidence=data.get("notes", "Visual asset does not match scene visual intent"),
                            recommended_action=f"Regenerate or retrieve new visual asset for {s_id}.",
                            regeneration_target=RegenerationTargetType.VISUAL_ASSET,
                        )
                    )

        # 3. Caption Placement / Safe-Zone / Crop Defects
        if creative_report.caption_placement.status in (CreativeQAStatus.BLOCK, CreativeQAStatus.WARN):
            for s_id, data in creative_report.caption_placement.scene_breakdown.items():
                if "collision" in str(data).lower() or "over" in str(data).lower():
                    defects.append(
                        DefectItem(
                            code=f"BAD_CROP_{s_id.upper()}",
                            severity=DefectSeverity.WARN,
                            confidence=0.92,
                            scene_id=s_id,
                            metric="Caption Placement",
                            evidence=f"Caption in {s_id} collides with salient subject.",
                            recommended_action=f"Recompute crop framing and caption position for {s_id}.",
                            regeneration_target=RegenerationTargetType.CROP_FRAMING,
                        )
                    )

        # 4. Caption Readability / Overflow
        if creative_report.caption_readability.status in (CreativeQAStatus.BLOCK, CreativeQAStatus.WARN):
            defects.append(
                DefectItem(
                    code="CAPTION_OVERFLOW_DETECTED",
                    severity=DefectSeverity.WARN,
                    confidence=0.95,
                    metric="Caption Readability",
                    evidence=creative_report.caption_readability.evidence,
                    recommended_action="Re-segment caption phrases into 2-4 impactful words per screen.",
                    regeneration_target=RegenerationTargetType.CAPTION_LAYOUT,
                )
            )

        # 5. Visual Continuity / Duplicate Footage
        if creative_report.visual_continuity.status in (CreativeQAStatus.BLOCK, CreativeQAStatus.WARN):
            defects.append(
                DefectItem(
                    code="REPEATED_VISUAL_DETECTED",
                    severity=DefectSeverity.WARN,
                    confidence=0.98,
                    metric="Visual Continuity",
                    evidence=creative_report.visual_continuity.evidence,
                    recommended_action="Replace duplicate visual asset with distinct footage.",
                    regeneration_target=RegenerationTargetType.VISUAL_ASSET,
                )
            )

        # 6. Pacing Defects
        if creative_report.pacing_cadence.status in (CreativeQAStatus.BLOCK, CreativeQAStatus.WARN):
            for s_id, data in creative_report.pacing_cadence.scene_breakdown.items():
                dur = data.get("duration_sec", 0.0)
                if dur > 6.0:
                    defects.append(
                        DefectItem(
                            code=f"PACING_{s_id.upper()}",
                            severity=DefectSeverity.WARN,
                            confidence=0.95,
                            scene_id=s_id,
                            metric="Pacing & Cadence",
                            evidence=f"{s_id} duration ({dur:.1f}s) exceeds maximum short-form shot budget.",
                            recommended_action=f"Split {s_id} into multiple visual cuts or tighten narration.",
                            regeneration_target=RegenerationTargetType.VISUAL_ASSET,
                        )
                    )

        # 7. Audio Balance Defects
        if creative_report.audio_balance.status in (CreativeQAStatus.BLOCK, CreativeQAStatus.WARN):
            defects.append(
                DefectItem(
                    code="AUDIO_BALANCE_DEFECT",
                    severity=DefectSeverity.BLOCK if creative_report.audio_balance.status == CreativeQAStatus.BLOCK else DefectSeverity.WARN,
                    confidence=0.92,
                    metric="Audio Balance",
                    evidence=creative_report.audio_balance.evidence,
                    recommended_action="Rebuild 3-track audio scene graph and verify sidechain ducking levels.",
                    regeneration_target=RegenerationTargetType.AUDIO_MIX,
                )
            )

        # 8. Black Frames (P0)
        if creative_report.black_frames.status in (CreativeQAStatus.BLOCK, CreativeQAStatus.WARN):
            defects.append(
                DefectItem(
                    code="BLACK_FRAMES_DETECTED",
                    severity=DefectSeverity.BLOCK if creative_report.black_frames.status == CreativeQAStatus.BLOCK else DefectSeverity.WARN,
                    confidence=0.95,
                    metric="Black Frames",
                    evidence=creative_report.black_frames.evidence,
                    recommended_action="Re-render; replace black source segments with valid footage.",
                    regeneration_target=RegenerationTargetType.VISUAL_ASSET,
                )
            )

        # 9. Freeze / Static Sections (P0)
        if creative_report.freeze_sections.status in (CreativeQAStatus.BLOCK, CreativeQAStatus.WARN):
            defects.append(
                DefectItem(
                    code="FROZEN_SECTION_DETECTED",
                    severity=DefectSeverity.BLOCK if creative_report.freeze_sections.status == CreativeQAStatus.BLOCK else DefectSeverity.WARN,
                    confidence=0.93,
                    metric="Freeze/Static Sections",
                    evidence=creative_report.freeze_sections.evidence,
                    recommended_action="Re-render with motion or additional B-roll coverage for static sections.",
                    regeneration_target=RegenerationTargetType.VISUAL_ASSET,
                )
            )

        # 10. Listicle Structure Defect (P0) — routes back to SCRIPT
        if creative_report.listicle_structure.status in (CreativeQAStatus.BLOCK, CreativeQAStatus.WARN):
            defects.append(
                DefectItem(
                    code="LISTICLE_STRUCTURE_DEFECT",
                    severity=DefectSeverity.BLOCK if creative_report.listicle_structure.status == CreativeQAStatus.BLOCK else DefectSeverity.WARN,
                    confidence=0.97,
                    metric="Listicle Structure",
                    evidence=creative_report.listicle_structure.evidence,
                    recommended_action=(
                        "Regenerate the SCRIPT with N substantively distinct items; this "
                        "invalidates dependent voice, asset, and render artifacts."
                    ),
                    regeneration_target=RegenerationTargetType.SCRIPT,
                )
            )

        # 11. Technical QA Ingestion (if provided)
        if technical_report:
            t_findings = getattr(technical_report, "findings", []) or []
            for f in t_findings:
                status = str(getattr(f, "status", "")).upper()
                if "BLOCK" in status or "FAIL" in status:
                    defects.append(
                        DefectItem(
                            code="TECHNICAL_QA_BLOCKER",
                            severity=DefectSeverity.BLOCK,
                            confidence=1.0,
                            metric=getattr(f, "category", "Technical QA"),
                            evidence=getattr(f, "message", "Technical invariant violation"),
                            recommended_action="Fix technical stream/container error and re-render.",
                            regeneration_target=RegenerationTargetType.FULL_TIMELINE,
                        )
                    )

        return defects
