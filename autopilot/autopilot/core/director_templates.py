"""Content-Type Specific Director Templates — Phase 2.

Implements the canonical director narrative templates:
  1. SCIENCE_EXPLAINER: HOOK -> PARADOX -> MECHANISM -> EVIDENCE -> IMPLICATION -> PAYOFF
  2. HISTORY_STORY: HOOK -> SETTING -> CATALYST -> CRISIS -> RESOLUTION -> LESSON
  3. LISTICLE: HOOK -> FACT_1 -> FACT_2 -> FACT_3 -> FACT_4 -> FACT_5 -> FINAL_PAYOFF
  4. MYTH_BUST: HOOK -> COMMON_BELIEF -> WHY_ITS_WRONG -> REAL_FACT -> PROOF -> PAYOFF

Rules:
  - CTA is strictly optional and omitted if it weakens the final narrative payoff.
"""
from __future__ import annotations

from enum import Enum
from typing import List, Dict, Any, Optional, Union
from pydantic import BaseModel, Field


class ContentType(str, Enum):
    SCIENCE_EXPLAINER = "SCIENCE_EXPLAINER"
    HISTORY_STORY = "HISTORY_STORY"
    LISTICLE = "LISTICLE"
    MYTH_BUST = "MYTH_BUST"


class NarrativeBeat(BaseModel):
    beat_id: str
    beat_name: str
    role_type: str
    target_duration_ratio: float = Field(..., ge=0.0, le=1.0)
    prompt_guidance: str
    shot_spec_hint: str
    is_optional: bool = False


class DirectorTemplate(BaseModel):
    content_type: ContentType
    template_name: str
    description: str
    beats: List[NarrativeBeat] = Field(..., min_length=1)
    allow_cta: bool = False  # CTA is optional and omitted by default to avoid weakening payoff

    def get_beat(self, beat_id: str) -> Optional[NarrativeBeat]:
        for b in self.beats:
            if b.beat_id == beat_id:
                return b
        return None

    def get_beat_sequence(self) -> List[str]:
        return [b.beat_name for b in self.beats]


TEMPLATES: Dict[ContentType, DirectorTemplate] = {
    ContentType.SCIENCE_EXPLAINER: DirectorTemplate(
        content_type=ContentType.SCIENCE_EXPLAINER,
        template_name="Science Explainer Engine",
        description="HOOK -> PARADOX -> MECHANISM -> EVIDENCE -> IMPLICATION -> PAYOFF",
        beats=[
            NarrativeBeat(
                beat_id="beat_1",
                beat_name="HOOK",
                role_type="HOOK",
                target_duration_ratio=0.15,
                prompt_guidance="Present an astonishing scientific observation or question that grabs immediate attention.",
                shot_spec_hint="fast dramatic macro zoom or aerial reveal",
            ),
            NarrativeBeat(
                beat_id="beat_2",
                beat_name="PARADOX",
                role_type="PARADOX",
                target_duration_ratio=0.15,
                prompt_guidance="Highlight why this observation seems impossible under conventional understanding.",
                shot_spec_hint="high contrast schematic or slow motion phenomenon",
            ),
            NarrativeBeat(
                beat_id="beat_3",
                beat_name="MECHANISM",
                role_type="MECHANISM",
                target_duration_ratio=0.25,
                prompt_guidance="Explain the underlying physical or biological mechanism in clear visual steps.",
                shot_spec_hint="dynamic 3D visual explanation or step-by-step schematic",
            ),
            NarrativeBeat(
                beat_id="beat_4",
                beat_name="EVIDENCE",
                role_type="EVIDENCE",
                target_duration_ratio=0.20,
                prompt_guidance="Cite concrete experiment, observation data, or telescope/satellite discovery.",
                shot_spec_hint="photorealistic lab or observatory discovery footage",
            ),
            NarrativeBeat(
                beat_id="beat_5",
                beat_name="IMPLICATION",
                role_type="IMPLICATION",
                target_duration_ratio=0.15,
                prompt_guidance="Explain what this discovery unlocks for technology, the cosmos, or human understanding.",
                shot_spec_hint="sweeping wide angle cinematic future reveal",
            ),
            NarrativeBeat(
                beat_id="beat_6",
                beat_name="PAYOFF",
                role_type="PAYOFF",
                target_duration_ratio=0.10,
                prompt_guidance="Deliver the final memorable insight that closes the loop opened by the hook.",
                shot_spec_hint="epic slow cinematic hold with subtle motion",
            ),
        ],
        allow_cta=False,
    ),

    ContentType.HISTORY_STORY: DirectorTemplate(
        content_type=ContentType.HISTORY_STORY,
        template_name="History Story Engine",
        description="HOOK -> SETTING -> CATALYST -> CRISIS -> RESOLUTION -> LESSON",
        beats=[
            NarrativeBeat(
                beat_id="beat_1",
                beat_name="HOOK",
                role_type="HOOK",
                target_duration_ratio=0.15,
                prompt_guidance="Open with a shocking historical turning point or pivotal decision.",
                shot_spec_hint="dramatic atmospheric historical artifact or map zoom",
            ),
            NarrativeBeat(
                beat_id="beat_2",
                beat_name="SETTING",
                role_type="SETTING",
                target_duration_ratio=0.15,
                prompt_guidance="Establish the era, location, and the stakes facing the figures involved.",
                shot_spec_hint="sweeping historical landscape or architectural pan",
            ),
            NarrativeBeat(
                beat_id="beat_3",
                beat_name="CATALYST",
                role_type="CATALYST",
                target_duration_ratio=0.20,
                prompt_guidance="Describe the spark or decision that triggered an unavoidable sequence of events.",
                shot_spec_hint="moving push in on historical document or catalyst event",
            ),
            NarrativeBeat(
                beat_id="beat_4",
                beat_name="CRISIS",
                role_type="CRISIS",
                target_duration_ratio=0.25,
                prompt_guidance="Detail the climax of the conflict, breakthrough, or catastrophe.",
                shot_spec_hint="fast-paced montage of intense historical depictions",
            ),
            NarrativeBeat(
                beat_id="beat_5",
                beat_name="RESOLUTION",
                role_type="RESOLUTION",
                target_duration_ratio=0.15,
                prompt_guidance="Reveal how the crisis concluded and what immediate change it created.",
                shot_spec_hint="calm aftermath visual with deliberate pan",
            ),
            NarrativeBeat(
                beat_id="beat_6",
                beat_name="LESSON",
                role_type="LESSON",
                target_duration_ratio=0.10,
                prompt_guidance="Articulate the enduring historical principle or modern consequence.",
                shot_spec_hint="modern day contrast or enduring monument shot",
            ),
        ],
        allow_cta=False,
    ),

    ContentType.LISTICLE: DirectorTemplate(
        content_type=ContentType.LISTICLE,
        template_name="Listicle Engine",
        description="HOOK -> FACT_1 -> FACT_2 -> FACT_3 -> FACT_4 -> FACT_5 -> FINAL_PAYOFF",
        beats=[
            NarrativeBeat(
                beat_id="beat_hook",
                beat_name="HOOK",
                role_type="HOOK",
                target_duration_ratio=0.10,
                prompt_guidance="High-energy hook introducing the curated countdown or list.",
                shot_spec_hint="high energy montage reveal with bold typography",
            ),
            NarrativeBeat(
                beat_id="beat_fact_1",
                beat_name="FACT_1",
                role_type="FACT_1",
                target_duration_ratio=0.15,
                prompt_guidance="First compelling verified fact.",
                shot_spec_hint="close up detailed B-roll matching fact 1",
            ),
            NarrativeBeat(
                beat_id="beat_fact_2",
                beat_name="FACT_2",
                role_type="FACT_2",
                target_duration_ratio=0.15,
                prompt_guidance="Second escalating fact with unexpected angle.",
                shot_spec_hint="dynamic panning shot matching fact 2",
            ),
            NarrativeBeat(
                beat_id="beat_fact_3",
                beat_name="FACT_3",
                role_type="FACT_3",
                target_duration_ratio=0.15,
                prompt_guidance="Third fact revealing deeper complexity.",
                shot_spec_hint="aerial drone shot matching fact 3",
            ),
            NarrativeBeat(
                beat_id="beat_fact_4",
                beat_name="FACT_4",
                role_type="FACT_4",
                target_duration_ratio=0.15,
                prompt_guidance="Fourth mind-bending fact.",
                shot_spec_hint="macro or high speed footage matching fact 4",
            ),
            NarrativeBeat(
                beat_id="beat_fact_5",
                beat_name="FACT_5",
                role_type="FACT_5",
                target_duration_ratio=0.15,
                prompt_guidance="Fifth supreme fact holding highest interest.",
                shot_spec_hint="jaw dropping drone or cinema shot matching fact 5",
            ),
            NarrativeBeat(
                beat_id="beat_final_payoff",
                beat_name="FINAL_PAYOFF",
                role_type="FINAL_PAYOFF",
                target_duration_ratio=0.15,
                prompt_guidance="Grand takeaway tying the list together.",
                shot_spec_hint="cinematic hold matching grand conclusion",
            ),
        ],
        allow_cta=False,
    ),

    ContentType.MYTH_BUST: DirectorTemplate(
        content_type=ContentType.MYTH_BUST,
        template_name="Myth Bust Engine",
        description="HOOK -> COMMON_BELIEF -> WHY_ITS_WRONG -> REAL_FACT -> PROOF -> PAYOFF",
        beats=[
            NarrativeBeat(
                beat_id="beat_1",
                beat_name="HOOK",
                role_type="HOOK",
                target_duration_ratio=0.15,
                prompt_guidance="State a viral widespread belief and challenge its truth immediately.",
                shot_spec_hint="intriguing visual framing of the popular misconception",
            ),
            NarrativeBeat(
                beat_id="beat_2",
                beat_name="COMMON_BELIEF",
                role_type="COMMON_BELIEF",
                target_duration_ratio=0.15,
                prompt_guidance="Explain why millions of people believe this myth to be true.",
                shot_spec_hint="cultural or mainstream depiction of the myth",
            ),
            NarrativeBeat(
                beat_id="beat_3",
                beat_name="WHY_ITS_WRONG",
                role_type="WHY_ITS_WRONG",
                target_duration_ratio=0.25,
                prompt_guidance="Break down the precise scientific/historical flaw in the myth.",
                shot_spec_hint="visual 'myth busted' transition or contrast demonstration",
            ),
            NarrativeBeat(
                beat_id="beat_4",
                beat_name="REAL_FACT",
                role_type="REAL_FACT",
                target_duration_ratio=0.20,
                prompt_guidance="Present the actual, even more fascinating reality.",
                shot_spec_hint="clear high-definition footage of the real phenomenon",
            ),
            NarrativeBeat(
                beat_id="beat_5",
                beat_name="PROOF",
                role_type="PROOF",
                target_duration_ratio=0.15,
                prompt_guidance="Show the direct experimental or archival proof.",
                shot_spec_hint="irrefutable visual proof or data visualization",
            ),
            NarrativeBeat(
                beat_id="beat_6",
                beat_name="PAYOFF",
                role_type="PAYOFF",
                target_duration_ratio=0.10,
                prompt_guidance="Conclude with why the real truth is more impressive than the myth.",
                shot_spec_hint="satisfying final visual resolution hold",
            ),
        ],
        allow_cta=False,
    ),
}


def get_director_template(content_type: Union[str, ContentType]) -> DirectorTemplate:
    """Retrieve canonical Director template for the specified content type."""
    key = content_type if isinstance(content_type, ContentType) else ContentType(str(content_type).upper())
    return TEMPLATES[key]
