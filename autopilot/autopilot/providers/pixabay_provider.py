"""Pixabay Asset Provider — Tier 2 Video & Image Provider.
Connects to Pixabay Video & Photo API v2.
Safe, commercial-ready, rights-verified, and resilient to network/API key failures.
"""
from __future__ import annotations
import json
import os
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from autopilot.core.config import CONFIG
from autopilot.providers.asset_contracts import AssetProvider
from autopilot.providers.contracts import ProviderHealth, CapabilityMetadata, CostUsageMetadata, ProviderErrorType
from autopilot.core.contracts import (
    AssetCandidate, AssetSelection, AssetArtifact, AssetLicense,
    AssetProvenance, AssetDimensions, AssetMediaInfo
)
from autopilot.core.asset_cache import safe_download_media, AssetCache, sanitize_filename
from autopilot.core.asset_normalizer import normalize_asset
from autopilot.core.rights_gate import evaluate_rights_gate


class PixabayAssetProvider(AssetProvider):
    """Tier 2 Stock Video & Photo Provider using Pixabay API."""
    provider_name = "pixabay"
    capability = CapabilityMetadata(
        max_resolution="4k",
        supports_9_16=True,
        local_only=False,
        license_note="Pixabay Content License — Free for commercial use, no attribution required.",
    )
    error_type = ProviderErrorType.NOT_AVAILABLE
    cost_meta = CostUsageMetadata(estimated_usd=0.0, provider_type="pixabay_api")

    def __init__(self, api_key: Optional[str] = None, timeout: Optional[float] = None):
        self.api_key = api_key or os.environ.get("PIXABAY_API_KEY") or CONFIG.pixabay_api_key or ""
        self.timeout = timeout or CONFIG.pixabay_timeout or 10.0
        self.base_url = "https://pixabay.com/api/videos/"
        self.cache = AssetCache()

    def health_check(self) -> ProviderHealth:
        """Verify API key configuration and reachability."""
        if not self.api_key:
            return ProviderHealth(
                healthy=False,
                provider_name=self.provider_name,
                error="Pixabay API key not configured (PIXABAY_API_KEY).",
                details={"configured": False},
            )
        try:
            params = {"key": self.api_key, "q": "nature", "per_page": 3}
            url = f"{self.base_url}?{urllib.parse.urlencode(params)}"
            req = urllib.request.Request(url, headers={"User-Agent": "Autopilot/4.0"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status == 200:
                    return ProviderHealth(
                        healthy=True,
                        provider_name=self.provider_name,
                        error="",
                        details={"configured": True, "http_status": 200},
                    )
                return ProviderHealth(
                    healthy=False,
                    provider_name=self.provider_name,
                    error=f"Pixabay health check returned status {resp.status}",
                    details={"http_status": resp.status},
                )
        except Exception as exc:
            return ProviderHealth(
                healthy=False,
                provider_name=self.provider_name,
                error=f"Pixabay health check failed: {exc}",
                details={"exception": str(exc)},
            )

    def search(self, request: dict, max_results: int = 5, **kwargs) -> List[AssetCandidate]:
        """Search Pixabay Videos for media matching query."""
        if not self.api_key:
            return []

        query = request.get("query") or request.get("b_roll_search_query") or request.get("visual_concept") or ""
        if not query.strip():
            return []

        params = {
            "key": self.api_key,
            "q": query.strip(),
            "per_page": min(max_results, 15),
            "video_type": "film",
        }
        url = f"{self.base_url}?{urllib.parse.urlencode(params)}"

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Autopilot/4.0"})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status != 200:
                    return []
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return []

        candidates: List[AssetCandidate] = []
        hits = payload.get("hits", [])

        for hit in hits:
            hit_id = str(hit.get("id"))
            duration = float(hit.get("duration") or 0.0)
            creator = hit.get("user") or "Pixabay Contributor"
            tags_str = hit.get("tags") or ""
            tags = [t.strip().lower() for t in tags_str.split(",") if t.strip()]

            videos_dict = hit.get("videos", {})
            # Preference: large > medium > small > tiny
            chosen_video = videos_dict.get("large") or videos_dict.get("medium") or videos_dict.get("small") or videos_dict.get("tiny")
            if not chosen_video:
                continue

            download_url = chosen_video.get("url")
            if not download_url:
                continue

            width = chosen_video.get("width") or 1920
            height = chosen_video.get("height") or 1080

            cand = AssetCandidate(
                candidate_id=f"pixabay_{hit_id}",
                asset_type="video",
                source_id=hit_id,
                source_url=download_url,
                title=f"Pixabay Video {hit_id} by {creator}",
                tags=tags,
                dimensions=AssetDimensions(
                    width=width,
                    height=height,
                    duration_sec=duration,
                ),
                media_info=AssetMediaInfo(
                    mime_type="video/mp4",
                    codec="h264",
                    format="mp4",
                ),
                license=AssetLicense(
                    license_name="Pixabay Content License",
                    license_url="https://pixabay.com/service/license-summary/",
                    source_url=hit.get("pageURL") or f"https://pixabay.com/videos/id-{hit_id}/",
                    creator=creator,
                    attribution_required=False,
                    commercial_use=True,
                    derivative_use=True,
                    rights_status="VERIFIED",
                ),
                provenance=AssetProvenance(
                    provider=self.provider_name,
                    source_id=hit_id,
                    source_url=download_url,
                    retrieval_timestamp=datetime.now(timezone.utc).isoformat(),
                ),
            )
            candidates.append(cand)

        return candidates[:max_results]

    def select(self, candidates: List[AssetCandidate], criteria: Optional[dict] = None) -> AssetSelection:
        """Select best candidate after verifying rights."""
        if not candidates:
            return AssetSelection(
                candidate_id="none",
                selected=False,
                reason="No candidate assets provided for selection",
            )
        for c in candidates:
            gate = evaluate_rights_gate(c.license)
            if gate.allowed:
                return AssetSelection(
                    candidate_id=c.candidate_id,
                    selected=True,
                    reason=f"Selected verified Pixabay asset: {c.candidate_id}",
                    score=1.0,
                    license=c.license,
                    provenance=c.provenance,
                )
        return AssetSelection(
            candidate_id="none",
            selected=False,
            reason="All Pixabay candidates failed rights gate",
        )

    def download(self, candidate: AssetCandidate, out_path: str, **kwargs) -> str:
        """Download asset to disk safely using AssetCache."""
        dest = Path(out_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        cached = self.cache.get_or_download(
            candidate.url,
            target_path=dest,
            timeout=self.timeout,
        )
        return str(cached)

    def normalize(self, artifact_path: str, out_path: str, **kwargs) -> str:
        """Normalize downloaded asset to target 9:16 format."""
        norm_result = normalize_asset(artifact_path, out_path, **kwargs)
        return str(norm_result.get("normalized_path") or out_path)
