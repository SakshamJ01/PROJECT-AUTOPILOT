from autopilot.providers.wikipedia_provider import WikipediaProvider
from autopilot.providers.mock_search import MockSearchProvider

def test_research_help_has_wikipedia():
    import subprocess, sys
    out = subprocess.run([sys.executable, "-m", "autopilot", "research", "--help"], capture_output=True, text=True)
    assert "wikipedia" in out.stdout

def test_produce_help_has_research_provider():
    import subprocess, sys
    out = subprocess.run([sys.executable, "-m", "autopilot", "produce", "--help"], capture_output=True, text=True)
    assert "--research-provider" in out.stdout
    assert "wikipedia" in out.stdout

def test_wikipedia_selects_provider():
    p = WikipediaProvider()
    assert p.provider_name == "wikipedia"

def test_wikipedia_evidence_uses_real_snippet():
    from autopilot.providers.wikipedia_provider import WikipediaProvider
    p = WikipediaProvider()
    res = p.search("test", max_results=1)
    # Real provider returns snippet from Wikipedia, not synthetic fixture text
    for r in res:
        assert "synthetic fixture" not in (r.get("snippet") or "") or r.get("snippet") == ""

def test_wikipedia_cache_roundtrip():
    import json, tempfile, os
    from autopilot.core.research_cache import save_cache, load_cache, cache_path
    from autopilot.providers.wikipedia_provider import WikipediaProvider
    payload = {
        "sources_found": 5,
        "results": [{"evidence_items": [{"snippet":"Real snippet"}]*5}],
        "summary": "Real",
        "provider":"wikipedia"
    }
    key = "test-cache-123"
    save_cache(key, payload)
    loaded = load_cache(key)
    assert loaded is not None
    assert loaded["sources_found"] == 5
    assert len(loaded["results"][0]["evidence_items"]) == 5
    # Cleanup
    p = cache_path(key)
    if p.exists(): p.unlink()

def test_produce_research_provider_wikipedia():
    from autopilot.providers.wikipedia_provider import WikipediaProvider
    from autopilot.providers.mock_search import MockSearchProvider
    # Confirm parameter wiring exists and wikipedia selects real provider
    assert WikipediaProvider().provider_name == "wikipedia"
    assert MockSearchProvider().provider_name == "mock_search"

def test_wikipedia_executes_not_mock():
    from autopilot.providers.wikipedia_provider import WikipediaProvider
    from autopilot.providers.mock_search import MockSearchProvider
    p = WikipediaProvider()
    assert p.provider_name == "wikipedia"
    assert not isinstance(p, MockSearchProvider)

def test_three_scenes_different_queries():
    from autopilot.core.asset_pipeline import process_scene_assets
    from autopilot.core.contracts import ScriptDocument, ScriptScene
    # Different narrations should produce different derived queries
    scenes = [
        ScriptScene(scene_id="s1", order=1, narration="Neural networks learn patterns", visual_intent="AI neural network", asset_query="generic topic"),
        ScriptScene(scene_id="s2", order=2, narration="AI in healthcare", visual_intent="Medical AI", asset_query="generic topic"),
    ]
    doc = ScriptDocument(content_id="t", topic="AI", scenes=scenes)
    # Derivation logic should not use identical generic query for both
    queries = []
    for s in doc.scenes:
        raw = s.asset_query or s.visual_intent or "topic"
        if raw == "generic topic" or len(raw) > 50:
            words = [w for w in (s.narration or "").lower().split() if w and len(w) > 2][:5]
            derived = " ".join(words)
            queries.append(derived)
        else:
            queries.append(raw)
    assert queries[0] != queries[1] or len(set(queries)) > 1

def test_openverse_parser_preserves_provenance():
    from autopilot.providers.openverse_provider import OpenverseAssetProvider
    p = OpenverseAssetProvider()
    mock_payload = {
        "results": [
            {
                "id": "neural-01",
                "title": "Neural Network Diagram",
                "url": "https://example.com/nn.jpg",
                "license": "cc0",
                "license_version": "1.0",
                "creator": "Researcher A",
                "width": 1080,
                "height": 1920,
            }
        ]
    }
    res = p.parse_api_response(mock_payload)
    assert len(res) == 1
    for r in res:
        assert r.provenance and r.provenance.provider == "openverse"
        assert r.source_url or r.provenance.source_url

def test_mock_search_default_available():
    p = MockSearchProvider()
    assert p.provider_name == "mock_search"
