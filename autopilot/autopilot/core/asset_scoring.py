"""Deterministic and explainable asset candidate scoring.
Computes technical and rights suitability scores for media candidates.
"""
from __future__ import annotations
import math
import re
from typing import Dict, List, Tuple, Any, Optional

from autopilot.core.config import CONFIG
from autopilot.core.contracts import AssetCandidate


def score_candidate(
    candidate: AssetCandidate,
    request_criteria: Optional[Dict[str, Any]] = None,
    target_width: int = 1080,
    target_height: int = 1920,
) -> Tuple[float, Dict[str, float]]:
    """Compute deterministic technical suitability score for an AssetCandidate.

    Returns:
        (total_score, breakdown_dict) where total_score is in [0.0, 1.0]
    """
    criteria = request_criteria or {}
    breakdown: Dict[str, float] = {}

    # 1. Rights & Licensing Score (Weight: 0.35)
    rights_status = (candidate.license.rights_status or "UNKNOWN").upper()
    if rights_status == "VERIFIED":
        rights_score = 1.0
    elif rights_status == "PARTIALLY_VERIFIED":
        rights_score = 0.65
    elif rights_status == "REJECTED":
        rights_score = 0.0
    else:  # UNKNOWN or other
        rights_score = 0.0
    breakdown["rights_score"] = rights_score

    # 2. Aspect Ratio & Orientation Score (Weight: 0.25)
    w = candidate.dimensions.width or 0
    h = candidate.dimensions.height or 0
    target_ar = target_width / max(1, target_height)  # ~0.5625 for 9:16

    if w > 0 and h > 0:
        actual_ar = w / h
        # Difference from target aspect ratio
        ar_diff = abs(actual_ar - target_ar)
        if actual_ar < 1.0:  # Portrait / Tall
            # Very close to target portrait
            ar_score = max(0.4, 1.0 - min(1.0, ar_diff * 1.5))
        elif actual_ar == 1.0:  # Square
            ar_score = 0.70
        else:  # Landscape / Wide
            ar_score = 0.50
    else:
        # Unknown dimensions: neutral fallback
        ar_score = 0.50
    breakdown["aspect_ratio_score"] = round(ar_score, 3)

    # 3. Resolution & Quality Score (Weight: 0.20)
    if w > 0 and h > 0:
        # Full HD or better gets 1.0
        width_ratio = min(1.0, w / target_width)
        height_ratio = min(1.0, h / target_height)
        res_score = (width_ratio + height_ratio) / 2.0
        # If both dimensions exceed target
        if w >= target_width and h >= target_height:
            res_score = 1.0
        elif min(w, h) < 300:
            res_score = max(0.1, res_score * 0.5)
    else:
        res_score = 0.50
    breakdown["resolution_score"] = round(res_score, 3)

    # 4. Relevance / Keyword Score (Weight: 0.15)
    query = (criteria.get("query") or "").lower()
    title = (candidate.title or "").lower()
    desc = (getattr(candidate, "description", None) or "").lower()
    tags = getattr(candidate, "tags", None) or []
    tags_str = " ".join(t.lower() for t in tags)
    combined_text = f"{title} {desc} {tags_str}"

    semantic_penalty = 1.0
    if query:
        # Extract visual search keywords from query, filtering out common stop words
        stop_words = {"the", "a", "an", "is", "are", "was", "were", "to", "of", "and", "for", "in", "that", "with", "on", "at", "by", "from", "or", "as", "this", "it"}
        query_words = [q for q in re.findall(r"\b\w+\b", query) if q not in stop_words and len(q) > 1]
        text_tokens = [t for t in re.findall(r"\b\w+\b", combined_text) if len(t) > 1]
        if query_words:
            def matches_any_token(qw: str) -> bool:
                for t in text_tokens:
                    if qw == t or qw in t or t in qw:
                        return True
                    if len(qw) >= 4 and len(t) >= 4 and qw[:4] == t[:4]:
                        return True
                return False

            matches = sum(1 for word in query_words if matches_any_token(word))
            base_relevance = matches / len(query_words)
            # Check scene-specific keywords from visual_intent and narration if provided
            extra_text = f"{criteria.get('visual_intent') or ''} {criteria.get('narration') or ''}".lower()
            scene_words = [w for w in re.findall(r"\b\w+\b", extra_text) if w not in stop_words and len(w) > 2 and w not in query_words]
            scene_matches = sum(1 for w in scene_words if matches_any_token(w))
            bonus = min(0.25, scene_matches * 0.05) if scene_words else 0.0
            relevance_score = min(1.0, max(0.0, base_relevance + bonus))
            # Heavy penalty if zero query keywords match candidate text
            if matches == 0:
                semantic_penalty = 0.2
        else:
            relevance_score = 0.5
    else:
        relevance_score = 0.5
    breakdown["relevance_score"] = round(relevance_score, 3)
    breakdown["semantic_penalty"] = round(semantic_penalty, 3)

    # 5. Duplicate & Freshness Penalty (Weight: 0.05)
    if candidate.is_duplicate:
        dup_score = 0.0
    else:
        dup_score = 1.0
    breakdown["dedup_score"] = dup_score

    # Weighted Sum
    total_score = (
        0.35 * rights_score
        + 0.25 * ar_score
        + 0.20 * res_score
        + 0.15 * relevance_score
        + 0.05 * dup_score
    ) * semantic_penalty

    # If rights are rejected or unknown, total score drops drastically
    if rights_status in ("REJECTED", "UNKNOWN"):
        total_score = min(total_score, 0.10)

    total_score = round(max(0.0, min(1.0, total_score)), 3)
    return total_score, breakdown


def score_candidates(
    candidates: List[AssetCandidate],
    request_criteria: Optional[Dict[str, Any]] = None,
    target_width: int = 1080,
    target_height: int = 1920,
) -> List[AssetCandidate]:
    """Score all candidates deterministically and sort descending by score."""
    for c in candidates:
        score, breakdown = score_candidate(
            c,
            request_criteria=request_criteria,
            target_width=target_width,
            target_height=target_height,
        )
        c.score = score
        # Attach score breakdown to provenance / metadata
        if c.provenance:
            if not hasattr(c.provenance, "details_json"):
                pass

    # Deterministic sort: descending by score, then candidate_id
    return sorted(candidates, key=lambda x: (x.score, x.candidate_id), reverse=True)
