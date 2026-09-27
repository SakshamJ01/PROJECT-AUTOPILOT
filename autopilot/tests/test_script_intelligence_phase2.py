"""Comprehensive Tests for Phase 2 — Script Intelligence, Claim Verification & Topic Difficulty.

Tests:
  1. Claim verification schema & statuses (VERIFIED, UNVERIFIED, CONFLICTING, REJECTED)
  2. Multi-source evidence contradiction detection
  3. Claim-strength calibration (ESTABLISHED_FACT, OBSERVED_DATA, THEORETICAL_PROPOSAL, POPULAR_MYTH)
  4. Qualification requirement & phrase generation
  5. Topic difficulty classifier (EASY, MODERATE, HARD, RARE)
  6. Content visualability scoring (HIGH_VISUALABILITY, CONCEPTUAL_MODERATE, ABSTRACT_LOW)
  7. Director Templates for all 4 content types (SCIENCE_EXPLAINER, HISTORY_STORY, LISTICLE, MYTH_BUST)
  8. Optional CTA behavior (omitted by default)
  9. Search query vs. Shot spec separation
  10. Cloud provider registry & cost telemetry
"""
import pytest

from autopilot.core.claim_verification import (
    ClaimVerifier,
    ClaimVerificationStatus,
    ClaimStrength,
    FactualClaim,
)
from autopilot.core.topic_intelligence import (
    TopicIntelligenceEngine,
    TopicDifficulty,
    ContentVisualabilityClassification,
    TopicDifficultyAssessment,
    ContentVisualabilityScore,
)
from autopilot.core.director_templates import (
    ContentType,
    DirectorTemplate,
    get_director_template,
)
from autopilot.core.cloud_provider_registry import (
    ScriptProviderModel,
    ScriptGenerationContract,
    ScriptIntelligenceEngine,
    SceneVisualSpec,
    ProviderCostRates,
)


# ---------------------------------------------------------------------------
# 1. Factual Claim Verification & Contradiction Tests
# ---------------------------------------------------------------------------

def test_claim_verification_verified_evidence():
    """Verify claim receives VERIFIED status when supported by research evidence."""
    verifier = ClaimVerifier()
    sources = [
        {
            "source_id": "wiki-Chenab_Bridge",
            "title": "Chenab Bridge",
            "content": "The Chenab Bridge is a steel and concrete arch bridge located in Jammu and Kashmir standing 359 meters above the river bed.",
        }
    ]
    claim = verifier.verify_claim(
        claim_text="The Chenab Bridge stands 359 meters above the river bed.",
        sources=sources,
    )
    assert claim.verification == ClaimVerificationStatus.VERIFIED
    assert claim.confidence >= 0.70
    assert claim.claim_strength == ClaimStrength.ESTABLISHED_FACT
    assert claim.qualification_required is False


def test_claim_verification_unverified():
    """Verify claim without source evidence is flagged as UNVERIFIED with qualification requirement."""
    verifier = ClaimVerifier()
    claim = verifier.verify_claim(
        claim_text="Aliens built the pyramids using anti-gravity lasers in 2500 BC.",
        sources=[],
    )
    assert claim.verification == ClaimVerificationStatus.UNVERIFIED
    assert claim.confidence <= 0.20
    assert claim.qualification_required is True
    assert claim.qualification_phrase is not None


def test_claim_verification_detects_conflicts():
    """Verify claim is flagged as CONFLICTING when multiple sources report contradictory data."""
    verifier = ClaimVerifier()
    sources = [
        {
            "source_id": "source_A",
            "content": "The ancient structure measured 350 meters in height during the excavation.",
        },
        {
            "source_id": "source_B",
            "content": "Official geological surveys recorded the ancient structure at 280 meters.",
        },
    ]
    claim = verifier.verify_claim(
        claim_text="The ancient structure stood 350 meters tall.",
        sources=sources,
    )
    assert claim.verification == ClaimVerificationStatus.CONFLICTING
    assert claim.qualification_required is True
    assert len(claim.conflicts) > 0


def test_claim_strength_calibration():
    """Verify claim strength distinguishes theoretical proposals, myths, observed data, and established facts."""
    verifier = ClaimVerifier()
    # Theoretical proposal
    c1 = verifier.verify_claim("Physicists hypothesize that wormholes might connect distant galaxies.", sources=[])
    assert c1.claim_strength == ClaimStrength.THEORETICAL_PROPOSAL
    assert c1.qualification_required is True

    # Popular myth
    c2 = verifier.verify_claim("It is a common myth that bats are completely blind.", sources=[])
    assert c2.claim_strength == ClaimStrength.POPULAR_MYTH
    assert c2.qualification_required is True

    # Observed data
    c3 = verifier.verify_claim("Satellite sensors recorded 45mm of rainfall across the basin.", sources=[])
    assert c3.claim_strength == ClaimStrength.OBSERVED_DATA


# ---------------------------------------------------------------------------
# 2. Topic Difficulty & Content Visualability Tests
# ---------------------------------------------------------------------------

def test_topic_difficulty_assessment():
    """Verify topic difficulty correctly classifies easy vs hard/rare subjects."""
    engine = TopicIntelligenceEngine()

    # Easy topic: abundant sources + high physical visual availability
    sources = [{"id": f"src_{i}", "text": "info"} for i in range(5)]
    easy_res = engine.assess_difficulty(topic="Volcano eruption in Hawaii", research_sources=sources)
    assert easy_res.difficulty in (TopicDifficulty.EASY, TopicDifficulty.MODERATE)
    assert easy_res.source_density_score >= 0.70

    # Rare/Hard topic: abstract math without sources
    hard_res = engine.assess_difficulty(topic="Algebraic topology homotopy axioms", research_sources=[])
    assert hard_res.difficulty in (TopicDifficulty.HARD, TopicDifficulty.RARE)
    assert hard_res.technical_complexity_score >= 0.50


def test_content_visualability_scoring():
    """Verify content visualability separates depictable physical subjects from abstract formulations."""
    engine = TopicIntelligenceEngine()

    # High visualability: concrete physical objects (bridge, rocket, space)
    high_vis = engine.score_visualability(topic="Deep space rocket launch to Mars moon")
    assert high_vis.classification == ContentVisualabilityClassification.HIGH_VISUALABILITY
    assert high_vis.visualability_score >= 0.65
    assert high_vis.b_roll_density_potential >= 0.70

    # Abstract low visualability: pure metaphysics/epistemology
    low_vis = engine.score_visualability(topic="Epistemology and ontology metaphysics syntax")
    assert low_vis.classification == ContentVisualabilityClassification.ABSTRACT_LOW
    assert low_vis.visualability_score < 0.45


# ---------------------------------------------------------------------------
# 3. Director Templates Tests
# ---------------------------------------------------------------------------

def test_science_explainer_template_structure():
    """Verify Science Explainer template follows HOOK -> PARADOX -> MECHANISM -> EVIDENCE -> IMPLICATION -> PAYOFF."""
    tpl = get_director_template(ContentType.SCIENCE_EXPLAINER)
    assert tpl.get_beat_sequence() == ["HOOK", "PARADOX", "MECHANISM", "EVIDENCE", "IMPLICATION", "PAYOFF"]
    assert tpl.allow_cta is False  # CTA omitted by default


def test_history_story_template_structure():
    """Verify History Story template follows HOOK -> SETTING -> CATALYST -> CRISIS -> RESOLUTION -> LESSON."""
    tpl = get_director_template(ContentType.HISTORY_STORY)
    assert tpl.get_beat_sequence() == ["HOOK", "SETTING", "CATALYST", "CRISIS", "RESOLUTION", "LESSON"]


def test_listicle_template_structure():
    """Verify Listicle template follows HOOK -> FACT_1 -> FACT_2 -> FACT_3 -> FACT_4 -> FACT_5 -> FINAL_PAYOFF."""
    tpl = get_director_template(ContentType.LISTICLE)
    assert tpl.get_beat_sequence() == ["HOOK", "FACT_1", "FACT_2", "FACT_3", "FACT_4", "FACT_5", "FINAL_PAYOFF"]


def test_myth_bust_template_structure():
    """Verify Myth Bust template follows HOOK -> COMMON_BELIEF -> WHY_ITS_WRONG -> REAL_FACT -> PROOF -> PAYOFF."""
    tpl = get_director_template(ContentType.MYTH_BUST)
    assert tpl.get_beat_sequence() == ["HOOK", "COMMON_BELIEF", "WHY_ITS_WRONG", "REAL_FACT", "PROOF", "PAYOFF"]


# ---------------------------------------------------------------------------
# 4. Search Query vs. Shot Spec & Cloud Provider Registry Tests
# ---------------------------------------------------------------------------

def test_script_intelligence_engine_generation_and_separation():
    """Verify script generation cleanly separates b_roll_search_query from shot_spec and verifies claims."""
    engine = ScriptIntelligenceEngine()
    req = ScriptGenerationContract(
        topic="Chenab Bridge Engineering",
        content_type=ContentType.SCIENCE_EXPLAINER,
        target_duration_sec=35.0,
        research_sources=[
            {
                "source_id": "wiki-Chenab",
                "title": "Chenab Bridge",
                "content": "Chenab Bridge is the highest rail arch bridge in the world.",
            }
        ],
        preferred_model=ScriptProviderModel.GEMINI_2_5_FLASH,
        allow_cta=False,
    )

    res = engine.generate_and_verify_script(req)

    assert len(res.script.scenes) == 6
    assert res.script.cta is None  # Optional CTA omitted
    assert len(res.claims) == 6
    assert len(res.visual_specs) == 6

    # Verify search query vs shot spec distinction
    spec_01 = res.visual_specs["scene_01"]
    assert isinstance(spec_01, SceneVisualSpec)
    assert "footage" in spec_01.b_roll_search_query
    assert "macro" in spec_01.shot_spec or "reveal" in spec_01.shot_spec or "cinematic" in spec_01.shot_spec

    # Verify telemetry tracking
    assert res.telemetry.model == "gemini-2.5-flash"
    assert res.telemetry.estimated_cost_usd >= 0.0
    assert res.telemetry.latency_ms >= 0.0
