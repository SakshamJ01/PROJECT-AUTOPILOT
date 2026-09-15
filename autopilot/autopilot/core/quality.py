"""Script quality gates — Phase 3.
Structured warnings vs blocking failures; configurable thresholds.
No arbitrary universal performance claims.
"""
from __future__ import annotations
import re
from pydantic import BaseModel, Field
from typing import List, Optional, Any


class QualityCheck(BaseModel):
    check_name: str
    status: str  # pass | warning | fail
    message: str
    severity: str  # warning | blocking


class QualityReport(BaseModel):
    script_id: str
    overall: str = "pending"  # pass | fail
    blocking_count: int = 0
    warning_count: int = 0
    checks: List[QualityCheck] = Field(default_factory=list)
    threshold_config: dict = Field(default_factory=dict)
    requested_items: Optional[int] = None
    detected_items: Optional[int] = None
    structure_type: Optional[str] = None
    structure_status: Optional[str] = None


_WORD_TO_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

_LISTICLE_NOUNS = (
    r"(?:facts|tips|ways|reasons|breakthroughs|secrets|steps|things|hacks|myths|"
    r"lessons|rules|habits|examples|discoveries|inventions|features|principles|"
    r"truths|mistakes|insights|takeaways|tricks|points|items|anomalies|questions)"
)

_LISTICLE_MODIFIERS = (
    r"(?:surprising|shocking|incredible|fascinating|unbelievable|hidden|key|major|"
    r"critical|top|best|essential|simple|easy|mind-blowing|powerful|unknown|proven|"
    r"weird|crazy|interesting|strange|crucial|vital|important|must-know)"
)


def detect_listicle_cardinality(topic: str) -> Optional[int]:
    """Detects explicit listicle cardinality (e.g. '3 surprising facts...', 'Top 5 tips...') from topic."""
    if not topic:
        return None
    text = topic.lower().strip()

    # Pattern 1: Number + (optional modifier) + listicle noun
    # e.g. "3 surprising facts", "top 5 reasons", "five secrets"
    num_pattern = (
        r"(?:\b|\A)(?:top\s+|best\s+)?(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:"
        + _LISTICLE_MODIFIERS
        + r"\s+)*"
        + _LISTICLE_NOUNS
        + r"\b"
    )
    m = re.search(num_pattern, text)
    if m:
        val_str = m.group(1).lower()
        if val_str.isdigit():
            val = int(val_str)
        else:
            val = _WORD_TO_NUM.get(val_str, 0)
        if 1 <= val <= 20:
            return val

    # Pattern 2: Starting with digit + noun phrase where digit indicates item count
    # e.g. "3 AI breakthroughs", "5 technologies changing the world"
    m2 = re.match(r"^\s*(\d+)\s+([a-zA-Z]+)", text)
    if m2:
        val = int(m2.group(1))
        if 2 <= val <= 20:
            return val

    return None


def is_pure_cta_scene(scene: Any) -> bool:
    """Check if a scene is purely a call-to-action or outro without substantive fact/content."""
    s_type = getattr(scene, "scene_type", "")
    if s_type in ("cta", "outro"):
        return True
    narration = (getattr(scene, "narration", "") or "").lower().strip()
    if not narration:
        return False

    cta_phrases = [
        "subscribe for more",
        "subscribe to",
        "follow for more",
        "like and subscribe",
        "like and follow",
        "share with a friend",
        "comment below",
        "leave a comment",
        "hit that subscribe",
        "hit the subscribe",
        "stay tuned for",
        "see you in the next",
        "check the link",
        "share your thoughts",
        "share your take",
    ]
    words = [w for w in narration.split() if w]
    if len(words) <= 12 and any(p in narration for p in cta_phrases):
        return True
    return False


def count_substantive_fact_units(scenes: List[Any]) -> int:
    """Counts substantive fact/content scenes, excluding pure CTA/outro scenes."""
    count = 0
    for s in scenes:
        narr = (getattr(s, "narration", "") or "").strip()
        if not narr:
            continue
        if is_pure_cta_scene(s):
            continue
        count += 1
    return count


def evaluate_script(script_doc, topic: Optional[str] = None) -> QualityReport:
    checks = []
    blocking = 0
    warnings = 0

    # Scene count
    if not script_doc.scenes or len(script_doc.scenes) < 1:
        checks.append(QualityCheck(check_name="scene_count", status="fail", message="Script must contain at least one scene", severity="blocking"))
        blocking += 1
    else:
        checks.append(QualityCheck(check_name="scene_count", status="pass", message="At least one scene present", severity="warning"))

    # Hook presence
    if not script_doc.hook or len(script_doc.hook.strip()) == 0:
        checks.append(QualityCheck(check_name="hook_present", status="fail", message="Hook missing or empty", severity="blocking"))
        blocking += 1
    else:
        checks.append(QualityCheck(check_name="hook_present", status="pass", message="Hook present", severity="warning"))

    # Scene IDs unique
    ids = [s.scene_id for s in script_doc.scenes]
    if len(ids) != len(set(ids)):
        checks.append(QualityCheck(check_name="unique_scene_ids", status="fail", message="Duplicate scene IDs", severity="blocking"))
        blocking += 1
    else:
        checks.append(QualityCheck(check_name="unique_scene_ids", status="pass", message="Scene IDs unique", severity="warning"))

    # Narration empty for spoken scenes
    for s in script_doc.scenes:
        if s.scene_type in {"talking_head", "broll", "montage"} and (not s.narration or len(s.narration.strip()) == 0):
            checks.append(QualityCheck(check_name="spoke_narration", status="fail", message=f"Scene {s.scene_id} narration empty for spoken type", severity="blocking"))
            blocking += 1

    # Duration positive
    for s in script_doc.scenes:
        if s.estimated_duration_seconds <= 0:
            checks.append(QualityCheck(check_name="positive_duration", status="fail", message=f"Scene {s.scene_id} duration non-positive", severity="blocking"))
            blocking += 1

    # Order deterministic
    orders = [s.order for s in script_doc.scenes]
    if sorted(orders) != orders:
        checks.append(QualityCheck(check_name="deterministic_order", status="fail", message="Scene order not ascending", severity="blocking"))
        blocking += 1
    else:
        checks.append(QualityCheck(check_name="deterministic_order", status="pass", message="Order ascending", severity="warning"))

    # CTA present (optional warning if missing but not blocking by default)
    if not script_doc.cta or len(script_doc.cta.strip()) == 0:
        checks.append(QualityCheck(check_name="cta_present", status="warning", message="CTA missing (optional)", severity="warning"))
        warnings += 1
    else:
        checks.append(QualityCheck(check_name="cta_present", status="pass", message="CTA present", severity="warning"))

    # Listicle Structure & Cardinality Intelligence Check
    topic_str = topic or getattr(script_doc, "topic", "")
    requested_items = detect_listicle_cardinality(topic_str)
    structure_type = "listicle" if requested_items is not None else "general"
    detected_items = count_substantive_fact_units(script_doc.scenes) if script_doc.scenes else 0

    if requested_items is not None:
        if detected_items < requested_items:
            checks.append(QualityCheck(
                check_name="list_structure",
                status="fail",
                message=f"List structure defect: expected at least {requested_items} distinct fact/content scenes for topic '{topic_str}', but detected only {detected_items}.",
                severity="blocking",
            ))
            blocking += 1
        else:
            checks.append(QualityCheck(
                check_name="list_structure",
                status="pass",
                message=f"List structure valid: {detected_items} substantive scenes present for {requested_items}-item request.",
                severity="warning",
            ))
    else:
        checks.append(QualityCheck(
            check_name="list_structure",
            status="pass",
            message="General content structure (non-listicle topic).",
            severity="warning",
        ))

    structure_status = "valid" if (requested_items is None or detected_items >= requested_items) else "defect"
    overall = "fail" if blocking > 0 else ("pass" if warnings == 0 else "pass_with_warnings")

    return QualityReport(
        script_id=script_doc.content_id,
        overall=overall,
        blocking_count=blocking,
        warning_count=warnings,
        checks=checks,
        threshold_config={
            "block_on_empty_scene": True,
            "block_on_missing_hook": True,
            "block_on_list_structure": True,
        },
        requested_items=requested_items,
        detected_items=detected_items,
        structure_type=structure_type,
        structure_status=structure_status,
    )
