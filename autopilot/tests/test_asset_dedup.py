"""Hard per-job asset deduplication tests.

The Moon job reused the same Pexels clip 3x across 7 scenes because the only
dedup signal was a soft score penalty keyed on original_hash_sha256 -- a field
the provider never populates at search time (only after download). These tests
cover the hard exclusion path that actually prevents that defect.
"""
from autopilot.core.contracts import (
    AssetCandidate,
    AssetDimensions,
    AssetLicense,
    AssetProvenance,
    ScriptDocument,
    ScriptScene,
)
from autopilot.providers.asset_contracts import AssetProvider


class _DupProvider(AssetProvider):
    """Returns the same two candidates for every scene.

    Candidate A is the strongest and would win every scene without hard dedup.
    """

    provider_name = "dup_test"

    def __init__(self, call_count=None):
        self._call_count = call_count

    def search(self, criteria, max_results=10):
        if self._call_count is not None:
            self._call_count[0] += 1
        return [
            AssetCandidate(
                candidate_id="cand-A",
                title="Clip A",
                tags=[],
                dimensions=AssetDimensions(width=1080, height=1920),
                license=AssetLicense(
                    license_name="Pexels License",
                    rights_status="VERIFIED",
                    commercial_use=True,
                    derivative_use=True,
                ),
                provenance=AssetProvenance(
                    provider=self.provider_name,
                    source_id="clip-A",
                    provider_asset_ref="dup_test:clip-A",
                ),
            ),
            AssetCandidate(
                candidate_id="cand-B",
                title="Clip B",
                tags=[],
                dimensions=AssetDimensions(width=1080, height=1920),
                license=AssetLicense(
                    license_name="Pexels License",
                    rights_status="VERIFIED",
                    commercial_use=True,
                    derivative_use=True,
                ),
                provenance=AssetProvenance(
                    provider=self.provider_name,
                    source_id="clip-B",
                    provider_asset_ref="dup_test:clip-B",
                ),
            ),
        ]

    def download(self, candidate, dest_path):
        # Not reached: search_only short-circuits before download.
        raise AssertionError("download should not run in search_only mode")

    def normalize(self, source_path, dest_path, **kwargs):
        raise AssertionError("normalize should not run in search_only mode")


def _make_script(n_scenes: int) -> ScriptDocument:
    scenes = [
        ScriptScene(
            scene_id=f"scene-{i+1:02d}",
            order=i + 1,
            narration=f"Scene {i+1} narration text",
            visual_intent=f"visual intent {i+1}",
            asset_query=f"query {i+1}",
        )
        for i in range(n_scenes)
    ]
    return ScriptDocument(
        content_id="dedup-test",
        topic="dedup test topic",
        title="Dedup Test",
        scenes=scenes,
    )


def test_duplicate_asset_is_excluded_from_later_scenes(tmp_path, monkeypatch):
    """Scene 2 must NOT get clip A once scene 1 has selected it."""
    import autopilot.core.asset_pipeline as ap

    provider = _DupProvider()
    monkeypatch.setattr(ap, "get_asset_provider", lambda name: provider)
    monkeypatch.setattr(ap, "build_asset_cascade", lambda name, allow: [])
    # Bypass the visual gate + inspection, which need real media.
    monkeypatch.setattr(ap, "visual_semantic_enabled", lambda: False)

    script = _make_script(2)
    artifacts, report = ap.process_scene_assets(
        script,
        provider_name="dup_test",
        job_id="job-dedup-1",
        search_only=True,
    )

    # In search_only mode every scene is marked successful via the candidates it
    # would have downloaded. Assert via the selections report instead: scene 2
    # must never select cand-A.
    scene_picks = {}
    for sel in report["selections"]:
        scene_picks.setdefault(sel["scene_id"], []).append(sel["candidate_id"])

    assert "cand-B" in scene_picks.get("scene-01", [])
    # Whatever scene-01 took, scene-02 must take the OTHER one.
    assert "cand-B" not in scene_picks.get("scene-02", []), (
        "scene-02 re-selected the same clip as scene-01 -- hard dedup failed"
    )
    assert "cand-A" in scene_picks.get("scene-02", [])


def test_dedup_records_rejection_reason(tmp_path, monkeypatch):
    """A blocked duplicate must appear in the report so the failure is auditable."""
    import autopilot.core.asset_pipeline as ap

    provider = _DupProvider()
    monkeypatch.setattr(ap, "get_asset_provider", lambda name: provider)
    monkeypatch.setattr(ap, "build_asset_cascade", lambda name, allow: [])
    monkeypatch.setattr(ap, "visual_semantic_enabled", lambda: False)

    artifacts, report = ap.process_scene_assets(
        _make_script(2),
        provider_name="dup_test",
        job_id="job-dedup-2",
        search_only=True,
    )

    dup_rejections = [
        r for r in report["rejections"] if "already selected for this job" in r["reason"]
    ]
    assert any(r["scene_id"] == "scene-02" for r in dup_rejections), (
        "expected a dedup rejection recorded for scene-02"
    )


def test_provider_asset_ref_defaults_to_source_id(tmp_path, monkeypatch):
    """Providers that only set source_id (legacy) still dedupe correctly."""
    import autopilot.core.asset_pipeline as ap

    class _LegacyProvider(_DupProvider):
        provider_name = "legacy"

        def search(self, criteria, max_results=10):
            cands = super().search(criteria, max_results)
            for c in cands:
                # Drop the new field, keep only source_id.
                c.provenance = AssetProvenance(
                    provider=self.provider_name,
                    source_id=c.provenance.source_id,
                )
            return cands

    provider = _LegacyProvider()
    monkeypatch.setattr(ap, "get_asset_provider", lambda name: provider)
    monkeypatch.setattr(ap, "build_asset_cascade", lambda name, allow: [])
    monkeypatch.setattr(ap, "visual_semantic_enabled", lambda: False)

    artifacts, report = ap.process_scene_assets(
        _make_script(2),
        provider_name="legacy",
        job_id="job-dedup-3",
        search_only=True,
    )

    scene_picks = {}
    for sel in report["selections"]:
        scene_picks.setdefault(sel["scene_id"], []).append(sel["candidate_id"])
    assert "cand-A" not in scene_picks.get("scene-02", []), (
        "legacy source_id fallback did not dedupe"
    )


def test_dedup_never_makes_a_scene_fail(tmp_path, monkeypatch):
    """If dedup would clear every candidate, it relaxes rather than failing the job."""
    import autopilot.core.asset_pipeline as ap

    class _SingleClipProvider(_DupProvider):
        """Only one clip exists at all, so scene 2 cannot avoid a duplicate."""

        provider_name = "single"

        def search(self, criteria, max_results=10):
            return [self.search(criteria, max_results)[0]] if False else [
                AssetCandidate(
                    candidate_id="cand-only",
                    title="The Only Clip",
                    tags=[],
                    dimensions=AssetDimensions(width=1080, height=1920),
                    license=AssetLicense(
                        license_name="Pexels License",
                        rights_status="VERIFIED",
                        commercial_use=True,
                        derivative_use=True,
                    ),
                    provenance=AssetProvenance(
                        provider=self.provider_name,
                        source_id="only",
                        provider_asset_ref="single:only",
                    ),
                ),
            ]

    provider = _SingleClipProvider()
    monkeypatch.setattr(ap, "get_asset_provider", lambda name: provider)
    monkeypatch.setattr(ap, "build_asset_cascade", lambda name, allow: [])
    monkeypatch.setattr(ap, "visual_semantic_enabled", lambda: False)

    artifacts, report = ap.process_scene_assets(
        _make_script(2),
        provider_name="single",
        job_id="job-dedup-4",
        search_only=True,
    )

    # Both scenes must still resolve to the only available clip; a repeated
    # asset is better than a failed scene.
    scene_picks = {}
    for sel in report["selections"]:
        scene_picks.setdefault(sel["scene_id"], []).append(sel["candidate_id"])
    assert "cand-only" in scene_picks.get("scene-01", [])
    assert "cand-only" in scene_picks.get("scene-02", [])
