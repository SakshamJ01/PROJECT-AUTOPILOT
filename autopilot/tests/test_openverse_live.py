"""Opt-In Live Openverse Integration Test.
IMPORTANT:
- NEVER run in default offline pytest suite.
- Run ONLY with explicit opt-in: `python -m pytest -m live`
"""
import pytest
import os
from pathlib import Path

from autopilot.providers.openverse_provider import OpenverseAssetProvider
from autopilot.core.asset_cache import safe_download_media
from autopilot.core.media_inspection import inspect_media
from autopilot.core.asset_normalizer import normalize_image


@pytest.mark.live
def test_live_openverse_search_and_fetch(tmp_path):
    """Opt-in live integration test verifying live Openverse queries."""
    provider = OpenverseAssetProvider()
    health = provider.health_check()
    if not health.healthy:
        pytest.skip(f"Live Openverse API not reachable: {health.error}")

    # Search for a single small public domain / CC0 item
    candidates = provider.search({"query": "nature", "aspect_ratio": "9:16"}, max_results=2)
    assert len(candidates) > 0, "No candidates returned from live Openverse query"

    cand = candidates[0]
    assert cand.source_url is not None
    assert cand.license is not None
    assert cand.license.rights_status in ("VERIFIED", "PARTIALLY_VERIFIED", "REJECTED", "UNKNOWN")

    # Download one item to temp path
    dest_path = tmp_path / f"live_{cand.candidate_id}.jpg"
    try:
        downloaded = safe_download_media(cand.source_url, dest_path, max_bytes=10 * 1024 * 1024)
        assert Path(downloaded).exists()
        assert Path(downloaded).stat().st_size > 0

        # Inspect downloaded image
        inspection = inspect_media(downloaded)
        assert inspection["valid"] is True

        # Test normalization
        norm_dest = tmp_path / f"norm_{cand.candidate_id}.png"
        normalized = normalize_image(downloaded, norm_dest, target_width=540, target_height=960)
        assert Path(normalized["output_path"]).exists()
    finally:
        # Cleanup
        if dest_path.exists():
            dest_path.unlink()
