"""Listicle Structure Validation (P0).

Prevents the failure mode where an N-item listicle request collapses into a
single repeated item (e.g. "5 animals that outlived dinosaurs" becoming one
parrot story repeated five times).

Validation operates on ACTUAL script content — it extracts each scene's
declared item identity (from on_screen_text / asset_query / visual_intent /
narration) and proves the items are substantively distinct. It never relies
solely on the number of scenes.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, Field

from autopilot.core.contracts import ScriptDocument, ScriptScene

# Words that carry no item identity — stripped before comparing items.
_GENERIC_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "to", "of", "and", "for",
    "in", "that", "with", "on", "at", "by", "from", "or", "as", "this", "it",
    "surprising", "amazing", "incredible", "facts", "fact", "about", "top",
    "best", "things", "ways", "reasons", "you", "your", "can", "could",
    "would", "should", "most", "more", "very", "really", "actually", "just",
    "like", "also", "than", "then", "there", "here", "when", "where", "what",
    "how", "why", "which", "who", "all", "any", "each", "some", "such",
    "not", "no", "but", "if", "so", "be", "been", "being", "have", "has",
    "had", "do", "does", "did", "will", "may", "might", "must", "one", "two",
    "three", "four", "five", "six", "seven", "eight", "nine", "ten",
}


def _content_tokens(text: str) -> Set[str]:
    """Substantive lowercased tokens (length >= 4, non-generic)."""
    raw = {w.lower() for w in re.findall(r"\b\w+\b", text or "")}
    return {w for w in raw if len(w) >= 4 and w not in _GENERIC_WORDS}


def extract_scene_item_label(scene: ScriptScene) -> str:
    """Best-effort identity label for a listicle item scene."""
    for field in (scene.on_screen_text, scene.asset_query, scene.visual_intent):
        if field and field.strip():
            cleaned = re.sub(r"^(fact|item|number|#)\s*#?\d*[^\w]*", "", field.strip(), flags=re.I)
            cleaned = cleaned.strip()
            if cleaned:
                return cleaned
    return (scene.narration or "").strip()[:120]


class ListicleItem(BaseModel):
    scene_id: str
    order: int
    label: str
    tokens: Set[str] = Field(default_factory=set)


class ListicleValidationResult(BaseModel):
    is_listicle: bool
    requested_item_count: Optional[int] = None
    detected_item_count: int = 0
    items: List[ListicleItem] = Field(default_factory=list)
    duplicate_groups: List[List[str]] = Field(default_factory=list)
    collapsed_into_single_item: bool = False
    passed: bool = True
    reason: str = ""


def _detect_listicle_count(topic: str) -> Optional[int]:
    """Extract N from topics like '5 Surprising Animals That ...'."""
    m = re.search(r"\b(\d{1,2})\b", topic or "")
    if not m:
        return None
    n = int(m.group(1))
    if 2 <= n <= 25:
        return n
    return None


def validate_listicle_structure(
    script: ScriptDocument,
    requested_count: Optional[int] = None,
) -> ListicleValidationResult:
    """Validate that an N-item listicle has N substantively distinct items.

    Returns a result whose `passed` is False when items collapse into near
    duplicates (the parrot-story failure mode).
    """
    topic = script.topic or ""
    n = requested_count if requested_count is not None else _detect_listicle_count(topic)
    is_listicle = n is not None

    # Extract item identity per scene.
    items: List[ListicleItem] = []
    for scene in script.scenes:
        label = extract_scene_item_label(scene)
        # Identity tokens combine the label and the narration substance.
        tokens = _content_tokens(label) | _content_tokens(scene.narration or "")
        items.append(
            ListicleItem(
                scene_id=scene.scene_id,
                order=scene.order,
                label=label,
                tokens=tokens,
            )
        )

    if not is_listicle or not items:
        return ListicleValidationResult(
            is_listicle=is_listicle,
            detected_item_count=len(items),
            items=items,
            passed=True,
            reason="Not an N-item listicle; structure check not applicable.",
        )

    # Group items that are near-duplicates: identity overlap above threshold.
    OVERLAP_THRESHOLD = 0.72
    duplicate_groups: List[List[str]] = []
    assigned: Set[str] = set()
    for i, a in enumerate(items):
        if a.scene_id in assigned or not a.tokens:
            continue
        group = [a.scene_id]
        assigned.add(a.scene_id)
        for b in items[i + 1:]:
            if b.scene_id in assigned or not b.tokens:
                continue
            inter = len(a.tokens & b.tokens)
            union = len(a.tokens | b.tokens)
            jaccard = inter / union if union else 0.0
            containment = inter / min(len(a.tokens), len(b.tokens)) if min(len(a.tokens), len(b.tokens)) else 0.0
            if max(jaccard, containment) >= OVERLAP_THRESHOLD:
                group.append(b.scene_id)
                assigned.add(b.scene_id)
        if len(group) > 1:
            duplicate_groups.append(group)

    distinct_items = len(items) - sum(len(g) - 1 for g in duplicate_groups)
    collapsed = distinct_items <= 1 and len(items) > 1

    # Required: at least min(n, len(scenes)) distinct items; here we require
    # the number of distinct items to be >= n when n items were requested.
    passed = True
    reasons: List[str] = []
    if duplicate_groups:
        passed = False
        reasons.append(
            f"Duplicate-item scenes detected: {duplicate_groups} — items must be substantively distinct."
        )
    if collapsed:
        passed = False
        reasons.append(
            f"Requested {n} items but the script collapsed into a single repeated item."
        )
    if distinct_items < n:
        passed = False
        reasons.append(
            f"Requested {n} distinct items but only {distinct_items} substantively distinct item(s) found."
        )

    return ListicleValidationResult(
        is_listicle=True,
        requested_item_count=n,
        detected_item_count=len(items),
        items=items,
        duplicate_groups=duplicate_groups,
        collapsed_into_single_item=collapsed,
        passed=passed,
        reason=" ".join(reasons) if reasons else f"All {n} listicle items are substantively distinct.",
    )


__all__ = [
    "ListicleValidationResult",
    "ListicleItem",
    "validate_listicle_structure",
    "extract_scene_item_label",
]
