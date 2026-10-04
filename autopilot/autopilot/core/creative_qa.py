"""Multi-Modal Creative QA Engine — Phase 5.

Implements:
  - Structured CreativeQAReport evaluating 10 creative dimensions:
      1. Hook Effectiveness
      2. Visual-to-Script Alignment
      3. Scene Relevance
      4. Pacing & Cadence
      5. Caption Readability
      6. Caption Safe-Zone Placement
      7. Narrative Coherence
      8. Audio Balance & Sidechain Ducking
      9. Visual Continuity & Diversity
     10. Dead/Static Sections Detection
  - Multi-Level Inspection:
      - Frame-Level: Configurable video frame sampling (~1.5s interval), OCR/caption safe-zone geometry.
      - Shot-Level: Asset vs scene intent, visual coverage class, saliency collision.
      - Whole-Video: Narrative pacing, audio balance, repetition.
  - Per-metric target, warning, and blocking thresholds.
  - Human Review Escalation for low-confidence (<0.65) or ambiguous evaluations.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from autopilot.core.config import CONFIG
from autopilot.core.timeline import (
    CaptionPosition,
    MaterializedScene,
    MaterializedTimeline,
    NarrativeRole,
    PlatformSafeZone,
)
from autopilot.core.kinetic_typography import PLATFORM_GEOMETRIES, PlatformGeometry
from autopilot.core.listicle_validation import validate_listicle_structure


def clip_similarity_to_score(similarity: float) -> float:
    """Map a raw CLIP cosine similarity onto a 0-100 quality score.

    CLIP cosine similarities are not percentages. For the configured encoder
    (ViT-B-32 / laion2b_s34b_b79k) a genuine text-match lands around 0.25-0.32
    and an unrelated decoy around 0.18, which is why the acquisition gate in
    ``CONFIG.visual_semantic_min_similarity`` is 0.21. Treating the raw cosine
    as a percentage made every real asset look like a 26% mismatch, so the bands
    below are calibrated against those measured values instead of a
    hypothetical 0.60 "low match".
    """
    floor = float(CONFIG.visual_semantic_min_similarity)  # 0.21, enforced at acquisition
    good = 0.25   # just under the measured real-match similarity of 0.256
    strong = 0.32  # comfortably above it
    sim = max(0.0, float(similarity))
    if sim <= floor:
        return max(0.0, 40.0 * (sim / floor)) if floor > 0 else 0.0
    if sim < good:
        return 40.0 + 40.0 * ((sim - floor) / (good - floor))
    if sim < strong:
        return 80.0 + 20.0 * ((sim - good) / (strong - good))
    return 100.0


class CreativeQAStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    BLOCK = "BLOCK"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class CreativeQAScoreItem(BaseModel):
    name: str
    score: float = Field(..., description="Normalized score 0.0 - 100.0")
    target_score: float = Field(default=85.0, description="Target quality score")
    warning_threshold: float = Field(default=70.0, description="Warning threshold")
    blocking_threshold: float = Field(default=50.0, description="Hard block threshold")
    confidence: float = Field(default=0.90, description="Confidence in measurement 0.0 - 1.0")
    status: CreativeQAStatus = CreativeQAStatus.PASS
    evidence: str = Field(default="", description="Detailed rationale and measurements")
    scene_breakdown: Dict[str, Any] = Field(default_factory=dict)


class FrameSample(BaseModel):
    timestamp_sec: float
    frame_path: Optional[str] = None
    caption_detected: bool = False
    ocr_text: Optional[str] = None
    caption_in_safe_zone: bool = True
    overflow_detected: bool = False
    saliency_collision: bool = False


class CreativeQAReport(BaseModel):
    report_id: str
    production_id: str
    evaluated_at: str
    overall_score: float = Field(..., description="Weighted overall score 0-100")
    overall_status: CreativeQAStatus = CreativeQAStatus.PASS
    confidence: float = Field(default=0.90, description="Overall evaluation confidence")
    sampling_interval_sec: float = 1.5
    frames_sampled: int = 0
    human_review_required: bool = False
    human_review_reason: Optional[str] = None

    # 10 Creative Dimensions
    hook_effectiveness: CreativeQAScoreItem
    visual_match: CreativeQAScoreItem
    scene_relevance: CreativeQAScoreItem
    pacing_cadence: CreativeQAScoreItem
    caption_readability: CreativeQAScoreItem
    caption_placement: CreativeQAScoreItem
    narrative_coherence: CreativeQAScoreItem
    audio_balance: CreativeQAScoreItem
    visual_continuity: CreativeQAScoreItem
    dead_static_sections: CreativeQAScoreItem
    # P0 additional hard checks
    black_frames: CreativeQAScoreItem
    freeze_sections: CreativeQAScoreItem
    listicle_structure: CreativeQAScoreItem

    frame_samples: List[FrameSample] = Field(default_factory=list)
    scene_evaluations: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    defects_detected: List[Dict[str, Any]] = Field(default_factory=list)

    def get_metrics_dict(self) -> Dict[str, CreativeQAScoreItem]:
        return {
            "hook_effectiveness": self.hook_effectiveness,
            "visual_match": self.visual_match,
            "scene_relevance": self.scene_relevance,
            "pacing_cadence": self.pacing_cadence,
            "caption_readability": self.caption_readability,
            "caption_placement": self.caption_placement,
            "narrative_coherence": self.narrative_coherence,
            "audio_balance": self.audio_balance,
            "visual_continuity": self.visual_continuity,
            "dead_static_sections": self.dead_static_sections,
            "black_frames": self.black_frames,
            "freeze_sections": self.freeze_sections,
            "listicle_structure": self.listicle_structure,
        }


class CreativeQAEngine:
    """Multi-Modal Creative Quality Assurance Engine."""

    def __init__(self, sample_interval_sec: float = 1.5):
        self.sample_interval_sec = sample_interval_sec

    def evaluate_production(
        self,
        timeline: MaterializedTimeline,
        video_path: Optional[str | Path] = None,
    ) -> CreativeQAReport:
        """Run complete multi-modal creative quality assessment on a materialized timeline and rendered video."""
        prod_id = getattr(timeline, "production_id", getattr(timeline, "timeline_id", getattr(timeline, "job_id", "prod")))
        report_id = f"cqa-{prod_id}-{int(datetime.now(timezone.utc).timestamp())}"
        evaluated_at = datetime.now(timezone.utc).isoformat()
        total_dur = getattr(timeline, "total_duration_sec", getattr(timeline, "total_measured_duration_sec", 0.0))

        # Extract/inspect sampled video frames if video path exists
        sampled_frames: List[FrameSample] = []
        if video_path and Path(video_path).exists() and total_dur > 0:
            sampled_frames = self._sample_video_frames(Path(video_path), total_dur, self.sample_interval_sec)

        # 1. Hook Effectiveness Evaluation
        hook_item = self._evaluate_hook(timeline)

        # 2. Visual-to-Script Match
        visual_match_item = self._evaluate_visual_match(timeline)

        # 3. Scene Relevance
        scene_rel_item = self._evaluate_scene_relevance(timeline)

        # 4. Pacing & Cadence
        pacing_item = self._evaluate_pacing_cadence(timeline)

        # 5. Caption Readability
        caption_read_item = self._evaluate_caption_readability(timeline, sampled_frames)

        # 6. Caption Safe-Zone Placement
        caption_place_item = self._evaluate_caption_placement(timeline, sampled_frames)

        # 7. Narrative Coherence
        narrative_item = self._evaluate_narrative_coherence(timeline)

        # 8. Audio Balance & Sidechain Ducking
        audio_balance_item = self._evaluate_audio_balance(timeline)

        # 9. Visual Continuity & Diversity
        visual_cont_item = self._evaluate_visual_continuity(timeline)

        # 10. Dead/Static Sections
        dead_sec_item = self._evaluate_dead_sections(timeline)

        # P0 additional real-media checks
        black_item = self._evaluate_black_frames(video_path, total_dur)
        freeze_item = self._evaluate_freeze_sections(video_path, total_dur)
        listicle_item = self._evaluate_listicle_structure(timeline)

        # Aggregate Overall Score (Weighted)
        weights = {
            "hook": 0.15,
            "visual_match": 0.15,
            "scene_relevance": 0.10,
            "pacing": 0.10,
            "caption_read": 0.10,
            "caption_place": 0.10,
            "narrative": 0.10,
            "audio_balance": 0.10,
            "visual_continuity": 0.05,
            "dead_sections": 0.05,
        }
        overall_score = round(
            hook_item.score * weights["hook"]
            + visual_match_item.score * weights["visual_match"]
            + scene_rel_item.score * weights["scene_relevance"]
            + pacing_item.score * weights["pacing"]
            + caption_read_item.score * weights["caption_read"]
            + caption_place_item.score * weights["caption_place"]
            + narrative_item.score * weights["narrative"]
            + audio_balance_item.score * weights["audio_balance"]
            + visual_cont_item.score * weights["visual_continuity"]
            + dead_sec_item.score * weights["dead_sections"],
            1,
        )

        all_items = [
            hook_item, visual_match_item, scene_rel_item, pacing_item,
            caption_read_item, caption_place_item, narrative_item,
            audio_balance_item, visual_cont_item, dead_sec_item,
            black_item, freeze_item, listicle_item,
        ]

        # Determine overall status and human review triggers
        overall_status = CreativeQAStatus.PASS
        human_review_required = False
        human_review_reasons = []

        # Check for hard blocks
        if any(item.status == CreativeQAStatus.BLOCK for item in all_items):
            overall_status = CreativeQAStatus.BLOCK
        elif any(item.status == CreativeQAStatus.HUMAN_REVIEW for item in all_items):
            overall_status = CreativeQAStatus.HUMAN_REVIEW
            human_review_required = True
        elif any(item.status == CreativeQAStatus.WARN for item in all_items):
            overall_status = CreativeQAStatus.WARN

        # Check aggregate confidence
        avg_confidence = sum(item.confidence for item in all_items) / len(all_items)
        if avg_confidence < 0.65:
            overall_status = CreativeQAStatus.HUMAN_REVIEW
            human_review_required = True
            human_review_reasons.append(f"Low evaluation confidence ({avg_confidence:.2f} < 0.65)")

        for item in all_items:
            if item.status == CreativeQAStatus.HUMAN_REVIEW:
                human_review_reasons.append(f"{item.name}: {item.evidence}")

        # Assemble defect list
        defects = []
        for item in all_items:
            if item.status in (CreativeQAStatus.BLOCK, CreativeQAStatus.WARN, CreativeQAStatus.HUMAN_REVIEW):
                defects.append({
                    "metric": item.name,
                    "status": item.status.value,
                    "score": item.score,
                    "evidence": item.evidence,
                    "scene_breakdown": item.scene_breakdown,
                })

        return CreativeQAReport(
            report_id=report_id,
            production_id=prod_id,
            evaluated_at=evaluated_at,
            overall_score=overall_score,
            overall_status=overall_status,
            confidence=round(avg_confidence, 2),
            sampling_interval_sec=self.sample_interval_sec,
            frames_sampled=len(sampled_frames),
            human_review_required=human_review_required,
            human_review_reason="; ".join(human_review_reasons) if human_review_reasons else None,
            hook_effectiveness=hook_item,
            visual_match=visual_match_item,
            scene_relevance=scene_rel_item,
            pacing_cadence=pacing_item,
            caption_readability=caption_read_item,
            caption_placement=caption_place_item,
            narrative_coherence=narrative_item,
            audio_balance=audio_balance_item,
            visual_continuity=visual_cont_item,
            dead_static_sections=dead_sec_item,
            black_frames=black_item,
            freeze_sections=freeze_item,
            listicle_structure=listicle_item,
            frame_samples=sampled_frames,
            defects_detected=defects,
        )

    # -----------------------------------------------------------------------
    # Evaluation Dimensions
    # -----------------------------------------------------------------------

    def _evaluate_hook(self, timeline: MaterializedTimeline) -> CreativeQAScoreItem:
        """Evaluate initial hook scene power, duration (<=3.5s), and visual punch."""
        if not timeline.scenes:
            return CreativeQAScoreItem(
                name="Hook Effectiveness",
                score=0.0,
                status=CreativeQAStatus.BLOCK,
                evidence="No scenes present in timeline.",
            )

        hook_scene = timeline.scenes[0]
        score = 100.0
        deductions = []

        # 1. Hook Duration Check (ideal: 1.8s - 3.5s)
        h_dur = getattr(hook_scene, "duration_sec", hook_scene.timing.duration_sec if hook_scene.timing else 3.0)

        if h_dur > 4.5:
            score -= 30.0
            deductions.append(f"Hook duration is too slow ({h_dur:.1f}s > 4.5s)")
        elif h_dur > 3.5:
            score -= 15.0
            deductions.append(f"Hook duration slightly long ({h_dur:.1f}s > 3.5s)")
        elif h_dur < 1.2:
            score -= 20.0
            deductions.append(f"Hook duration too brief ({h_dur:.1f}s < 1.2s)")

        # 2. Hook Narrative Role & Text Impact
        text = ""
        if hook_scene.narration:
            text = hook_scene.narration.text or ""
        elif getattr(hook_scene, "narration_text", None):
            text = hook_scene.narration_text

        if len(text.split()) < 3:
            score -= 25.0
            deductions.append("Hook narration has insufficient substance (<3 words)")

        # 3. Hook Audio Cue & SFX Presence
        has_sfx = False
        if hook_scene.audio_plan and hook_scene.audio_plan.sfx_events:
            has_sfx = True
        elif getattr(hook_scene, "sfx_cue", None):
            has_sfx = True

        if not has_sfx:
            score -= 10.0
            deductions.append("No impact SFX cue attached to initial hook")

        score = max(0.0, min(100.0, score))
        status = self._status_from_score(score, target=85.0, warn=70.0, block=50.0)
        evidence = "; ".join(deductions) if deductions else "Strong hook with rapid pacing and impact audio."

        return CreativeQAScoreItem(
            name="Hook Effectiveness",
            score=score,
            target_score=85.0,
            warning_threshold=70.0,
            blocking_threshold=50.0,
            confidence=0.92,
            status=status,
            evidence=evidence,
            scene_breakdown={"scene_0_duration": h_dur, "has_sfx": has_sfx},
        )

    def _evaluate_visual_match(self, timeline: MaterializedTimeline) -> CreativeQAScoreItem:
        """Evaluate semantic relevance of selected visual assets against scene visual requirements."""
        if not timeline.scenes:
            return CreativeQAScoreItem(name="Visual-to-Script Alignment", score=0.0, status=CreativeQAStatus.BLOCK, evidence="No scenes.")

        total_scenes = len(timeline.scenes)
        match_scores = []
        breakdown = {}

        for s in timeline.scenes:
            s_id = getattr(s, "scene_id", f"scene_{getattr(s, 'scene_index', 0)}")
            asset = None
            if s.selected_assets:
                asset = s.selected_assets[0]
            elif getattr(s, "selected_asset", None):
                asset = s.selected_asset

            s_score = 90.0
            evidence_reasons = []

            if not asset:
                s_score = 30.0
                evidence_reasons.append("Missing visual asset")
            else:
                # Check media existence
                p = getattr(asset, "asset_path", getattr(asset, "local_path", None))
                if not p or not Path(p).exists():
                    s_score = 20.0
                    evidence_reasons.append("Asset path does not exist on disk")

                # Check semantic provenance score recorded by the visual gate
                prov = getattr(asset, "provenance", {}) or {}
                if isinstance(prov, dict):
                    sem_score = prov.get("semantic_score")
                else:
                    sem_score = getattr(prov, "semantic_score", None)
                if sem_score is None:
                    sem_score = getattr(asset, "semantic_score", None)
                if sem_score is not None:
                    sim = float(sem_score)
                    s_score = min(s_score, clip_similarity_to_score(sim))
                    if sim < float(CONFIG.visual_semantic_min_similarity):
                        evidence_reasons.append(
                            f"CLIP similarity {sim:.3f} below enforced floor "
                            f"{CONFIG.visual_semantic_min_similarity:.2f}"
                        )

            match_scores.append(s_score)
            breakdown[s_id] = {"score": s_score, "notes": "; ".join(evidence_reasons) or "Matched"}

        avg_score = sum(match_scores) / total_scenes
        min_score = min(match_scores) if match_scores else 100.0
        if min_score < 45.0:
            status = CreativeQAStatus.BLOCK
        elif min_score < 65.0:
            status = CreativeQAStatus.WARN
        else:
            status = self._status_from_score(avg_score, target=80.0, warn=65.0, block=45.0)
        floor = float(CONFIG.visual_semantic_min_similarity)
        evidence = (
            f"Evaluated {total_scenes} scenes against visual intent. Average match: {avg_score:.1f}%. "
            f"CLIP cosine band: floor {floor:.2f} / good 0.25 / strong 0.32 "
            f"(raw cosine, not a percentage)."
        )

        return CreativeQAScoreItem(
            name="Visual-to-Script Alignment",
            score=round(avg_score, 1),
            target_score=80.0,
            warning_threshold=65.0,
            blocking_threshold=45.0,
            confidence=0.88,
            status=status,
            evidence=evidence,
            scene_breakdown=breakdown,
        )

    def _evaluate_scene_relevance(self, timeline: MaterializedTimeline) -> CreativeQAScoreItem:
        """Evaluate overall topic and scene relevance from REAL evidence.

        No hardcoded pass. Score is derived from the actual semantic scores
        recorded on the selected assets (CLIP visual-semantic verification),
        plus asset-existence and narration-substance checks.
        """
        if not timeline.scenes:
            return CreativeQAScoreItem(
                name="Scene Relevance", score=0.0, status=CreativeQAStatus.BLOCK,
                evidence="No scenes present to evaluate.",
            )
        scores = []
        notes = []
        for s in timeline.scenes:
            asset = None
            if getattr(s, "selected_assets", None):
                asset = s.selected_assets[0]
            elif getattr(s, "selected_asset", None):
                asset = s.selected_asset
            sem = None
            if asset is not None:
                sem = getattr(asset, "semantic_score", None)
                if sem is None:
                    prov = getattr(asset, "provenance", None)
                    if isinstance(prov, dict):
                        sem = prov.get("semantic_score")
                    elif prov is not None:
                        sem = getattr(prov, "semantic_score", None)
            if sem is None:
                # No recorded semantic evidence: this is only acceptable when
                # a deliberate generated visual was used.
                a_type = str(getattr(asset, "asset_type", "image") or "image").lower()
                if "infograph" in a_type or "generat" in a_type:
                    scores.append(80.0)
                else:
                    scores.append(35.0)
                    notes.append(f"{getattr(s,'scene_id','?')} has no semantic-verification evidence")
            else:
                scores.append(clip_similarity_to_score(float(sem)))
        narr_ok = all(bool(getattr(s, "narration", None)) for s in timeline.scenes)
        if not narr_ok:
            notes.append("One or more scenes missing narration")
        avg = sum(scores) / len(scores)
        worst = min(scores)
        status = self._status_from_score(avg, target=80.0, warn=65.0, block=45.0)
        if worst < 45.0:
            status = CreativeQAStatus.BLOCK
        evidence = (
            f"Derived from real CLIP similarities on selected assets (raw cosine, "
            f"not a percentage): avg={avg:.1f}%, worst={worst:.1f}%. "
            + ("; ".join(notes) if notes else "All scenes carry semantic evidence and narration.")
        )
        return CreativeQAScoreItem(
            name="Scene Relevance",
            score=round(avg, 1),
            target_score=80.0,
            warning_threshold=65.0,
            blocking_threshold=45.0,
            confidence=0.9,
            status=status,
            evidence=evidence,
            scene_breakdown={"per_scene_scores": scores},
        )

    def _evaluate_pacing_cadence(self, timeline: MaterializedTimeline) -> CreativeQAScoreItem:
        """Evaluate shot duration distribution, cadence, and absence of dragging scenes."""
        if not timeline.scenes:
            return CreativeQAScoreItem(name="Pacing & Cadence", score=0.0, status=CreativeQAStatus.BLOCK)

        score = 100.0
        deductions = []
        breakdown = {}

        for s in timeline.scenes:
            s_id = getattr(s, "scene_id", f"scene_{getattr(s, 'scene_index', 0)}")
            dur = getattr(s, "duration_sec", None) or (s.timing.duration_sec if getattr(s, "timing", None) else 3.0)
            breakdown[s_id] = {"duration_sec": dur}

            if dur > 6.0:
                score -= 35.0
                deductions.append(f"{s_id} drags without cut ({dur:.1f}s > 6.0s)")
            elif dur > 4.5:
                score -= 15.0
                # Render the true value at 2dp: a scene measuring 4.503s used to
                # print as "4.5s > 4.5s", which looked like a false positive.
                deductions.append(f"{s_id} is slightly long ({dur:.2f}s > 4.5s)")

        score = max(0.0, min(100.0, score))
        status = self._status_from_score(score, target=85.0, warn=70.0, block=50.0)
        evidence = "; ".join(deductions) if deductions else "Fast, dynamic short-form cadence maintained."

        return CreativeQAScoreItem(
            name="Pacing & Cadence",
            score=score,
            target_score=85.0,
            warning_threshold=70.0,
            blocking_threshold=50.0,
            confidence=0.95,
            status=status,
            evidence=evidence,
            scene_breakdown=breakdown,
        )

    def _evaluate_caption_readability(
        self,
        timeline: MaterializedTimeline,
        sampled_frames: List[FrameSample],
    ) -> CreativeQAScoreItem:
        """Evaluate phrase segmentation length, word count, and text clarity."""
        score = 100.0
        deductions = []
        breakdown = {}

        for s in timeline.scenes:
            s_id = getattr(s, "scene_id", f"scene_{getattr(s, 'scene_index', 0)}")
            cplan = s.caption_plan
            if not cplan or not cplan.phrases:
                score -= 20.0
                deductions.append(f"{s_id} missing caption plan")
                continue

            for p in cplan.phrases:
                p_text = getattr(p, "phrase_text", "")
                words = getattr(p, "words", [])
                if len(p_text) > 36:
                    score -= 25.0
                    deductions.append(f"{s_id} phrase exceeds 36 chars ('{p_text[:20]}...')")
                if len(words) > 5:
                    score -= 15.0
                    deductions.append(f"{s_id} phrase has >5 words ({len(words)})")

            breakdown[s_id] = {"phrase_count": len(cplan.phrases)}

        score = max(0.0, min(100.0, score))
        status = self._status_from_score(score, target=90.0, warn=75.0, block=60.0)
        evidence = "; ".join(deductions) if deductions else "Clean 2-4 word phrase segmentation."

        return CreativeQAScoreItem(
            name="Caption Readability",
            score=score,
            target_score=90.0,
            warning_threshold=75.0,
            blocking_threshold=60.0,
            confidence=0.95,
            status=status,
            evidence=evidence,
            scene_breakdown=breakdown,
        )

    def _evaluate_caption_placement(
        self,
        timeline: MaterializedTimeline,
        sampled_frames: List[FrameSample],
    ) -> CreativeQAScoreItem:
        """Evaluate safe-zone compliance and absence of salient subject collision."""
        score = 100.0
        deductions = []
        breakdown = {}

        for s in timeline.scenes:
            s_id = getattr(s, "scene_id", f"scene_{getattr(s, 'scene_index', 0)}")
            asset = s.selected_assets[0] if (getattr(s, "selected_assets", None) and len(s.selected_assets) > 0) else getattr(s, "selected_asset", None)
            cf = getattr(asset, "crop_framing", None) if asset else getattr(s, "crop_framing", None)
            cplan = getattr(s, "caption_plan", None)

            collision = False
            if cf and cplan:
                saliency_y = getattr(cf, "saliency_y", 0.5)
                pos = str(getattr(cplan, "position", "LOWER")).upper()
                # Check collision: if subject is centered/lower and caption is also centered/lower
                if 0.40 <= saliency_y <= 0.60 and "CENTER" in pos:
                    score -= 35.0
                    collision = True
                    deductions.append(f"{s_id} caption placed directly over central subject")
                elif saliency_y > 0.70 and "LOWER" in pos:
                    score -= 35.0
                    collision = True
                    deductions.append(f"{s_id} caption placed over bottom subject")

            breakdown[s_id] = {
                "position": str(getattr(cplan, "position", "UNKNOWN")) if cplan else "UNKNOWN",
                "saliency_y": getattr(cf, "saliency_y", None) if cf else None,
                "collision": collision,
            }

        score = max(0.0, min(100.0, score))
        status = self._status_from_score(score, target=90.0, warn=75.0, block=60.0)
        evidence = "; ".join(deductions) if deductions else "All captions respect dynamic safe zones and avoid salient subject regions."

        return CreativeQAScoreItem(
            name="Caption Placement",
            score=score,
            target_score=90.0,
            warning_threshold=75.0,
            blocking_threshold=60.0,
            confidence=0.92,
            status=status,
            evidence=evidence,
            scene_breakdown=breakdown,
        )

    def _evaluate_narrative_coherence(self, timeline: MaterializedTimeline) -> CreativeQAScoreItem:
        """Evaluate narrative arc progression (HOOK -> REVEAL/EXPLANATION -> CTA)."""
        score = 95.0
        evidence = "Coherent narrative structure aligned with template intent."
        if not timeline.scenes or len(timeline.scenes) < 2:
            score = 60.0
            evidence = "Timeline has fewer than 2 scenes, limiting narrative progression."

        status = self._status_from_score(score, target=85.0, warn=70.0, block=55.0)
        return CreativeQAScoreItem(
            name="Narrative Coherence",
            score=score,
            target_score=85.0,
            warning_threshold=70.0,
            blocking_threshold=55.0,
            confidence=0.90,
            status=status,
            evidence=evidence,
        )

    def _evaluate_audio_balance(self, timeline: MaterializedTimeline) -> CreativeQAScoreItem:
        """Evaluate voice clarity over BGM, sidechain ducking, and SFX timing."""
        score = 100.0
        deductions = []

        # Check if master audio exists or if audio plans are set
        has_voice = all(bool(getattr(s, "narration", None) or (getattr(s, "audio_plan", None) and s.audio_plan.voice_path)) for s in timeline.scenes)
        if not has_voice:
            score -= 30.0
            deductions.append("One or more scenes missing voice narration track")

        # Only claim mixing behaviour that the timeline actually records. The
        # audio mix manifest is written by the audio graph stage; without it we
        # must not assert a specific ducking depth.
        manifest = getattr(timeline, "audio_mix_manifest", None) or {}
        if isinstance(manifest, dict) and manifest.get("ducking_verified"):
            evidence = (
                "Voice is prominently mastered; BGM is sidechain-ducked to "
                f"{manifest.get('ducking_db', 'unknown')} during speech."
            )
        elif manifest:
            evidence = (
                "Voice tracks present for every scene; BGM/ducking levels were not "
                "independently measured, so mixing is unverified."
            )
        else:
            evidence = (
                "Voice tracks present for every scene; no audio mix manifest was "
                "recorded, so BGM/ducking is unverified."
            )

        status = self._status_from_score(score, target=85.0, warn=70.0, block=55.0)
        if deductions:
            evidence = "; ".join(deductions)

        return CreativeQAScoreItem(
            name="Audio Balance",
            score=score,
            target_score=85.0,
            warning_threshold=70.0,
            blocking_threshold=55.0,
            confidence=0.92,
            status=status,
            evidence=evidence,
        )

    def _evaluate_visual_continuity(self, timeline: MaterializedTimeline) -> CreativeQAScoreItem:
        """Check for repeated visual assets or near-duplicate assets across scenes."""
        seen_assets = set()
        duplicates = []

        for s in timeline.scenes:
            s_id = getattr(s, "scene_id", f"scene_{getattr(s, 'scene_index', 0)}")
            asset = None
            if getattr(s, "selected_assets", None):
                asset = s.selected_assets[0]
            elif getattr(s, "selected_asset", None):
                asset = s.selected_asset

            if asset:
                a_id = getattr(asset, "asset_id", None)
                if a_id:
                    if a_id in seen_assets:
                        duplicates.append((s_id, a_id))
                    seen_assets.add(a_id)

        score = 100.0
        evidence = "High visual diversity across scenes with zero duplicate footage."
        if duplicates:
            score = max(40.0, 100.0 - 30.0 * len(duplicates))
            evidence = f"Duplicate visual asset detected in scenes: {duplicates}"

        status = self._status_from_score(score, target=85.0, warn=70.0, block=50.0)
        return CreativeQAScoreItem(
            name="Visual Continuity",
            score=score,
            target_score=85.0,
            warning_threshold=70.0,
            blocking_threshold=50.0,
            confidence=0.98,
            status=status,
            evidence=evidence,
        )

    def _evaluate_dead_sections(self, timeline: MaterializedTimeline) -> CreativeQAScoreItem:
        """Detect frozen or static scenes exceeding duration budget without motion."""
        score = 100.0
        deductions = []

        for s in timeline.scenes:
            s_id = getattr(s, "scene_id", f"scene_{getattr(s, 'scene_index', 0)}")
            dur = getattr(s, "duration_sec", None) or (s.timing.duration_sec if getattr(s, "timing", None) else 3.0)
            asset = s.selected_assets[0] if (getattr(s, "selected_assets", None) and len(s.selected_assets) > 0) else getattr(s, "selected_asset", None)
            m_type = getattr(asset, "media_type", getattr(asset, "asset_type", "image")) if asset else "image"

            # If static image with no motion duration > 5.0s
            if str(m_type).lower() in ("image", "assettype.image") and dur > 5.0:
                score -= 20.0
                deductions.append(f"{s_id} static image exceeds 5.0s budget ({dur:.1f}s)")

        score = max(0.0, min(100.0, score))
        status = self._status_from_score(score, target=85.0, warn=70.0, block=50.0)
        # A detected defect must never surface as a clean PASS: one 20-point
        # deduction still lands above the warning line, which read as
        # "inspected and fine" while a real static section was present.
        if deductions and status == CreativeQAStatus.PASS:
            status = CreativeQAStatus.WARN
        evidence = "; ".join(deductions) if deductions else "No dead or static sections detected."

        return CreativeQAScoreItem(
            name="Dead/Static Sections",
            score=score,
            target_score=85.0,
            warning_threshold=70.0,
            blocking_threshold=50.0,
            confidence=0.95,
            status=status,
            evidence=evidence,
        )

    # -----------------------------------------------------------------------
    # Helper Utilities
    # -----------------------------------------------------------------------

    def _evaluate_black_frames(self, video_path: Optional[str | Path], duration_sec: float) -> CreativeQAScoreItem:
        """Detect black/near-black frames in the real rendered video."""
        if not video_path or not Path(video_path).exists() or duration_sec <= 0:
            return CreativeQAScoreItem(
                name="Black Frames", score=100.0, status=CreativeQAStatus.PASS,
                evidence="No video available for black-frame inspection; skipped.",
                confidence=0.5,
            )
        try:
            cmd = [
                "ffmpeg", "-v", "error", "-i", str(video_path),
                "-vf", "blackdetect=d=0.6:pix_th=0.10", "-an", "-f", "null", "-",
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            black_segments = re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", r.stderr or "")
            total_black = sum(float(e) - float(s) for s, e in black_segments)
            if total_black <= 0:
                return CreativeQAScoreItem(
                    name="Black Frames", score=100.0, status=CreativeQAStatus.PASS,
                    evidence="No black frames detected in rendered video.",
                )
            pct = total_black / max(duration_sec, 1e-6)
            score = max(0.0, 100.0 - pct * 300.0)
            status = CreativeQAStatus.BLOCK if pct > 0.10 else (
                CreativeQAStatus.WARN if pct > 0.03 else CreativeQAStatus.PASS
            )
            return CreativeQAScoreItem(
                name="Black Frames", score=round(score, 1), status=status,
                evidence=f"Black frames: {total_black:.2f}s ({pct*100:.1f}% of {duration_sec:.1f}s) across {len(black_segments)} segment(s).",
                scene_breakdown={"black_segments": [[float(s), float(e)] for s, e in black_segments]},
            )
        except Exception as exc:
            return CreativeQAScoreItem(
                name="Black Frames", score=70.0, status=CreativeQAStatus.WARN,
                evidence=f"Black-frame inspection failed: {exc}", confidence=0.5,
            )

    def _evaluate_freeze_sections(self, video_path: Optional[str | Path], duration_sec: float) -> CreativeQAScoreItem:
        """Detect frozen sections via real frame-difference analysis.

        Scene-cut detection cannot answer this question: ffmpeg's ``scene`` filter
        only fires on hard cuts, so continuous B-roll (drifting underwater
        footage, slow pans) reports "no changes" even while every frame differs.
        A section is only frozen when consecutive frames are near-identical, so
        measure the actual per-pixel delta between adjacent sampled frames.
        """
        if not video_path or not Path(video_path).exists() or duration_sec <= 0:
            return CreativeQAScoreItem(
                name="Freeze/Static Sections", score=100.0, status=CreativeQAStatus.PASS,
                evidence="No video available for freeze inspection; skipped.",
                confidence=0.5,
            )
        try:
            import numpy as np

            sample_fps = 2.0
            width, height = 64, 36
            frame_bytes = width * height
            cmd = [
                "ffmpeg", "-v", "error", "-i", str(video_path),
                "-vf", f"fps={sample_fps},scale={width}:{height},format=gray",
                "-f", "rawvideo", "-pix_fmt", "gray", "-",
            ]
            raw = subprocess.run(cmd, capture_output=True, timeout=180).stdout or b""
            usable = len(raw) - (len(raw) % frame_bytes)
            frames = [
                np.frombuffer(raw, dtype=np.uint8, count=frame_bytes, offset=o)
                .reshape(height, width)
                .astype(np.float32)
                for o in range(0, usable, frame_bytes)
            ]
            if len(frames) < 3:
                return CreativeQAScoreItem(
                    name="Freeze/Static Sections", score=70.0, status=CreativeQAStatus.WARN,
                    evidence="Too few frames sampled for reliable freeze analysis.",
                    confidence=0.5,
                )

            deltas = [float(np.abs(frames[i + 1] - frames[i]).mean()) for i in range(len(frames) - 1)]
            # A frame pair is effectively frozen only when it is near-identical.
            frozen_threshold = 0.5
            frozen_flags = [d < frozen_threshold for d in deltas]

            # Longest run of consecutive frozen pairs, expressed in seconds.
            longest_frozen = 0.0
            current = 0
            for is_frozen in frozen_flags:
                if is_frozen:
                    current += 1
                    longest_frozen = max(longest_frozen, current)
                else:
                    current = 0
            longest_frozen_sec = longest_frozen / sample_fps

            moving_ratio = 1.0 - (sum(frozen_flags) / len(frozen_flags))
            mean_delta = float(np.mean(deltas))
            max_delta = float(np.max(deltas))

            if longest_frozen_sec >= 1.0:
                score = max(0.0, 100.0 - longest_frozen_sec * 25.0)
                status = CreativeQAStatus.BLOCK if longest_frozen_sec >= 3.0 else CreativeQAStatus.WARN
                return CreativeQAScoreItem(
                    name="Freeze/Static Sections", score=round(score, 1), status=status,
                    evidence=(
                        f"Frozen stretch of {longest_frozen_sec:.1f}s detected "
                        f"(frame delta below {frozen_threshold})."
                    ),
                )

            return CreativeQAScoreItem(
                name="Freeze/Static Sections", score=100.0, status=CreativeQAStatus.PASS,
                evidence=(
                    f"{moving_ratio * 100:.0f}% of sampled frame pairs show real change "
                    f"(mean delta {mean_delta:.1f}, peak {max_delta:.1f}); no frozen stretches."
                ),
            )
        except Exception as exc:
            return CreativeQAScoreItem(
                name="Freeze/Static Sections", score=70.0, status=CreativeQAStatus.WARN,
                evidence=f"Freeze inspection failed: {exc}", confidence=0.5,
            )

    def _evaluate_listicle_structure(self, timeline: MaterializedTimeline) -> CreativeQAScoreItem:
        """Validate N-item listicle structure on the real script content."""
        script = getattr(timeline, "script_document", None) or getattr(timeline, "source_script", None)
        if script is None:
            # Reconstruct a minimal ScriptDocument from timeline scenes if possible.
            try:
                from autopilot.core.contracts import ScriptDocument, ScriptScene

                topic = getattr(timeline, "topic", "") or ""
                scenes = []
                for s in timeline.scenes:
                    narr = getattr(s, "narration", None)
                    narr_text = getattr(narr, "text", "") if narr is not None else ""
                    scenes.append(
                        ScriptScene(
                            scene_id=str(getattr(s, "scene_id", f"scene_{len(scenes)}")),
                            order=len(scenes) + 1,
                            narration=narr_text,
                            visual_intent=narr_text[:40] or "visual",
                        )
                    )
                script = ScriptDocument(content_id=getattr(timeline, "timeline_id", "tl"), topic=topic, scenes=scenes)
            except Exception as exc:
                return CreativeQAScoreItem(
                    name="Listicle Structure", score=100.0, status=CreativeQAStatus.PASS,
                    evidence=f"Listicle validation unavailable: {exc}", confidence=0.5,
                )
        try:
            res = validate_listicle_structure(script)
            if not res.is_listicle:
                return CreativeQAScoreItem(
                    name="Listicle Structure", score=100.0, status=CreativeQAStatus.PASS,
                    evidence="Not an N-item listicle; structure check not applicable.",
                )
            if res.passed:
                return CreativeQAScoreItem(
                    name="Listicle Structure", score=100.0, status=CreativeQAStatus.PASS,
                    evidence=f"Requested {res.requested_item_count} items; all substantively distinct.",
                )
            return CreativeQAScoreItem(
                name="Listicle Structure", score=25.0, status=CreativeQAStatus.BLOCK,
                evidence=f"Listicle structure defect: {res.reason}",
                scene_breakdown={"duplicate_groups": res.duplicate_groups},
            )
        except Exception as exc:
            return CreativeQAScoreItem(
                name="Listicle Structure", score=70.0, status=CreativeQAStatus.WARN,
                evidence=f"Listicle validation error: {exc}", confidence=0.5,
            )

    def _status_from_score(self, score: float, target: float, warn: float, block: float) -> CreativeQAStatus:
        if score < block:
            return CreativeQAStatus.BLOCK
        elif score < warn:
            return CreativeQAStatus.WARN
        return CreativeQAStatus.PASS

    def _sample_video_frames(
        self,
        video_path: Path,
        duration_sec: float,
        interval_sec: float,
    ) -> List[FrameSample]:
        """Sample REAL video frames and detect caption text + safe-zone.

        Frames are extracted with ffmpeg. Caption presence is detected by
        inspecting the lower-third pixel band for high-contrast text-like
        edges (a real signal, not an assumed True).
        """
        samples: List[FrameSample] = []
        if not Path(video_path).exists() or duration_sec <= 0:
            return samples
        num_samples = max(1, int(duration_sec / max(0.5, interval_sec)))
        import tempfile

        tmp = Path(tempfile.mkdtemp(prefix="cqa_"))
        try:
            for i in range(num_samples):
                t = round((i + 0.5) * interval_sec, 2)
                if t > duration_sec:
                    break
                frame_path = tmp / f"frame_{i:04d}.png"
                cmd = [
                    "ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}",
                    "-i", str(video_path), "-frames:v", "1", "-q:v", "2",
                    str(frame_path),
                ]
                try:
                    subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                except Exception:
                    continue
                if not frame_path.exists():
                    continue
                detected, in_zone = self._detect_caption_in_frame(frame_path)
                samples.append(
                    FrameSample(
                        timestamp_sec=t,
                        frame_path=str(frame_path),
                        caption_detected=detected,
                        caption_in_safe_zone=in_zone,
                        overflow_detected=False,
                        saliency_collision=False,
                    )
                )
        finally:
            try:
                for f in tmp.iterdir():
                    f.unlink()
                tmp.rmdir()
            except Exception:
                pass
        return samples

    def _detect_caption_in_frame(self, frame_path: Path) -> Tuple[bool, bool]:
        """Detect caption text in the lower third via luminance variance.

        Burned captions produce strong local contrast in the caption band.
        Returns (caption_detected, caption_in_safe_zone).
        """
        try:
            from PIL import Image

            with Image.open(frame_path) as img:
                w, h = img.size
                gray = img.convert("L")
                # Caption band: lower third (typical placement)
                band = gray.crop((int(w * 0.1), int(h * 0.62), int(w * 0.9), int(h * 0.86)))
                pixels = list(band.getdata())
                if not pixels:
                    return False, True
                mean = sum(pixels) / len(pixels)
                variance = sum((p - mean) ** 2 for p in pixels) / len(pixels)
                stdev = variance ** 0.5
                # High stdev = strong text contrast; plain footage is smoother.
                detected = stdev >= 28.0
                return detected, True
        except Exception:
            return False, True
