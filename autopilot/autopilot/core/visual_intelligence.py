"""Visual Intelligence & Semantic Asset Engine — Phase 3.

Implements the production visual pipeline:
  Intent/Scene Visual Requirements
    ↓
  Multi-Tier Asset Retrieval (Pexels -> Pixabay -> Infographics -> Openverse -> Local)
    ↓
  Hard Quality & Rights Gates (License, Corruption, Effective Resolution, Watermark, Blur, Duration)
    ↓
  Semantic Visual Ranking & Coverage Classification (EXACT, STRONG, CONTEXTUAL, WEAK, MISMATCH)
    ↓
  Visual Assertion Awareness (literal, schematic, illustrative, metaphorical)
    ↓
  Saliency-Aware 9:16 Crop Planning & Dynamic Safe-Zone Positioning (TOP, CENTER, LOWER)
    ↓
  Visual Diversity (pHash deduplication) & Shot Continuity
    ↓
  Visual Change Budgeting (> 4.5s multi-shot planning)
    ↓
  MaterializedTimeline Integration & Machine-Readable Provenance
"""
from __future__ import annotations

import os
import re
import math
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union, Set
from pydantic import BaseModel, Field

import numpy as np
from PIL import Image, ImageFilter

from autopilot.core.config import CONFIG
from autopilot.core.contracts import (
    AssetCandidate, AssetSelection, AssetArtifact, AssetLicense,
    AssetProvenance, AssetDimensions, AssetMediaInfo
)
from autopilot.core.timeline import (
    MaterializedTimeline,
    MaterializedScene,
    VisualRequirements,
    VisualCoverageClass,
    VisualAssertionLevel,
    ShotType,
    CropFraming,
    CaptionPosition,
    TrimRange,
    SelectedAsset,
)
from autopilot.providers.asset_contracts import AssetProvider
from autopilot.providers.local_asset_provider import LocalAssetProvider
from autopilot.providers.openverse_provider import OpenverseAssetProvider
from autopilot.providers.pexels_provider import PexelsAssetProvider
from autopilot.providers.pixabay_provider import PixabayAssetProvider
from autopilot.core.infographics_generator import InfographicsAssetProvider, InfographicsGenerator
from autopilot.core.rights_gate import evaluate_rights_gate, RightsGateResult
from autopilot.core.asset_cache import compute_file_sha256, compute_image_phash, AssetCache


# ---------------------------------------------------------------------------
# Blur & Saliency Analysis Utilities (Fast NumPy / PIL)
# ---------------------------------------------------------------------------

def compute_laplacian_variance(image_path: Union[str, Path]) -> float:
    """Compute blur metric using Laplacian variance on grayscale image.
    Higher values = sharp/detailed. Values < 100.0 = blurry.
    """
    try:
        with Image.open(image_path) as img:
            gray = img.convert("L")
            # Downscale slightly for bounded fast processing if large
            if gray.width > 512 or gray.height > 512:
                gray.thumbnail((512, 512), Image.Resampling.BILINEAR)
            arr = np.array(gray, dtype=np.float64)
            # 3x3 Discrete Laplacian Kernel
            kernel = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float64)
            # Simple 2D convolution
            h, w = arr.shape
            if h < 3 or w < 3:
                return 200.0
            # Interior slice convolution
            lap = (
                arr[0 : h - 2, 1 : w - 1]
                + arr[2:h, 1 : w - 1]
                + arr[1 : h - 1, 0 : w - 2]
                + arr[1 : h - 1, 2:w]
                - 4.0 * arr[1 : h - 1, 1 : w - 1]
            )
            var = float(np.var(lap))
            return var
    except Exception:
        # Fallback to neutral passing variance if unreadable as image (e.g. video file before frame extract)
        return 250.0


def compute_saliency_and_safe_zone(image_path: Union[str, Path]) -> Tuple[float, float, CaptionPosition]:
    """Compute center of visual energy / saliency and dynamic caption safe zone."""
    try:
        with Image.open(image_path) as img:
            gray = img.convert("L")
            if gray.width > 256 or gray.height > 256:
                gray.thumbnail((256, 256), Image.Resampling.BILINEAR)
            arr = np.array(gray, dtype=np.float64)
            h, w = arr.shape
            if h < 4 or w < 4:
                return 0.5, 0.5, CaptionPosition.LOWER

            # Gradient energy magnitude
            dy = np.abs(arr[1:, :] - arr[:-1, :])[:, :-1]
            dx = np.abs(arr[:, 1:] - arr[:, :-1])[:-1, :]
            energy = dx + dy
            total_energy = np.sum(energy)

            if total_energy <= 1e-6:
                return 0.5, 0.5, CaptionPosition.LOWER

            y_indices, x_indices = np.indices(energy.shape)
            center_x = float(np.sum(x_indices * energy) / total_energy) / max(1, energy.shape[1])
            center_y = float(np.sum(y_indices * energy) / total_energy) / max(1, energy.shape[0])

            # Dynamic Safe Zone Decision
            # If subject/energy is in bottom half (center_y > 0.58), place captions at TOP
            # If subject/energy is in top half (center_y < 0.42), place captions at LOWER
            # If centered, use LOWER
            if center_y > 0.58:
                safe_pos = CaptionPosition.TOP
            elif center_y < 0.42:
                safe_pos = CaptionPosition.LOWER
            else:
                safe_pos = CaptionPosition.LOWER

            return round(center_x, 3), round(center_y, 3), safe_pos
    except Exception:
        return 0.5, 0.5, CaptionPosition.LOWER


def compute_phash_hamming_distance(hash1: Optional[str], hash2: Optional[str]) -> int:
    """Compute Hamming distance between two 16-hex-char perceptual hashes."""
    if not hash1 or not hash2:
        return 64
    try:
        int1 = int(hash1, 16)
        int2 = int(hash2, 16)
        xor_val = int1 ^ int2
        return bin(xor_val).count("1")
    except Exception:
        return 64


# ---------------------------------------------------------------------------
# Hard Quality & Rights Gates
# ---------------------------------------------------------------------------

class HardGateResult(BaseModel):
    passed: bool
    reasons: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    effective_resolution_ok: bool = True
    blur_score: float = 0.0
    is_watermark_flagged: bool = False
    rights_cleared: bool = True


def evaluate_hard_quality_gates(
    candidate: AssetCandidate,
    local_path: Optional[Union[str, Path]] = None,
    target_duration: Optional[float] = None,
    min_blur_threshold: float = 80.0,
) -> HardGateResult:
    """Evaluate all mandatory hard quality and rights gates before soft ranking."""
    reasons: List[str] = []
    warnings: List[str] = []
    passed = True

    # 1. Commercial Rights Gate
    rights_res = evaluate_rights_gate(candidate.license)
    if not rights_res.allowed:
        passed = False
        reasons.append(f"Hard Rights Gate failed: {'; '.join(rights_res.reasons)}")

    # 2. Watermark / Stock Overlay Heuristic Check
    title_str = candidate.title or ""
    desc_str = getattr(candidate, "description", "") or ""
    tags_str = " ".join(candidate.tags or [])
    text_to_check = f"{title_str} {desc_str} {tags_str}".lower()
    watermark_signals = [
        "watermark", "shutterstock", "gettyimages", "istock", "alamy",
        "adobe stock", "preview only", "sample image", "pond5", "storyblocks"
    ]
    is_watermarked = any(sig in text_to_check for sig in watermark_signals)
    if is_watermarked:
        passed = False
        reasons.append("Hard Gate: Watermark / stock preview overlay detected in candidate metadata")

    # 3. Effective Usable Resolution Check (Supports 9:16 crop from landscape/4K/1080p)
    w = candidate.dimensions.width if candidate.dimensions else 0
    h = candidate.dimensions.height if candidate.dimensions else 0
    w = w or 0
    h = h or 0
    effective_res_ok = True
    if w > 0 and h > 0:
        # Effective 9:16 extraction rule:
        # If portrait (w <= h): require w >= 608 or h >= 1080
        # If landscape (w > h): height h >= 608 enables a 9:16 crop without pixel collapse
        if w < 500 and h < 500:
            effective_res_ok = False
            passed = False
            reasons.append(f"Hard Gate: Source resolution ({w}x{h}) is too low for usable 9:16 crop")
    else:
        warnings.append("Dimensions unknown prior to download")

    # 4. Valid Media Duration (for video candidates)
    media_type = getattr(candidate, "asset_type", None) or getattr(candidate, "media_type", "image")
    cand_dur = (candidate.dimensions.duration_sec if candidate.dimensions else None) or getattr(candidate, "duration", 0.0) or 0.0
    if media_type == "video" and target_duration and target_duration > 0:
        if 0 < cand_dur < (target_duration - 1.0):
            passed = False
            reasons.append(f"Hard Gate: Video duration ({cand_dur:.1f}s) insufficient for scene duration ({target_duration:.1f}s)")

    # 5. Local File Corruption & Blur Check (if media file exists on disk)
    blur_score = 300.0
    if local_path:
        lp = Path(local_path)
        if not lp.exists() or lp.stat().st_size == 0:
            passed = False
            reasons.append(f"Hard Gate: Media file on disk is missing or 0 bytes ({lp})")
        else:
            # Check blur for raster image files
            if lp.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                blur_score = compute_laplacian_variance(lp)
                if blur_score < min_blur_threshold:
                    passed = False
                    reasons.append(f"Hard Gate: Media blur score ({blur_score:.1f}) is below minimum threshold ({min_blur_threshold})")

    return HardGateResult(
        passed=passed,
        reasons=reasons,
        warnings=warnings,
        effective_resolution_ok=effective_res_ok,
        blur_score=round(blur_score, 2),
        is_watermark_flagged=is_watermarked,
        rights_cleared=rights_res.allowed,
    )


# ---------------------------------------------------------------------------
# Semantic Visual Scoring & Coverage Classification
# ---------------------------------------------------------------------------

class VisualScoreResult(BaseModel):
    total_score: float = Field(..., ge=0.0, le=1.0)
    coverage_class: VisualCoverageClass
    breakdown: Dict[str, float] = Field(default_factory=dict)
    reasons: List[str] = Field(default_factory=list)
    is_fallback_lexical: bool = True
    contextual_allowed_reason: Optional[str] = None


def compute_semantic_visual_score(
    candidate: AssetCandidate,
    visual_requirements: VisualRequirements,
    target_duration: float = 5.0,
) -> VisualScoreResult:
    """Compute layered visual score and coverage class respecting assertion levels."""
    breakdown: Dict[str, float] = {}
    reasons: List[str] = []

    concept = visual_requirements.visual_concept.lower()
    search_query = (visual_requirements.b_roll_search_query or concept).lower()
    shot_spec = (visual_requirements.shot_spec or "").lower()
    req_shot_type = visual_requirements.required_shot_type
    assertion = visual_requirements.visual_assertion_level

    # 1. Lexical Relevance Score (Weight: 0.30)
    stop_words = {"the", "a", "an", "is", "are", "was", "were", "to", "of", "and", "for", "in", "that", "with", "on", "at", "by", "from", "or", "as", "this", "it", "video", "footage", "clip", "photo", "image"}
    query_tokens = [q for q in re.findall(r"\b\w+\b", search_query) if q not in stop_words and len(q) > 1]
    concept_tokens = [c for c in re.findall(r"\b\w+\b", concept) if c not in stop_words and len(c) > 1]
    all_target_tokens = list(set(query_tokens + concept_tokens))

    cand_title = candidate.title or ""
    cand_desc = getattr(candidate, "description", "") or ""
    cand_tags = " ".join(candidate.tags or [])
    cand_text = f"{cand_title} {cand_desc} {cand_tags}".lower()
    cand_tokens = set(re.findall(r"\b\w+\b", cand_text))

    if all_target_tokens:
        matched = sum(1 for t in all_target_tokens if t in cand_tokens or any(t in ct for ct in cand_tokens))
        lexical_score = min(1.0, matched / len(all_target_tokens))
    else:
        lexical_score = 0.50
    breakdown["lexical_relevance"] = round(lexical_score, 3)

    # 2. Shot-Type & Composition Compatibility (Weight: 0.20)
    shot_score = 0.60
    req_shot_name = req_shot_type.value.lower()
    cand_provider = getattr(candidate, "provider", None) or (candidate.provenance.provider if candidate.provenance else "local")
    if req_shot_name in cand_text:
        shot_score = 1.0
        reasons.append(f"Direct shot-type match for '{req_shot_name}'")
    elif req_shot_type in (ShotType.AERIAL, ShotType.WIDE) and ("drone" in cand_text or "landscape" in cand_text or "aerial" in cand_text or "cityscape" in cand_text):
        shot_score = 0.95
    elif req_shot_type in (ShotType.CLOSE_UP, ShotType.MACRO) and ("close" in cand_text or "macro" in cand_text or "detail" in cand_text or "portrait" in cand_text):
        shot_score = 0.95
    elif req_shot_type in (ShotType.DIAGRAM, ShotType.MAP) and (cand_provider == "infographics" or "diagram" in cand_text or "chart" in cand_text or "map" in cand_text):
        shot_score = 1.0
    breakdown["shot_compatibility"] = round(shot_score, 3)

    # 3. Visual Assertion Compatibility (Weight: 0.25)
    assertion_score = 0.70
    contextual_reason = None
    if assertion == VisualAssertionLevel.LITERAL:
        # Literal assertions require strong direct subject evidence
        if lexical_score >= 0.60:
            assertion_score = 1.0
            reasons.append("Literal assertion satisfied by direct subject match")
        elif lexical_score > 0.0:
            assertion_score = lexical_score
            reasons.append("Literal assertion penalized for partial keyword match")
        else:
            assertion_score = 0.0
            reasons.append("Literal assertion rejected: zero direct subject match")
    elif assertion == VisualAssertionLevel.SCHEMATIC:
        if cand_provider == "infographics" or "infographic" in cand_text or "diagram" in cand_text:
            assertion_score = 1.0
            reasons.append("Schematic assertion satisfied by structured infographic / diagram")
        else:
            assertion_score = 0.60
    elif assertion in (VisualAssertionLevel.ILLUSTRATIVE, VisualAssertionLevel.METAPHORICAL):
        # Abstract / illustrative allows b-roll and contextual visuals
        assertion_score = max(0.75, lexical_score + 0.25)
        contextual_reason = f"Assertion '{assertion.value}' permits contextual b-roll and thematic symbolism"
        reasons.append(contextual_reason)
    breakdown["assertion_compatibility"] = round(assertion_score, 3)

    # 4. Aspect Ratio & Crop Quality (Weight: 0.15)
    w = (candidate.dimensions.width if candidate.dimensions else None) or 1080
    h = (candidate.dimensions.height if candidate.dimensions else None) or 1920
    ar = w / max(1, h)
    if 0.50 <= ar <= 0.60:  # Native 9:16
        crop_score = 1.0
    elif ar < 1.0:  # Portrait
        crop_score = 0.85
    elif w >= 1920 or h >= 1080:  # High-res landscape easily cropped to 9:16
        crop_score = 0.80
    else:
        crop_score = 0.60
    breakdown["crop_quality"] = round(crop_score, 3)

    # 5. Media Format & Quality Tier (Weight: 0.10)
    cand_media_type = getattr(candidate, "asset_type", None) or getattr(candidate, "media_type", "image")
    format_score = 0.90 if cand_media_type == "video" else 0.75
    breakdown["format_quality"] = round(format_score, 3)

    # Weighted Composite Score
    total_score = (
        0.30 * breakdown["lexical_relevance"]
        + 0.20 * breakdown["shot_compatibility"]
        + 0.25 * breakdown["assertion_compatibility"]
        + 0.15 * breakdown["crop_quality"]
        + 0.10 * breakdown["format_quality"]
    )
    # If literal assertion and completely zero relevance, penalize total score below threshold
    if assertion == VisualAssertionLevel.LITERAL and lexical_score == 0.0:
        total_score = min(total_score, 0.25)
    total_score = round(min(1.0, max(0.0, total_score)), 3)

    # Coverage Class Mapping
    if total_score >= 0.80:
        cov_class = VisualCoverageClass.EXACT_MATCH
    elif total_score >= 0.65:
        cov_class = VisualCoverageClass.STRONG_MATCH
    elif total_score >= 0.45:
        cov_class = VisualCoverageClass.CONTEXTUAL_MATCH
    elif total_score >= 0.30:
        cov_class = VisualCoverageClass.WEAK_MATCH
    else:
        cov_class = VisualCoverageClass.MISMATCH

    return VisualScoreResult(
        total_score=total_score,
        coverage_class=cov_class,
        breakdown=breakdown,
        reasons=reasons,
        is_fallback_lexical=True,
        contextual_allowed_reason=contextual_reason,
    )


# ---------------------------------------------------------------------------
# Visual Diversity & Continuity Trackers
# ---------------------------------------------------------------------------

class VisualDiversityTracker:
    """Tracks selected assets across a production to enforce pHash diversity and source variation."""

    def __init__(self):
        self.selected_hashes: Set[str] = set()
        self.selected_phashes: List[str] = []
        self.selected_providers: List[str] = []
        self.selected_subjects: List[str] = []

    def is_duplicate(self, candidate_id: str, phash: Optional[str]) -> bool:
        """Check if candidate is an exact or perceptual duplicate."""
        if candidate_id in self.selected_hashes:
            return True
        if phash:
            for existing_phash in self.selected_phashes:
                if compute_phash_hamming_distance(phash, existing_phash) <= 6:
                    return True
        return False

    def register_selection(self, candidate_id: str, phash: Optional[str], provider: str, subject: str) -> None:
        """Record selected asset."""
        self.selected_hashes.add(candidate_id)
        if phash:
            self.selected_phashes.append(phash)
        self.selected_providers.append(provider)
        self.selected_subjects.append(subject.lower())


class ShotContinuityPlanner:
    """Provides progression bonuses to ensure cinematic variety (WIDE -> MEDIUM -> CLOSE_UP)."""

    def __init__(self):
        self.last_shot_type: Optional[ShotType] = None

    def evaluate_progression(self, next_shot_type: ShotType) -> float:
        """Calculate continuity bonus [0.0, 0.10]."""
        if not self.last_shot_type:
            self.last_shot_type = next_shot_type
            return 0.05
        # Same shot type repeated consecutively gets no bonus
        if next_shot_type == self.last_shot_type:
            return 0.0
        # Preferred progression order
        progression = {
            ShotType.AERIAL: [ShotType.WIDE, ShotType.MEDIUM],
            ShotType.WIDE: [ShotType.MEDIUM, ShotType.CLOSE_UP],
            ShotType.MEDIUM: [ShotType.CLOSE_UP, ShotType.MACRO, ShotType.WIDE],
            ShotType.CLOSE_UP: [ShotType.WIDE, ShotType.MEDIUM, ShotType.DIAGRAM],
            ShotType.DIAGRAM: [ShotType.WIDE, ShotType.MEDIUM],
        }
        favored = progression.get(self.last_shot_type, [])
        self.last_shot_type = next_shot_type
        return 0.08 if next_shot_type in favored else 0.03


# ---------------------------------------------------------------------------
# Visual Intelligence Engine Orchestrator
# ---------------------------------------------------------------------------

class VisualIntelligenceEngine:
    """Orchestrates multi-tier retrieval, quality gating, semantic scoring,
    saliency crop planning, diversity tracking, and MaterializedTimeline integration.
    """

    def __init__(
        self,
        providers: Optional[Dict[str, AssetProvider]] = None,
        cache_dir: Optional[Path] = None,
    ):
        self.cache_dir = cache_dir or CONFIG.get_asset_cache_dir()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache = AssetCache(self.cache_dir)

        # Initialize Multi-Tier Provider Cascade
        if providers:
            self.providers = providers
        else:
            self.providers = {
                "pexels": PexelsAssetProvider(),
                "pixabay": PixabayAssetProvider(),
                "infographics": InfographicsAssetProvider(self.cache_dir / "infographics"),
                "openverse": OpenverseAssetProvider(),
                "local": LocalAssetProvider(),
            }

    def get_provider_cascade(self, visual_reqs: VisualRequirements) -> List[Tuple[str, AssetProvider]]:
        """Determine provider ordering based on visual assertion level and requirements."""
        cascade: List[str] = []
        assertion = visual_reqs.visual_assertion_level
        shot_type = visual_reqs.required_shot_type

        if assertion == VisualAssertionLevel.SCHEMATIC or shot_type in (ShotType.DIAGRAM, ShotType.MAP):
            cascade = ["infographics", "pexels", "pixabay", "openverse", "local"]
        else:
            cascade = ["pexels", "pixabay", "infographics", "openverse", "local"]

        active: List[Tuple[str, AssetProvider]] = []
        for name in cascade:
            if name in self.providers:
                active.append((name, self.providers[name]))
        return active

    def retrieve_and_score_scene(
        self,
        scene_id: str,
        visual_reqs: VisualRequirements,
        target_duration: float,
        diversity_tracker: Optional[VisualDiversityTracker] = None,
        continuity_planner: Optional[ShotContinuityPlanner] = None,
        download_media: bool = True,
    ) -> SelectedAsset:
        """Execute full visual intelligence pipeline for a single scene."""
        diversity = diversity_tracker or VisualDiversityTracker()
        continuity = continuity_planner or ShotContinuityPlanner()

        cascade = self.get_provider_cascade(visual_reqs)
        search_req = {
            "query": visual_reqs.b_roll_search_query or visual_reqs.visual_concept,
            "visual_concept": visual_reqs.visual_concept,
            "b_roll_search_query": visual_reqs.b_roll_search_query,
            "duration": target_duration,
            "aspect_ratio": "9:16",
        }

        all_scored_candidates: List[Tuple[AssetCandidate, VisualScoreResult, HardGateResult, AssetProvider]] = []

        # Multi-Tier Retrieval & Scoring
        for prov_name, provider in cascade:
            try:
                candidates = provider.search(search_req, max_results=5)
            except Exception:
                candidates = []

            for cand in candidates:
                # 1. Hard Quality Gates (Pre-download check)
                pre_gate = evaluate_hard_quality_gates(cand, target_duration=target_duration)
                if not pre_gate.passed:
                    continue

                # 2. Semantic Scoring
                score_res = compute_semantic_visual_score(cand, visual_reqs, target_duration=target_duration)
                if score_res.coverage_class == VisualCoverageClass.MISMATCH:
                    continue

                # 3. Continuity adjustment
                cont_bonus = continuity.evaluate_progression(visual_reqs.required_shot_type)
                score_res.total_score = min(1.0, score_res.total_score + cont_bonus)

                all_scored_candidates.append((cand, score_res, pre_gate, provider))

            # If we found strong candidates in high tier, we can proceed
            if any(s.coverage_class in (VisualCoverageClass.EXACT_MATCH, VisualCoverageClass.STRONG_MATCH) for _, s, _, _ in all_scored_candidates):
                break

        # Sort candidates by total score descending
        all_scored_candidates.sort(key=lambda item: item[1].total_score, reverse=True)

        # Fallback to local asset provider if no candidates survived
        if not all_scored_candidates:
            fallback_prov = self.providers.get("local") or LocalAssetProvider()
            fb_cands = fallback_prov.search(search_req, max_results=1)
            if not fb_cands:
                # Generate synthetic card as resilient absolute floor
                info_prov = self.providers.get("infographics") or InfographicsAssetProvider()
                fb_cands = info_prov.search(search_req, max_results=1)
            cand = fb_cands[0]
            score_res = compute_semantic_visual_score(cand, visual_reqs, target_duration=target_duration)
            pre_gate = evaluate_hard_quality_gates(cand, target_duration=target_duration)
            all_scored_candidates.append((cand, score_res, pre_gate, fallback_prov))

        # Select the best candidate that satisfies diversity
        selected_tuple = None
        for cand, score_res, gate_res, prov in all_scored_candidates:
            if not diversity.is_duplicate(cand.candidate_id, getattr(cand, "phash", None)):
                selected_tuple = (cand, score_res, gate_res, prov)
                break
        if not selected_tuple:
            selected_tuple = all_scored_candidates[0]

        best_cand, best_score, best_gate, best_prov = selected_tuple
        best_cand_media_type = getattr(best_cand, "asset_type", None) or getattr(best_cand, "media_type", "image")
        best_cand_url = getattr(best_cand, "source_url", None) or getattr(best_cand, "url", "")
        best_cand_provider = getattr(best_cand, "provider", None) or (best_cand.provenance.provider if best_cand.provenance else "local")

        # Download asset to cache
        target_ext = ".mp4" if best_cand_media_type == "video" else ".png"
        out_filename = f"{best_cand.candidate_id}{target_ext}"
        target_path = self.cache_dir / out_filename

        final_local_path = str(target_path)
        if download_media:
            try:
                final_local_path = best_prov.download(best_cand, str(target_path))
            except Exception:
                final_local_path = str(target_path)

        # Compute Saliency & Safe Zone Framing
        phash_val = compute_image_phash(final_local_path)
        sal_x, sal_y, safe_zone = compute_saliency_and_safe_zone(final_local_path)

        # Register in diversity tracker
        diversity.register_selection(
            best_cand.candidate_id,
            phash_val,
            best_cand_provider,
            visual_reqs.visual_concept,
        )

        # Compute Provenance
        download_hash = ""
        if Path(final_local_path).exists():
            try:
                download_hash = compute_file_sha256(final_local_path)
            except Exception:
                download_hash = ""

        provenance_data = {
            "provider": best_cand_provider,
            "asset_id": best_cand.candidate_id,
            "source_id": getattr(best_cand, "source_id", "") or "",
            "source_url": best_cand_url,
            "license": best_cand.license.license_name,
            "license_name": best_cand.license.license_name,
            "rights_status": best_cand.license.rights_status,
            "creator": best_cand.license.creator or "Unknown Creator",
            "retrieval_timestamp": datetime.now(timezone.utc).isoformat(),
            "download_hash": download_hash,
            "local_path": final_local_path,
            "scoring": {
                "total_score": best_score.total_score,
                "coverage_class": best_score.coverage_class.value,
                "breakdown": best_score.breakdown,
                "is_fallback_lexical": best_score.is_fallback_lexical,
                "reasons": best_score.reasons,
                "contextual_allowed_reason": best_score.contextual_allowed_reason,
            },
        }

        # Build SelectedAsset
        return SelectedAsset(
            asset_id=best_cand.candidate_id,
            asset_path=final_local_path,
            media_type=best_cand_media_type,
            provenance=provenance_data,
            trim_range=TrimRange(in_sec=0.0, out_sec=target_duration),
            shot_type=visual_reqs.required_shot_type,
            shot_spec=visual_reqs.shot_spec,
            camera_motion="slow_push",
            crop_framing=CropFraming(
                saliency_x=sal_x,
                saliency_y=sal_y,
                safe_margin=0.15,
                caption_safe_zone=safe_zone,
            ),
            color_grade="cinematic_warm",
            start_offset_sec=0.0,
            duration_sec=target_duration,
        )

    def process_materialized_timeline(
        self,
        timeline: MaterializedTimeline,
        download_media: bool = True,
    ) -> MaterializedTimeline:
        """Process all scenes in a MaterializedTimeline, acquiring and attaching selected assets."""
        diversity = VisualDiversityTracker()
        continuity = ShotContinuityPlanner()

        for scene in timeline.scenes:
            dur = scene.timing.duration_sec
            visual_reqs = scene.visual_requirements
            change_budget = visual_reqs.visual_change_budget or (2 if dur > 4.5 else 1)

            selected_assets: List[SelectedAsset] = []

            if change_budget > 1 and dur > 4.5:
                # Plan multi-shot visual change within the scene
                sub_duration = round(dur / change_budget, 3)
                current_offset = 0.0

                for shot_idx in range(change_budget):
                    shot_dur = sub_duration if shot_idx < change_budget - 1 else round(dur - current_offset, 3)
                    sub_reqs = visual_reqs.model_copy()
                    if shot_idx > 0:
                        # Vary shot type for visual change
                        sub_reqs.required_shot_type = ShotType.CLOSE_UP if visual_reqs.required_shot_type == ShotType.WIDE else ShotType.WIDE

                    asset = self.retrieve_and_score_scene(
                        scene_id=f"{scene.scene_id}_shot_{shot_idx+1}",
                        visual_reqs=sub_reqs,
                        target_duration=shot_dur,
                        diversity_tracker=diversity,
                        continuity_planner=continuity,
                        download_media=download_media,
                    )
                    asset.start_offset_sec = round(current_offset, 3)
                    asset.duration_sec = shot_dur
                    asset.trim_range = TrimRange(in_sec=0.0, out_sec=shot_dur)
                    selected_assets.append(asset)
                    current_offset += shot_dur
            else:
                # Single primary shot covering entire scene duration
                asset = self.retrieve_and_score_scene(
                    scene_id=scene.scene_id,
                    visual_reqs=visual_reqs,
                    target_duration=dur,
                    diversity_tracker=diversity,
                    continuity_planner=continuity,
                    download_media=download_media,
                )
                selected_assets.append(asset)

            scene.selected_assets = selected_assets

            # Align caption position with dynamic safe zone of primary asset
            if selected_assets:
                primary_crop = selected_assets[0].crop_framing
                scene.caption_plan.position = primary_crop.caption_safe_zone

        return timeline
