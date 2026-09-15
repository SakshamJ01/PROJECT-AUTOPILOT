"""Focused unit tests for asset-query generation, visual fallback hierarchy, and semantic selection."""
import pytest
from autopilot.core.contracts import ScriptDocument, ScriptScene, AssetCandidate, AssetLicense, AssetDimensions
from autopilot.core.asset_pipeline import derive_visual_subject_query, derive_deterministic_fallback_query, process_scene_assets
from autopilot.core.asset_scoring import score_candidate, score_candidates
from autopilot.core.rights_gate import evaluate_rights_gate
from autopilot.providers.asset_contracts import AssetProvider


def test_scene_specific_visual_query_generation():
    # Scene with narration and distinct visual intent
    scene1 = ScriptScene(
        scene_id="s1",
        order=1,
        narration="Artificial intelligence was first introduced at the Dartmouth conference in 1956.",
        visual_intent="Historical Dartmouth college campus vintage photograph",
        asset_query="Dartmouth college historical campus 1956",
        estimated_duration_seconds=6.0,
    )
    topic = "3 Surprising Facts About Artificial Intelligence"

    q1 = derive_visual_subject_query(scene1, topic)
    assert q1 == "Dartmouth college historical campus 1956"
    assert q1 != topic

    # Scene where asset_query was duplicated as generic topic
    scene2 = ScriptScene(
        scene_id="s2",
        order=2,
        narration="Modern machine learning systems learn patterns directly from massive datasets.",
        visual_intent="Data network neural network server room",
        asset_query=topic,  # duplicated
        estimated_duration_seconds=6.0,
    )
    q2 = derive_visual_subject_query(scene2, topic)
    assert q2 != topic
    assert "data" in q2 or "network" in q2 or "machine" in q2


def test_narration_first_words_not_used_as_fallback():
    scene = ScriptScene(
        scene_id="s1",
        order=1,
        narration="was first introduced by visionary researchers long ago",
        visual_intent="Ancient research laboratory with books",
        asset_query="",
        estimated_duration_seconds=6.0,
    )
    topic = "Artificial Intelligence History"

    fallback_q = derive_deterministic_fallback_query(scene, topic)
    assert "was first introduced" not in fallback_q
    assert fallback_q != "was first introduced"
    assert "ancient" in fallback_q.lower() or "research" in fallback_q.lower() or "artificial" in fallback_q.lower()


def test_semantically_irrelevant_candidates_deprioritized():
    query = "Dartmouth college campus"
    
    relevant_cand = AssetCandidate(
        candidate_id="cand-relevant",
        title="Dartmouth College Historic Campus Buildings",
        tags=["dartmouth", "college", "campus"],
        dimensions=AssetDimensions(width=1080, height=1920),
        license=AssetLicense(rights_status="VERIFIED", commercial_use=True, derivative_use=True),
    )

    irrelevant_cand = AssetCandidate(
        candidate_id="cand-irrelevant",
        title="Narrow street in Valletta Malta Europe",
        tags=["malta", "street", "valletta", "europe"],
        dimensions=AssetDimensions(width=1080, height=1920),
        license=AssetLicense(rights_status="VERIFIED", commercial_use=True, derivative_use=True),
    )

    scored = score_candidates([irrelevant_cand, relevant_cand], request_criteria={"query": query})
    assert scored[0].candidate_id == "cand-relevant"
    assert scored[1].score < scored[0].score
    # Verify semantic penalty applied to irrelevant candidate
    s_irrelevant, bd_irrelevant = score_candidate(irrelevant_cand, request_criteria={"query": query})
    assert bd_irrelevant["semantic_penalty"] < 0.5


def test_rights_gating_remains_intact():
    query = "machine learning"

    rejected_rights_cand = AssetCandidate(
        candidate_id="cand-nc",
        title="Machine Learning Server Room Data Center",
        tags=["machine", "learning"],
        dimensions=AssetDimensions(width=1080, height=1920),
        license=AssetLicense(license_name="CC BY-NC", rights_status="REJECTED", commercial_use=False),
    )

    gate_res = evaluate_rights_gate(rejected_rights_cand.license)
    assert gate_res.allowed is False
    score, _ = score_candidate(rejected_rights_cand, request_criteria={"query": query})
    assert score <= 0.10


class MockZeroResultProvider(AssetProvider):
    provider_name = "mock_zero"
    def search(self, criteria, max_results=10):
        if criteria.get("query") == "obscure non-existent concept":
            return []
        # Return fallback candidate
        return [
            AssetCandidate(
                candidate_id="cand-fallback",
                title="Generic Renewable Solar Panels Energy",
                tags=["solar", "panels", "energy"],
                dimensions=AssetDimensions(width=1080, height=1920),
                license=AssetLicense(rights_status="VERIFIED", commercial_use=True),
            )
        ]
    def download(self, candidate, dest_path):
        return dest_path
    def normalize(self, source_path, dest_path, asset_type, target_width, target_height):
        return dest_path


def test_arbitrary_topic_support(tmp_path):
    topic = "The Future of Solar Energy Technology"
    scene = ScriptScene(
        scene_id="s1",
        order=1,
        narration="Solar power efficiency has increased dramatically over recent years.",
        visual_intent="Solar panel array in desert sunlight",
        asset_query="solar panel array desert",
        estimated_duration_seconds=5.0,
    )

    q = derive_visual_subject_query(scene, topic)
    assert "solar" in q.lower()
    fallback_q = derive_deterministic_fallback_query(scene, topic)
    assert "was first introduced" not in fallback_q
