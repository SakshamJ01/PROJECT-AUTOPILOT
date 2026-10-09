"""Tests for the Round-2 upgrade modules (all offline / hermetic)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from autopilot.core.contracts import ScriptDocument, ScriptScene


def _make_script_document(topic: str = "Solid State Batteries") -> ScriptDocument:
    scene = ScriptScene(
        scene_id="s1",
        order=1,
        narration="Solid state batteries just changed everything.",
        visual_intent="close-up of a battery cell",
        estimated_duration_seconds=3.0,
    )
    return ScriptDocument(
        content_id="item-test",
        topic=topic,
        working_title=topic,
        hook="This changes everything",
        scenes=[scene],
        cta="Follow for more!",
    )


# ---------------------------------------------------------------------------
# Item 1 — BGM generator
# ---------------------------------------------------------------------------
class TestBGMGenerator:
    def test_procedural_backend_always_available(self):
        from autopilot.core.bgm_generator import (
            BGM_BACKEND_PROCEDURAL,
            available_bgm_backends,
        )

        assert BGM_BACKEND_PROCEDURAL in available_bgm_backends()

    def test_generate_bgm_bed_procedural_fallback(self, tmp_path: Path):
        from autopilot.core.bgm_generator import generate_bgm_bed

        out = tmp_path / "bed.wav"
        path, backend = generate_bgm_bed(
            out, duration_sec=5.0, mood="dramatic", backend="procedural"
        )
        assert backend == "procedural"
        assert path.exists()
        assert path.stat().st_size > 1000

    def test_fill_library_creates_mood_tagged_files(self, tmp_path: Path):
        from autopilot.core.bgm_generator import fill_bgm_library
        from autopilot.core.audio_scene_graph import resolve_bgm_source

        written = fill_bgm_library(
            tmp_path,
            moods=["dramatic", "contemplative"],
            per_mood=1,
            duration_sec=5.0,
            backend="procedural",
        )
        assert len(written) == 2
        names = {p.name for p in written}
        assert "bgm_dramatic_00.wav" in names
        assert "bgm_contemplative_00.wav" in names

        # The deterministic mood matcher should now pick a library track.
        track, source = resolve_bgm_source("war battle disaster", library_dir=tmp_path)
        assert source == "library"
        assert track is not None

    def test_fill_library_is_idempotent(self, tmp_path: Path):
        from autopilot.core.bgm_generator import fill_bgm_library

        first = fill_bgm_library(
            tmp_path, moods=["mysterious"], per_mood=1, duration_sec=5.0, backend="procedural"
        )
        second = fill_bgm_library(
            tmp_path, moods=["mysterious"], per_mood=1, duration_sec=5.0, backend="procedural"
        )
        assert len(first) == 1
        assert len(second) == 0  # already present, skipped


# ---------------------------------------------------------------------------
# Item 2 — Demucs vocal clarity (fail-soft)
# ---------------------------------------------------------------------------
class TestAudioIntelligence:
    def test_missing_media_reports_unavailable(self, tmp_path: Path):
        from autopilot.core.audio_intelligence import analyze_vocal_clarity

        report = analyze_vocal_clarity(tmp_path / "nope.mp4")
        assert report.available is False
        assert "not found" in report.notes

    def test_demucs_availability_is_boolean(self):
        from autopilot.core.audio_intelligence import demucs_available

        assert isinstance(demucs_available(), bool)


# ---------------------------------------------------------------------------
# Item 3 — Structured LLM output
# ---------------------------------------------------------------------------
class TestStructuredLLM:
    def test_validate_against_schema_success(self):
        from autopilot.providers.structured_llm import validate_against_schema

        doc = _make_script_document()
        instance, errors = validate_against_schema(doc, ScriptDocument)
        assert instance is not None
        assert errors == []

    def test_validate_against_schema_failure(self):
        from autopilot.providers.structured_llm import validate_against_schema

        instance, errors = validate_against_schema({}, ScriptDocument)
        assert instance is None
        assert errors

    def test_generate_script_structured_valid(self):
        from autopilot.providers.structured_llm import generate_script_structured

        class Provider:
            def generate_script(self, **kwargs):
                return _make_script_document()

        doc = generate_script_structured(Provider(), topic="Solid State Batteries")
        assert isinstance(doc, ScriptDocument)

    def test_generate_script_structured_retry_then_success(self):
        from autopilot.providers.structured_llm import generate_script_structured

        class Provider:
            def __init__(self):
                self.calls = 0
                self.corrective_seen = []

            def generate_script(self, **kwargs):
                self.calls += 1
                if kwargs.get("corrective_instructions"):
                    self.corrective_seen.append(kwargs["corrective_instructions"])
                if self.calls == 1:
                    return {}  # invalid on first attempt
                return _make_script_document()

        provider = Provider()
        doc = generate_script_structured(provider, topic="Topic")
        assert isinstance(doc, ScriptDocument)
        assert provider.calls == 2
        assert provider.corrective_seen  # corrective instructions were fed back

    def test_generate_script_structured_raises_when_always_invalid(self):
        from autopilot.providers.structured_llm import generate_script_structured

        class Provider:
            def generate_script(self, **kwargs):
                return {}

        with pytest.raises(ValidationError):
            generate_script_structured(Provider(), topic="Topic", max_attempts=2)

    def test_complete_structured_success_and_retry(self):
        from autopilot.providers.structured_llm import complete_structured

        responses = [
            "not json at all",
            json.dumps({"title": "T", "scenes": [{"narration": "a", "visual_intent": "b"}]}),
        ]

        def chat_fn(**kwargs):
            return responses.pop(0)

        result = complete_structured(
            chat_fn, schema=__import__("autopilot.providers.structured_llm", fromlist=["ScriptPayloadSchema"]).ScriptPayloadSchema, max_attempts=2
        )
        assert result.title == "T"


# ---------------------------------------------------------------------------
# Item 4 — Reddit trend provider + factory
# ---------------------------------------------------------------------------
class TestRedditTrendProvider:
    def _reddit_payload(self, now_ts: float):
        return {
            "data": {
                "children": [
                    {
                        "data": {
                            "title": "Breakthrough in solid state batteries",
                            "created_utc": now_ts,
                            "ups": 2500,
                            "permalink": "/r/technology/abc",
                            "selftext": "A major automaker announced a new cell.",
                        }
                    }
                ]
            }
        }

    def test_discover_trends_maps_posts(self):
        from autopilot.providers.reddit_trend import RedditTrendProvider

        now_ts = datetime.now(timezone.utc).timestamp()
        payload = self._reddit_payload(now_ts)

        def fetch(url, timeout):
            # Only the target subreddit returns a post; others are empty.
            return payload if "r/technology" in url else {"data": {"children": []}}

        provider = RedditTrendProvider(fetch_json=fetch)

        signals = provider.discover_trends(category="technology", limit=5)
        assert len(signals) == 1
        sig = signals[0]
        assert sig.topic == "Breakthrough in solid state batteries"
        assert sig.source == "reddit_trend"
        assert sig.freshness_score > 0.5
        assert 0.5 < sig.relevance_score <= 1.0
        assert sig.provenance is not None

    def test_discover_trends_fails_soft_on_error(self):
        from autopilot.providers.reddit_trend import RedditTrendProvider

        def boom(url, timeout):
            raise RuntimeError("network down")

        provider = RedditTrendProvider(fetch_json=boom)
        assert provider.discover_trends(category="science", limit=3) == []

    def test_trend_factory_resolves_providers(self):
        from autopilot.providers.trend_factory import get_trend_provider
        from autopilot.providers.mock_trend import MockTrendProvider
        from autopilot.providers.reddit_trend import RedditTrendProvider

        assert isinstance(get_trend_provider("mock"), MockTrendProvider)
        assert isinstance(get_trend_provider(None), MockTrendProvider)
        assert isinstance(get_trend_provider("reddit"), RedditTrendProvider)
        assert isinstance(get_trend_provider("reddit_trend"), RedditTrendProvider)


# ---------------------------------------------------------------------------
# Item 5 — Keyword / SEO scorer
# ---------------------------------------------------------------------------
class TestKeywordScorer:
    def test_score_keyword_computes_kgr_and_opportunity(self):
        from autopilot.core.keyword_scorer import score_keyword

        result = score_keyword(
            "how to invest",
            fetch_volume=lambda k: 50.0,
            fetch_competition=lambda k: 0.3,
        )
        assert result.keyword == "how to invest"
        assert result.search_volume == 50.0
        assert result.competition == 0.3
        # kgR = (0.3*10000)/50 = 60
        assert result.kgR == pytest.approx(60.0)
        assert result.opportunity_score == pytest.approx(58.0, abs=0.1)

    def test_score_keyword_handles_missing_signals(self):
        from autopilot.core.keyword_scorer import score_keyword

        result = score_keyword(
            "mystery topic",
            fetch_volume=lambda k: None,
            fetch_competition=lambda k: None,
        )
        assert result.search_volume is None
        assert result.competition is None
        assert result.kgR is None
        assert result.opportunity_score is None

    def test_derive_keywords_and_score_topic(self):
        from autopilot.core.keyword_scorer import derive_keywords_from_topic, score_topic

        phrases = derive_keywords_from_topic("Solid State Battery Breakthrough")
        assert phrases[0] == "solid state battery breakthrough"
        assert "solid state battery" in phrases

        best = score_topic(
            "Solid State Battery Breakthrough",
            fetch_volume=lambda k: 40.0 if "solid state" in k else 10.0,
            fetch_competition=lambda k: 0.2,
        )
        assert best.opportunity_score is not None

    def test_empty_keyword(self):
        from autopilot.core.keyword_scorer import score_keyword

        assert score_keyword("").keyword == ""


# ---------------------------------------------------------------------------
# Item 6 — Multimodal (Gemini) QA
# ---------------------------------------------------------------------------
class TestMultimodalQA:
    def test_analyze_video_with_injected_fn(self, tmp_path: Path):
        from autopilot.core.multimodal_qa import analyze_video

        video = tmp_path / "v.mp4"
        video.write_bytes(b"\x00")

        result = analyze_video(
            video,
            analyze_fn=lambda p, k, m: {"relevance": 80, "dynamism": 65, "hook": 90},
        )
        assert result.available is True
        assert result.relevance_score == 80
        assert result.dynamism_score == 65
        assert result.hook_effectiveness == 90

    def test_analyze_video_missing_file(self, tmp_path: Path):
        from autopilot.core.multimodal_qa import analyze_video

        result = analyze_video(tmp_path / "missing.mp4")
        assert result.available is False

    def test_analyze_video_without_key_or_sdk(self, tmp_path: Path):
        from autopilot.core import multimodal_qa

        video = tmp_path / "v.mp4"
        video.write_bytes(b"\x00")
        # No injected fn; if key/sdks absent it should report unavailable.
        result = multimodal_qa.analyze_video(video)
        # Either unavailable (no key/sdk) or, if a key happens to exist, it will
        # attempt and fail-soft to unavailable on error. Never raises.
        assert isinstance(result.available, bool)

    def test_gemini_available_is_boolean(self):
        from autopilot.core.multimodal_qa import gemini_available

        assert isinstance(gemini_available(), bool)


# ---------------------------------------------------------------------------
# Item 7 — Generative thumbnail backend (falls back to PIL)
# ---------------------------------------------------------------------------
class TestGenerativeThumbnail:
    def test_backend_disabled_by_default(self):
        from autopilot.core.thumbnail import _generative_thumbnail_backend_available

        # thumbnail_generative defaults to False -> backend not used.
        assert _generative_thumbnail_backend_available() is False

    def test_falls_back_to_pil_composite(self, tmp_path: Path):
        from PIL import Image

        from autopilot.core.thumbnail import generate_thumbnail

        asset = tmp_path / "scene.jpg"
        Image.new("RGB", (200, 300), color=(20, 80, 200)).save(asset)

        scenes = [
            {
                "scene_id": "s1",
                "asset_path": str(asset),
                "semantic_score": 0.3,
                "asset_type": "image",
            }
        ]
        out = generate_thumbnail(scenes, tmp_path, hook="Wow", channel_name="Chan")
        assert out is not None
        assert out.exists()
        assert out.name == "thumb.jpg"

    def test_generative_backend_used_when_available(self, tmp_path: Path, monkeypatch):
        from autopilot.core import thumbnail

        monkeypatch.setattr(
            thumbnail, "_generative_thumbnail_backend_available", lambda: True
        )
        expected = tmp_path / "thumbnails" / "thumb.jpg"

        def fake_gen(hook, channel_name, out_path, model=None):
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"jpegdata")
            return out_path

        monkeypatch.setattr(thumbnail, "_generate_generative_thumbnail", fake_gen)

        out = thumbnail.generate_thumbnail([], tmp_path, hook="Hi", channel_name="C")
        assert out == expected
        assert out.read_bytes() == b"jpegdata"
