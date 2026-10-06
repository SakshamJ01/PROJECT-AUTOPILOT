"""Second-chance query rewrite tests (quality plan 1.3).

A scene whose downloaded asset clears the hard CLIP floor (0.21) but scores
below the strong threshold (0.28) must trigger ONE retry of the scene search
with a broader synonym query derived from visual_intent -- and the weak asset
must still be accepted if the broader search finds nothing better.
"""
import shutil
from pathlib import Path

from PIL import Image

from autopilot.core.config import Config
from autopilot.core.contracts import (
    AssetCandidate,
    AssetDimensions,
    AssetLicense,
    AssetProvenance,
    ScriptDocument,
    ScriptScene,
)
from autopilot.core.asset_pipeline import derive_broader_query, derive_visual_subject_query
from autopilot.db.manager import DBManager
from autopilot.providers.asset_contracts import AssetProvider


# ------------------------------------------------------------------
# Query-derivation unit tests
# ------------------------------------------------------------------
def _scene() -> ScriptScene:
    return ScriptScene(
        scene_id="scene-01",
        order=1,
        narration="The Battle of Gettysburg turned on July 3, 1863.",
        visual_intent="Vintage battlefield cannons photograph",
        asset_query="gettysburg battlefield",
        estimated_duration_seconds=5.0,
    )


def test_broader_query_applies_visual_synonyms():
    q = derive_broader_query(_scene(), "5 Facts About the Battle of Gettysburg")
    assert "war" in q.split(), f"battlefield must broaden to war: {q!r}"
    assert "battlefield" not in q.split() or "war" in q.split()


def test_broader_query_merges_topic_core():
    q = derive_broader_query(_scene(), "5 Facts About the Battle of Gettysburg")
    assert "gettysburg" in q.lower(), q
    assert q != derive_visual_subject_query(_scene(), "5 Facts About the Battle of Gettysburg")


def test_broader_query_never_uses_narration_words():
    scene = _scene()
    q = derive_broader_query(scene, "Titanic Facts")
    assert "july" not in q.lower()
    assert "1863" not in q.lower()


def test_broader_query_deterministic_and_falls_back_to_topic():
    scene = ScriptScene(
        scene_id="scene-02",
        order=2,
        narration="Some narration here.",
        visual_intent=" ",
        asset_query="",
        estimated_duration_seconds=5.0,
    )
    topic = "The Moon Landing"
    q1 = derive_broader_query(scene, topic)
    q2 = derive_broader_query(scene, topic)
    assert q1 == q2
    assert "moon" in q1.lower()


# ------------------------------------------------------------------
# Integration: process_scene_assets second chance
# ------------------------------------------------------------------
def _make_candidate(cand_id: str, source_id: str) -> AssetCandidate:
    return AssetCandidate(
        candidate_id=cand_id,
        title=f"Candidate {cand_id}",
        asset_type="image",
        source_url=f"https://example.com/{cand_id}.png",
        source_id=source_id,
        tags=[],
        dimensions=AssetDimensions(width=1080, height=1920),
        license=AssetLicense(
            license_name="Pexels License",
            rights_status="VERIFIED",
            commercial_use=True,
            derivative_use=True,
        ),
        provenance=AssetProvenance(
            provider="pexels",
            source_id=source_id,
            provider_asset_ref=f"pexels:{source_id}",
        ),
    )


class _SecondChanceProvider(AssetProvider):
    """First search returns the weak candidate; later searches are scripted."""

    provider_name = "pexels"

    def __init__(self, retry_results):
        self.queries = []
        self._retry_results = retry_results

    def search(self, criteria, max_results=10):
        self.queries.append(criteria.get("query", ""))
        if len(self.queries) == 1:
            return [_make_candidate("cand-weak", "weak-src")]
        return list(self._retry_results)

    def download(self, candidate, dest_path):
        img = Image.new("RGB", (64, 64), color=(200, 120, 40))
        img.save(dest_path, format="PNG")
        return dest_path

    def normalize(self, source_path, dest_path, **kwargs):
        shutil.copyfile(source_path, dest_path)
        return dest_path


def _script() -> ScriptDocument:
    return ScriptDocument(
        content_id="second-chance-test",
        topic="5 Facts About the Battle of Gettysburg",
        title="Second Chance",
        scenes=[_scene()],
    )


def _setup(tmp_path, monkeypatch, retry_results, clip_score_by_id):
    import autopilot.core.asset_pipeline as ap

    provider = _SecondChanceProvider(retry_results)
    monkeypatch.setattr(ap, "get_asset_provider", lambda name: provider)
    monkeypatch.setattr(ap, "build_asset_cascade", lambda name, allow: [])
    monkeypatch.setattr(ap, "visual_semantic_enabled", lambda: True)

    def fake_verify(path, **kwargs):
        p = str(path)
        score = 0.30
        for cand_id, sc in clip_score_by_id.items():
            if cand_id in p:
                score = sc
        return {
            "passed_gate": True,
            "visual_semantic_score": score,
            "reason": "synthetic verify",
            "model": "test-model",
            "per_variant": {},
            "frame_count": 1,
        }

    monkeypatch.setattr(ap, "visual_semantic_verify", fake_verify)

    cfg = Config(artifacts_dir=tmp_path, db_path=tmp_path / "sc.db")
    db = DBManager(cfg.db_path)
    db.init_schema()
    return ap, provider, cfg, db


def test_score_below_strong_threshold_triggers_broader_research(tmp_path, monkeypatch):
    """A scene scoring 0.25 must re-search once with the broader query."""
    ap, provider, cfg, db = _setup(
        tmp_path,
        monkeypatch,
        retry_results=[_make_candidate("cand-strong", "strong-src")],
        clip_score_by_id={"cand-weak": 0.25, "cand-strong": 0.42},
    )

    artifacts, report = ap.process_scene_assets(
        _script(),
        job_id="job-sc-1",
        provider_name="pexels",
        db=db,
        config=cfg,
    )

    scene = _scene()
    expected_broader = derive_broader_query(scene, "5 Facts About the Battle of Gettysburg")
    assert len(provider.queries) == 2, f"expected original + broader search, got {provider.queries}"
    assert provider.queries[1] == expected_broader

    assert len(artifacts) == 1, report
    assert artifacts[0].provenance.source_id == "strong-src"
    assert artifacts[0].semantic_score == 0.42
    assert any("second-chance" in w for w in report.get("warnings", [])), report.get("warnings")


def test_score_at_or_above_strong_threshold_skips_research(tmp_path, monkeypatch):
    """A 0.30 pass clears the strong threshold: no second search."""
    ap, provider, cfg, db = _setup(
        tmp_path,
        monkeypatch,
        retry_results=[],
        clip_score_by_id={"cand-weak": 0.30},
    )

    artifacts, report = ap.process_scene_assets(
        _script(),
        job_id="job-sc-2",
        provider_name="pexels",
        db=db,
        config=cfg,
    )

    assert len(provider.queries) == 1, provider.queries
    assert len(artifacts) == 1
    assert artifacts[0].provenance.source_id == "weak-src"
    assert artifacts[0].semantic_score == 0.30


def test_weak_asset_still_accepted_when_broader_search_finds_nothing(tmp_path, monkeypatch):
    """Second chance must never cost the scene its floor-passing asset."""
    ap, provider, cfg, db = _setup(
        tmp_path,
        monkeypatch,
        retry_results=[],
        clip_score_by_id={"cand-weak": 0.25},
    )

    artifacts, report = ap.process_scene_assets(
        _script(),
        job_id="job-sc-3",
        provider_name="pexels",
        db=db,
        config=cfg,
    )

    assert len(provider.queries) == 2, provider.queries
    assert len(artifacts) == 1, report
    assert artifacts[0].provenance.source_id == "weak-src"
    assert artifacts[0].semantic_score == 0.25
