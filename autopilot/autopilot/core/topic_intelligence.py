"""Topic Intelligence, Difficulty Estimation & Content Visualability — Phase 2.

Implements:
  1. Topic Difficulty Classifier (EASY, MODERATE, HARD, RARE)
     - Evaluates Source Density, Visual Availability, Technical Complexity
  2. Content Visualability Scoring
     - Evaluates concept visual depictability vs abstract formulations
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class TopicDifficulty(str, Enum):
    EASY = "EASY"
    MODERATE = "MODERATE"
    HARD = "HARD"
    RARE = "RARE"


class ContentVisualabilityClassification(str, Enum):
    HIGH_VISUALABILITY = "HIGH_VISUALABILITY"
    CONCEPTUAL_MODERATE = "CONCEPTUAL_MODERATE"
    ABSTRACT_LOW = "ABSTRACT_LOW"


class TopicDifficultyAssessment(BaseModel):
    topic: str
    difficulty: TopicDifficulty
    source_density_score: float = Field(..., ge=0.0, le=1.0)
    visual_availability_score: float = Field(..., ge=0.0, le=1.0)
    technical_complexity_score: float = Field(..., ge=0.0, le=1.0)
    composite_difficulty_score: float = Field(..., ge=0.0, le=1.0)
    explanation: str


class ContentVisualabilityScore(BaseModel):
    topic: str
    visualability_score: float = Field(..., ge=0.0, le=1.0)
    classification: ContentVisualabilityClassification
    b_roll_density_potential: float = Field(..., ge=0.0, le=1.0)
    rationale: str


class TopicIntelligenceEngine:
    """Production topic intelligence evaluator for difficulty and visualability."""

    # Keywords indicating high visual depictability (physical objects, actions, geography, space)
    VISUAL_KEYWORDS = {
        "bridge", "rocket", "ocean", "volcano", "planet", "telescope", "car", "train", "building",
        "city", "mountain", "animal", "space", "star", "laser", "explosion", "forest", "desert",
        "sun", "moon", "drone", "construction", "airplane", "ship", "river", "glacier", "island"
    }

    # Keywords indicating abstract / low visualability (pure math, metaphysics, abstract syntax)
    ABSTRACT_KEYWORDS = {
        "axiom", "theorem", "topology", "metaphysics", "epistemology", "calculus", "isomorphism",
        "algebraic", "syllogism", "ontology", "nominalism", "tautology", "recursion", "syntax"
    }

    # Technical complexity keywords
    COMPLEXITY_KEYWORDS = {
        "quantum", "relativity", "spectroscopy", "thermodynamics", "neuroscience", "genome",
        "particle", "fusion", "encryption", "algorithm", "superconductor", "electromagnetism"
    }

    def assess_difficulty(self, topic: str, research_sources: Optional[List[Any]] = None) -> TopicDifficultyAssessment:
        """Assess topic difficulty considering source density, visual availability, and technical complexity."""
        sources = research_sources or []
        num_sources = len(sources)

        # 1. Source Density Score (higher = more sources = easier research)
        if num_sources >= 5:
            source_density = 0.9
        elif num_sources >= 3:
            source_density = 0.7
        elif num_sources >= 1:
            source_density = 0.45
        else:
            source_density = 0.15

        # 2. Technical Complexity (higher = more complex)
        words = set(re.findall(r"\w+", topic.lower()))
        complexity_matches = len(words.intersection(self.COMPLEXITY_KEYWORDS))
        abstract_matches = len(words.intersection(self.ABSTRACT_KEYWORDS))
        tech_complexity = min(1.0, 0.2 + (complexity_matches * 0.3) + (abstract_matches * 0.4))

        # 3. Visual Availability (higher = easier to find footage)
        visual_matches = len(words.intersection(self.VISUAL_KEYWORDS))
        visual_avail = min(1.0, 0.4 + (visual_matches * 0.3) - (abstract_matches * 0.3))
        visual_avail = max(0.1, visual_avail)

        # 4. Composite Difficulty Score (0.0 easy -> 1.0 rare/hard)
        # Low source density + high complexity + low visual availability = harder topic
        diff_score = (
            (1.0 - source_density) * 0.40
            + tech_complexity * 0.40
            + (1.0 - visual_avail) * 0.20
        )
        diff_score = min(1.0, max(0.0, round(diff_score, 2)))

        if diff_score < 0.35:
            difficulty = TopicDifficulty.EASY
            expl = "Abundant research sources with high visual availability and low technical complexity."
        elif diff_score < 0.60:
            difficulty = TopicDifficulty.MODERATE
            expl = "Moderate research density with accessible visual anchors."
        elif diff_score < 0.80:
            difficulty = TopicDifficulty.HARD
            expl = "High technical complexity or specialized visual depiction requirements."
        else:
            difficulty = TopicDifficulty.RARE
            expl = "Scarce research sources with abstract concepts and low visual availability."

        return TopicDifficultyAssessment(
            topic=topic,
            difficulty=difficulty,
            source_density_score=source_density,
            visual_availability_score=visual_avail,
            technical_complexity_score=tech_complexity,
            composite_difficulty_score=diff_score,
            explanation=expl,
        )

    def score_visualability(self, topic: str, concepts: Optional[List[str]] = None) -> ContentVisualabilityScore:
        """Evaluate content visualability (conceptually strong + visually depictable vs abstract)."""
        all_text = f"{topic} {' '.join(concepts or [])}".lower()
        words = set(re.findall(r"\w+", all_text))

        visual_hits = len(words.intersection(self.VISUAL_KEYWORDS))
        abstract_hits = len(words.intersection(self.ABSTRACT_KEYWORDS))

        raw_score = 0.5 + (visual_hits * 0.15) - (abstract_hits * 0.25)
        score = min(1.0, max(0.1, round(raw_score, 2)))

        if score >= 0.65:
            classification = ContentVisualabilityClassification.HIGH_VISUALABILITY
            b_roll = min(1.0, score + 0.1)
            rationale = "Concepts feature concrete physical nouns with abundant B-roll and drone footage potential."
        elif score >= 0.40:
            classification = ContentVisualabilityClassification.CONCEPTUAL_MODERATE
            b_roll = score
            rationale = "Concepts require contextual/illustrative B-roll or animated diagrams to depict clearly."
        else:
            classification = ContentVisualabilityClassification.ABSTRACT_LOW
            b_roll = max(0.2, score)
            rationale = "Abstract mathematical or philosophical subject; requires schematic diagrams or kinetic typography."

        return ContentVisualabilityScore(
            topic=topic,
            visualability_score=score,
            classification=classification,
            b_roll_density_potential=b_roll,
            rationale=rationale,
        )
