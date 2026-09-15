"""Milestone 3 Real Asset Engine tests — completely offline and deterministic."""
import os
import json
import tempfile
import pytest
from pathlib import Path
from PIL import Image

from autopilot.core.config import CONFIG
from autopilot.core.contracts import (
    AssetRequest, AssetCandidate, AssetSelection, AssetArtifact,
    AssetLicense, AssetValidationResult, AssetDimensions, AssetMediaInfo,
    AssetProvenance, ScriptDocument, ScriptScene, RenderPlan
)
from autopilot.providers.local_asset_provider import LocalAssetProvider
from autopilot.providers.openverse_provider import OpenverseAssetProvider, map_openverse_license
from autopilot.core.asset_scoring import score_candidate, score_candidates
from autopilot.core.rights_gate import evaluate_rights_gate
from autopilot.core.asset_cache import (
    AssetCache, safe_download_media, compute_file_sha256,
    compute_image_phash, sanitize_filename
)
from autopilot.core.asset_normalizer import normalize_image, normalize_asset
from autopilot.core.asset_pipeline import process_scene_assets
from autopilot.core.media_inspection import inspect_media
from autopilot.core.renderer import FFmpegRenderer
from autopilot.db.manager import DBManager


# ------------------------------------------------------------------
# 1. Contracts & Metadata Tests
# ------------------------------------------------------------------
def test_asset_contracts_creation():
    lic = AssetLicense(
        license_name="CC0-1.0",
        rights_status="VERIFIED",
        source_url="https://example.com/item",
        creator="Test Author",
        commercial_use=True,
        derivative_use=True,
    )
    assert lic.rights_status == "VERIFIED"
    assert lic.commercial_use is True

    req = AssetRequest(
        asset_request_id="req-1",
        scene_id="scene-1",
        query="golden hour forest",
        aspect_ratio="9:16",
    )
    assert req.query == "golden hour forest"

    cand = AssetCandidate(
        candidate_id="cand-1",
        asset_type="image",
        source_url="https://example.com/image.jpg",
        title="Forest",
        license=lic,
        score=0.95,
    )
    assert cand.candidate_id == "cand-1"
    assert cand.score == 0.95


# ------------------------------------------------------------------
# 2. Openverse Provider & License Mapping Tests (Mocked / Offline)
# ------------------------------------------------------------------
def test_openverse_license_mapping():
    # CC0 -> Verified
    l_cc0 = map_openverse_license("cc0", creator="Artist A")
    assert l_cc0.rights_status == "VERIFIED"
    assert l_cc0.attribution_required is False
    assert l_cc0.commercial_use is True

    # BY -> Verified with attribution
    l_by = map_openverse_license("by", license_version="4.0", creator="Artist B")
    assert l_by.rights_status == "VERIFIED"
    assert l_by.attribution_required is True
    assert l_by.commercial_use is True

    # BY-NC -> Rejected for commercial factory
    l_nc = map_openverse_license("by-nc", creator="Artist C")
    assert l_nc.rights_status == "REJECTED"
    assert l_nc.commercial_use is False

    # Missing / None -> UNKNOWN
    l_unk = map_openverse_license(None)
    assert l_unk.rights_status == "UNKNOWN"


def test_openverse_parse_mock_response():
    provider = OpenverseAssetProvider()
    mock_payload = {
        "result_count": 2,
        "results": [
            {
                "id": "12345",
                "title": "Ancient Clock Tower",
                "creator": "Photographer X",
                "url": "https://images.example.com/clock.jpg",
                "foreign_landing_url": "https://example.com/photos/12345",
                "license": "cc0",
                "license_version": "1.0",
                "filetype": "jpg",
                "filesize": 1048576,
                "width": 1080,
                "height": 1920,
            },
            {
                "id": "67890",
                "title": "Restricted Painting",
                "creator": "Painter Y",
                "url": "https://images.example.com/painting.png",
                "license": "by-nc-nd",
                "license_version": "4.0",
                "filetype": "png",
                "width": 800,
                "height": 600,
            }
        ]
    }
    candidates = provider.parse_api_response(mock_payload)
    assert len(candidates) == 2
    assert candidates[0].candidate_id == "openverse-12345"
    assert candidates[0].license.rights_status == "VERIFIED"
    assert candidates[0].dimensions.width == 1080
    assert candidates[1].license.rights_status == "REJECTED"


def test_openverse_empty_and_malformed_handling():
    provider = OpenverseAssetProvider()
    empty_res = provider.parse_api_response({"results": []})
    assert len(empty_res) == 0

    sel = provider.select([])
    assert sel.status == "rejected"
    assert sel.selected_id is None


# ------------------------------------------------------------------
# 3. Candidate Scoring & Rights Gate Tests
# ------------------------------------------------------------------
def test_candidate_scoring_factors():
    # 1. Portrait Verified candidate matching query
    good_cand = AssetCandidate(
        candidate_id="good-1",
        title="Golden sunset over mountain valley",
        dimensions=AssetDimensions(width=1080, height=1920),
        license=AssetLicense(rights_status="VERIFIED", commercial_use=True, derivative_use=True),
    )
    score_good, bd_good = score_candidate(good_cand, {"query": "mountain valley"}, 1080, 1920)
    assert score_good > 0.80
    assert bd_good["rights_score"] == 1.0
    assert bd_good["aspect_ratio_score"] > 0.90

    # 2. Rejected rights candidate
    bad_rights_cand = AssetCandidate(
        candidate_id="bad-1",
        title="Mountain view",
        dimensions=AssetDimensions(width=1080, height=1920),
        license=AssetLicense(rights_status="REJECTED", commercial_use=False),
    )
    score_bad, _ = score_candidate(bad_rights_cand, {"query": "mountain"}, 1080, 1920)
    assert score_bad <= 0.10

    # 3. Ranking order
    ranked = score_candidates([bad_rights_cand, good_cand], {"query": "mountain valley"})
    assert ranked[0].candidate_id == "good-1"


def test_rights_gate_policy():
    # VERIFIED
    res_v = evaluate_rights_gate(AssetLicense(license_name="CC0", rights_status="VERIFIED", commercial_use=True, derivative_use=True))
    assert res_v.allowed is True

    # UNKNOWN (Blocked by default)
    res_u = evaluate_rights_gate(AssetLicense(license_name="UNKNOWN", rights_status="UNKNOWN"))
    assert res_u.allowed is False

    # REJECTED (Blocked)
    res_r = evaluate_rights_gate(AssetLicense(license_name="CC BY-NC", rights_status="REJECTED", commercial_use=False))
    assert res_r.allowed is False


# ------------------------------------------------------------------
# 4. Safe Downloader & Durable Cache Tests
# ------------------------------------------------------------------
def test_safe_downloader_and_cache_cycle(tmp_path):
    cache_dir = tmp_path / "cache"
    cache = AssetCache(cache_dir=cache_dir)

    # Create dummy local source file
    src_file = tmp_path / "source.png"
    img = Image.new("RGB", (640, 480), color=(255, 0, 0))
    img.save(src_file)

    out_dest = tmp_path / "downloaded.png"
    downloaded = safe_download_media(str(src_file), out_dest, use_cache=True, cache=cache)
    assert Path(downloaded).exists()
    sha = compute_file_sha256(downloaded)
    assert len(sha) == 64

    # Put in cache & check cache hit
    cache.put("https://mock.test/img.png", Path(downloaded), "image/png")
    cached_get = cache.get("https://mock.test/img.png")
    assert cached_get is not None
    cached_path, meta = cached_get
    assert meta["cache_status"] == "CACHE_HIT"
    assert cached_path.exists()

    # Invalidate
    cache.invalidate("https://mock.test/img.png")
    assert cache.get("https://mock.test/img.png") is None


def test_filename_sanitization():
    assert sanitize_filename("../../../etc/passwd.jpg") == "etc_passwd.jpg"
    assert sanitize_filename("bad;rm -rf /;image.png") == "bad_rm_-rf_image.png"
    assert ".." not in sanitize_filename("../../../secret.png")
    assert "/" not in sanitize_filename("path/to/file.png")
    assert "\\" not in sanitize_filename("path\\to\\file.png")


def test_perceptual_hash(tmp_path):
    img_path = tmp_path / "phash_test.png"
    img = Image.new("RGB", (100, 100), color=(120, 120, 120))
    img.save(img_path)
    phash = compute_image_phash(img_path)
    assert phash is not None
    assert len(phash) == 16


# ------------------------------------------------------------------
# 5. Asset Normalization Tests (Images & Dimensions)
# ------------------------------------------------------------------
def test_image_normalization_strategies(tmp_path):
    src_img = tmp_path / "input_landscape.png"
    # Create landscape 800x400
    img = Image.new("RGB", (800, 400), color=(0, 128, 255))
    img.save(src_img)

    # 1. Crop to 9:16 (540x960 for test speed)
    out_crop = tmp_path / "norm_crop.png"
    res_crop = normalize_image(src_img, out_crop, target_width=540, target_height=960, strategy="crop")
    assert res_crop["width"] == 540
    assert res_crop["height"] == 960
    with Image.open(out_crop) as res_im:
        assert res_im.size == (540, 960)

    # 2. Pad to 9:16
    out_pad = tmp_path / "norm_pad.png"
    res_pad = normalize_image(src_img, out_pad, target_width=540, target_height=960, strategy="pad")
    with Image.open(out_pad) as res_im:
        assert res_im.size == (540, 960)

    # 3. Fit (blurred background)
    out_fit = tmp_path / "norm_fit.png"
    res_fit = normalize_image(src_img, out_fit, target_width=540, target_height=960, strategy="fit")
    with Image.open(out_fit) as res_im:
        assert res_im.size == (540, 960)


# ------------------------------------------------------------------
# 6. Scene-to-Asset Pipeline Orchestration
# ------------------------------------------------------------------
def test_scene_to_asset_pipeline_offline(tmp_path):
    db_path = tmp_path / "test.db"
    db = DBManager(db_path)
    db.init_schema()

    script = ScriptDocument(
        content_id="job-asset-test-01",
        topic="Architecture of Bridges",
        scenes=[
            ScriptScene(
                scene_id="s1",
                order=1,
                narration="Suspension bridges span incredible distances.",
                visual_intent="Golden Gate Bridge",
                asset_query="bridge",
                estimated_duration_seconds=3.0,
            ),
            ScriptScene(
                scene_id="s2",
                order=2,
                narration="Arch bridges rely on stone and steel compression.",
                visual_intent="Stone Arch Bridge",
                asset_query="arch bridge",
                estimated_duration_seconds=3.0,
            )
        ]
    )

    artifacts, report = process_scene_assets(
        script=script,
        job_id="job-asset-test-01",
        provider_name="local",
        db=db,
    )

    assert len(artifacts) == 2
    assert report["successful_selections"] == 2
    assert Path(artifacts[0].normalized_path).exists()
    assert artifacts[0].validated is True

    # Verify DB persistence
    db_rows = db.get_asset_artifacts_for_job("job-asset-test-01")
    assert len(db_rows) == 2
    assert db_rows[0]["scene_id"] == "s1"


# ------------------------------------------------------------------
# 7. Renderer Dynamic Asset Path Consumption
# ------------------------------------------------------------------
def test_renderer_consumes_dynamic_asset(tmp_path):
    renderer = FFmpegRenderer(profile="vertical_short")

    # Create a distinct custom normalized image
    custom_asset = tmp_path / "custom_scene_asset.png"
    img = Image.new("RGB", (1080, 1920), color=(34, 139, 34))  # Forest green
    img.save(custom_asset)

    plan = RenderPlan(
        plan_id="plan-dynamic-1",
        content_id="c-dyn-1",
        job_id="job-dyn-1",
        profile="vertical_short",
        scenes=[{
            "scene_id": "scene-01",
            "duration_sec": 2.0,
            "asset_path": str(custom_asset),
        }]
    )

    out_mp4 = tmp_path / "dynamic_render.mp4"
    res = renderer.render(plan, str(out_mp4))
    assert Path(res.output_path).exists()
    assert Path(res.output_path).stat().st_size > 0
    inspection = inspect_media(res.output_path)
    assert inspection["valid"] is True
    assert inspection["width"] == 1080
    assert inspection["height"] == 1920
