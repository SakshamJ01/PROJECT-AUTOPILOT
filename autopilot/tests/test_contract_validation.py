"""Contract validation tests — Phase 1 / M1."""
from __future__ import annotations
import pytest
from pathlib import Path

from autopilot.core.contracts import (
    ScriptDocument, ScriptScene, ContentPackage, ContentItem,
    ResearchSource, VoiceSegment, AssetRequest, PublicationMetadata,
    ProvenanceRecord, package_to_json, package_from_json,
    generate_json_schema, CONTRACT_SCHEMA_VERSION,
)
from autopilot.core.duration import estimate_duration, estimate_scene_durations, DEFAULT_WPM
from autopilot.providers.mock_script import MockScriptProvider


class TestScriptDocumentValidation:
    def test_valid_script_document(self):
        s = ScriptDocument(
            content_id="test-01",
            topic="test",
            scenes=[
                ScriptScene(scene_id="s1", order=1, narration="Hello.", visual_intent="test", estimated_duration_seconds=3.0),
            ],
        )
        assert s.scenes[0].scene_id == "s1"
        assert s.content_id == "test-01"

    def test_invalid_no_scenes(self):
        with pytest.raises(Exception):
            ScriptDocument(content_id="bad", topic="bad", scenes=[])

    def test_duplicate_scene_ids(self):
        with pytest.raises(Exception):
            ScriptDocument(
                content_id="bad", topic="bad",
                scenes=[
                    ScriptScene(scene_id="dup", order=1, narration="A"),
                    ScriptScene(scene_id="dup", order=2, narration="B"),
                ],
            )

    def test_narration_empty_for_spoken(self):
        with pytest.raises(Exception):
            ScriptDocument(
                content_id="bad", topic="bad",
                scenes=[
                    ScriptScene(scene_id="s1", order=1, narration="", scene_type="talking_head", visual_intent="x"),
                ],
            )

    def test_narration_ok_for_text_scene(self):
        s = ScriptDocument(
            content_id="txt", topic="txt",
            scenes=[ScriptScene(scene_id="s1", order=1, narration="", scene_type="text", visual_intent="x")],
        )
        assert s is not None

    def test_duration_positive(self):
        with pytest.raises(Exception):
            ScriptScene(scene_id="s", order=1, narration="x", estimated_duration_seconds=0)

    def test_scene_order_deterministic(self):
        with pytest.raises(Exception):
            ScriptDocument(
                content_id="bad", topic="bad",
                scenes=[
                    ScriptScene(scene_id="s2", order=2, narration="B"),
                    ScriptScene(scene_id="s1", order=1, narration="A", visual_intent="v"),
                ],
            )

    def test_total_duration_positive(self):
        s = ScriptDocument(content_id="t", topic="t", scenes=[ScriptScene(scene_id="s1", order=1, narration="x", visual_intent="v", estimated_duration_seconds=5.0)], total_estimated_duration=10.0)
        assert s.total_estimated_duration == 10.0


class TestDurationUtility:
    def test_estimate_duration_default(self):
        text = "This is a simple sentence with ten words"
        d = estimate_duration(text, wpm=150)
        assert d > 0
        assert isinstance(d, float)

    def test_estimate_duration_empty(self):
        assert estimate_duration("", wpm=150) == 0.0

    def test_estimate_duration_configurable(self):
        text = "One two three four"
        d_fast = estimate_duration(text, wpm=300)
        d_slow = estimate_duration(text, wpm=60)
        assert d_fast < d_slow

    def test_estimate_scene_durations(self):
        scenes = [ScriptScene(scene_id="s1", order=1, narration="Hello world", visual_intent="v", estimated_duration_seconds=2.0)]
        total = estimate_scene_durations(scenes, wpm=150)
        assert total > 0


class TestSerialization:
    def test_round_trip_json(self, tmp_path):
        original = ScriptDocument(
            content_id="rt-01",
            topic="roundtrip",
            scenes=[ScriptScene(scene_id="s1", order=1, narration="Hello", visual_intent="test")],
        )
        path = tmp_path / "rt.json"
        path.write_text(original.model_dump_json(indent=2), encoding="utf-8")
        loaded = ScriptDocument.model_validate_json(path.read_text(encoding="utf-8"))
        assert loaded.content_id == original.content_id
        assert loaded.scenes[0].scene_id == "s1"

    def test_package_round_trip(self, tmp_path):
        pkg = ContentPackage(
            content_item=ContentItem(content_id="pkg-01", topic="t"),
            script=ScriptDocument(content_id="pkg-01", topic="t", scenes=[ScriptScene(scene_id="s1", order=1, narration="N", visual_intent="v")]),
        )
        text = pkg.model_dump_json()
        loaded = ContentPackage.model_validate_json(text)
        assert loaded.content_item.topic == "t"

    def test_malformed_json_rejected(self):
        with pytest.raises(Exception):
            ScriptDocument.model_validate_json('{"bad": true}')


class TestProviderMock:
    def test_mock_provider_health(self):
        p = MockScriptProvider()
        h = p.health_check()
        assert h.healthy is True
        assert h.provider_name == "mock_script"

    def test_mock_provider_output(self):
        p = MockScriptProvider()
        script = p.generate_script("automation", content_id="m-01")
        assert script.content_id == "m-01"
        assert len(script.scenes) >= 2
        assert script.scenes[0].scene_id == "scene-01"

    def test_mock_output_stable_for_same_topic(self):
        p = MockScriptProvider()
        s1 = p.generate_script("same")
        s2 = p.generate_script("same")
        assert s1.scenes[0].narration == s2.scenes[0].narration


class TestPublicationMetadata:
    def test_valid_privacy(self):
        pm = PublicationMetadata(title="Test", privacy_status="draft")
        assert pm.privacy_status == "draft"

    def test_invalid_privacy(self):
        with pytest.raises(Exception):
            PublicationMetadata(title="Test", privacy_status="badvalue")

    def test_empty_platforms_rejected(self):
        with pytest.raises(Exception):
            PublicationMetadata(title="Test", target_platforms=[])


class TestContentPackage:
    def test_package_validation(self):
        pkg = ContentPackage(
            content_item=ContentItem(content_id="c-01", topic="t"),
            script=ScriptDocument(content_id="c-01", topic="t", scenes=[ScriptScene(scene_id="s1", order=1, narration="hello", visual_intent="x")]),
        )
        assert pkg.script.content_id == "c-01"

    def test_artifact_creation_for_package(self, tmp_path):
        pkg = ContentPackage(
            content_item=ContentItem(content_id="art-01", topic="t"),
            script=ScriptDocument(content_id="art-01", topic="t", scenes=[ScriptScene(scene_id="s1", order=1, narration="hello", visual_intent="x")]),
        )
        path = tmp_path / "pkg.json"
        from autopilot.core.contracts import package_to_file, package_from_file
        package_to_file(pkg, path)
        assert path.exists()
        loaded = package_from_file(path)
        assert loaded.content_item.content_id == "art-01"


class TestSchemaVisibility:
    def test_schema_file_exists(self):
        schema_path = Path("schemas/content-contract-v1.json")
        assert schema_path.exists(), "Schema file must exist for machine-readable contract"
        text = schema_path.read_text(encoding="utf-8")
        assert "ScriptDocument" in text or "ContentPackage" in text


class TestProvenance:
    def test_provenance_record(self):
        p = ProvenanceRecord(provider="local", deterministic_idempotency_key="k-001")
        assert p.provider == "local"
