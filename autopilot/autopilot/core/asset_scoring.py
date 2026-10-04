"""Deterministic and explainable asset candidate scoring (P0 rewrite).

The semantic decision is now driven by REAL visual-semantic similarity
measured on downloaded pixels by the local CLIP vision-language model
(``autopilot.core.visual_semantic``). Provider-generated title/tag keyword
overlap is deliberately NOT the semantic decision — it is reduced to a tiny
non-decisive tie-break hint that excludes provider tags entirely.

Candidate scoring combines:
  * visual semantic similarity  (authoritative, hard-gated)  weight 0.45
  * rights / licensing                                       weight 0.25
  * orientation / crop quality                               weight 0.12
  * resolution                                               weight 0.10
  * duplicate penalty                                        weight 0.05
  * keyword hint (title/description only, capped)            weight 0.03

Hard gates:
  * If a ``visual_semantic_score`` is supplied and is below the configured
    minimum, the candidate is REJECTED (score forced toward 0). HARD FAILURE
    is preferable to an unrelated asset.
  * Rejected/Unknown rights cap the score at 0.10.
"""
from __future__ import annotations
import re
from typing import Dict, List, Tuple, Any, Optional

from autopilot.core.config import CONFIG
from autopilot.core.contracts import AssetCandidate

# Weights are normalized at compute time so pre-download ranking (no visual
# score yet) still works, while never letting keywords decide semantics.
_W_VISUAL = 0.45
_W_RIGHTS = 0.25
_W_ASPECT = 0.12
_W_RESOLUTION = 0.10
_W_DEDUP = 0.15
_W_KEYWORD = 0.03

_TAG_STOP = {"the", "a", "an", "of", "and", "for", "in", "on", "at", "to", "is", "are"}


def _keyword_hint(candidate: AssetCandidate, query: str) -> Tuple[float, str]:
    """Tiny, non-decisive hint from TITLE/DESCRIPTION text only.

    Provider-generated TAGS are intentionally excluded so a provider is never
    rewarded merely because its metadata repeats the search query. This can
    never decide selection on its own (capped contribution).
    """
    q = (query or "").lower().strip()
    if not q:
        return 0.5, "no query"
    title = (candidate.title or "").lower()
    desc = (getattr(candidate, "description", "") or "").lower()
    # NOTE: candidate.tags deliberately NOT consulted — provider tags are not
    # semantic evidence.
    text = f"{title} {desc}"
    if not text.strip():
        return 0.5, "no metadata text"
    q_words = [w for w in re.findall(r"\b\w+\b", q) if w not in _TAG_STOP and len(w) > 1]
    if not q_words:
        return 0.5, "query has no substantive words"
    t_tokens = [t for t in re.findall(r"\b\w+\b", text) if len(t) > 1]
    matches = sum(1 for w in q_words if any(w == t or w in t or t in w for t in t_tokens))
    return min(1.0, matches / len(q_words)), f"title/desc overlap {matches}/{len(q_words)}"


def score_candidate(
    candidate: AssetCandidate,
    request_criteria: Optional[Dict[str, Any]] = None,
    target_width: int = 1080,
    target_height: int = 1920,
) -> Tuple[float, Dict[str, float]]:
    """Compute deterministic suitability score for an AssetCandidate.

    ``request_criteria`` may carry:
      query            : scene asset query (for the small keyword hint)
      visual_semantic_score : real CLIP similarity measured on downloaded pixels.
                              When present and below the minimum, the candidate
                              is hard-rejected.
    Returns (total_score in [0,1], breakdown_dict).
    """
    criteria = request_criteria or {}
    breakdown: Dict[str, float] = {}

    # ---- 1. Visual semantic similarity (authoritative) --------------------
    vss = criteria.get("visual_semantic_score")
    vss = float(vss) if vss is not None else None
    min_sim = float(getattr(CONFIG, "visual_semantic_min_similarity", 0.22))
    breakdown["visual_semantic_min_similarity"] = min_sim

    # ---- 2. Rights & Licensing --------------------------------------------
    rights_status = (candidate.license.rights_status or "UNKNOWN").upper()
    rights_score = {
        "VERIFIED": 1.0,
        "PARTIALLY_VERIFIED": 0.65,
        "REJECTED": 0.0,
    }.get(rights_status, 0.0)
    breakdown["rights_score"] = rights_score

    # ---- 3. Aspect Ratio & Orientation ------------------------------------
    w = candidate.dimensions.width or 0
    h = candidate.dimensions.height or 0
    target_ar = target_width / max(1, target_height)  # ~0.5625 for 9:16
    if w > 0 and h > 0:
        actual_ar = w / h
        ar_diff = abs(actual_ar - target_ar)
        if actual_ar < 1.0:
            ar_score = max(0.4, 1.0 - min(1.0, ar_diff * 1.5))
        elif actual_ar == 1.0:
            ar_score = 0.70
        else:
            ar_score = 0.50
    else:
        ar_score = 0.50
    breakdown["aspect_ratio_score"] = round(ar_score, 3)

    # ---- 4. Resolution & Quality ------------------------------------------
    if w > 0 and h > 0:
        width_ratio = min(1.0, w / target_width)
        height_ratio = min(1.0, h / target_height)
        res_score = (width_ratio + height_ratio) / 2.0
        if w >= target_width and h >= target_height:
            res_score = 1.0
        elif min(w, h) < 300:
            res_score = max(0.1, res_score * 0.5)
    else:
        res_score = 0.50
    breakdown["resolution_score"] = round(res_score, 3)

    # ---- 5. Duplicate penalty ---------------------------------------------
    dup_score = 0.0 if candidate.is_duplicate else 1.0
    breakdown["dedup_score"] = dup_score

    # ---- 6. Keyword hint (tiny; tags excluded) -----------------------------
    kw_hint, kw_reason = _keyword_hint(candidate, criteria.get("query") or "")
    breakdown["keyword_hint"] = round(kw_hint, 3)
    # Backwards-compatible alias: callers and QA read "relevance_score".
    breakdown["relevance_score"] = round(kw_hint, 3)
    breakdown["relevance_reason"] = kw_reason

    # ---- HARD GATE: visual semantic similarity -----------------------------
    hard_reject = False
    if vss is not None:
        breakdown["visual_semantic_score"] = round(vss, 4)
        if vss < min_sim:
            hard_reject = True

    # ---- Weighted sum (renormalized by available components) ---------------
    components = [
        ("rights", rights_score, _W_RIGHTS),
        ("aspect", ar_score, _W_ASPECT),
        ("resolution", res_score, _W_RESOLUTION),
        ("dedup", dup_score, _W_DEDUP),
        ("keyword", kw_hint, _W_KEYWORD),
    ]
    if vss is not None:
        # Map cosine into a bounded quality signal for ranking within the gate.
        visual_norm = max(0.0, min(1.0, (vss - min_sim) / max(1e-6, 0.45 - min_sim) + 0.30))
        components.append(("visual", visual_norm, _W_VISUAL))
        breakdown["visual_normalized"] = round(visual_norm, 4)
        # Final semantic relevance level: the measured visual signal when we
        # have it, otherwise the (weak) metadata keyword hint.
        breakdown["semantic_penalty"] = round(visual_norm, 3)
    else:
        breakdown["semantic_penalty"] = round(kw_hint, 3)

    total_weight = sum(c[2] for c in components)
    total_score = sum(score * weight for _, score, weight in components) / max(total_weight, 1e-9)

    # Hard gates cap/kill the score.
    if hard_reject:
        total_score = min(total_score, 0.02)
        breakdown["hard_reject"] = 1.0
        breakdown["reject_reason"] = (
            f"Visual semantic similarity {vss:.3f} < gate {min_sim:.3f}: unrelated asset rejected"
        )
    elif rights_status in ("REJECTED", "UNKNOWN"):
        total_score = min(total_score, 0.10)
        breakdown["rights_capped"] = 1.0

    total_score = round(max(0.0, min(1.0, total_score)), 3)
    return total_score, breakdown


def score_candidates(
    candidates: List[AssetCandidate],
    request_criteria: Optional[Dict[str, Any]] = None,
    target_width: int = 1080,
    target_height: int = 1920,
) -> List[AssetCandidate]:
    """Score all candidates deterministically and sort descending by score.

    Any candidate that fails the visual-semantic hard gate is scored ~0 and
    therefore sorted to the bottom (and rejected upstream before download).
    """
    for c in candidates:
        score, breakdown = score_candidate(
            c,
            request_criteria=request_criteria,
            target_width=target_width,
            target_height=target_height,
        )
        c.score = score
        # Persist breakdown onto the candidate for explainability.
        try:
            c.score_breakdown = breakdown
            if breakdown.get("hard_reject"):
                c.selection_reason = breakdown.get("reject_reason")
        except Exception:
            pass

    # Deterministic sort: descending by score, then candidate_id
    return sorted(candidates, key=lambda x: (x.score, x.candidate_id), reverse=True)
