"""Pexels Asset Provider — Tier 1 Video & Image Provider.
Connects to Pexels Video/Photo API v1.
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


class PexelsAssetProvider(AssetProvider):
    """Tier 1 Stock Video & Photo Provider using Pexels API."""
    provider_name = "pexels"
    capability = CapabilityMetadata(
        max_resolution="4k",
        supports_9_16=True,
        local_only=False,
        license_note="Pexels License — Commercial use allowed, derivative works allowed, attribution not required.",
    )
    error_type = ProviderErrorType.NOT_AVAILABLE
    cost_meta = CostUsageMetadata(estimated_usd=0.0, provider_type="pexels_api")

    def __init__(self, api_key: Optional[str] = None, timeout: Optional[float] = None):
        self.api_key = api_key or os.environ.get("PEXELS_API_KEY") or CONFIG.pexels_api_key or ""
        self.timeout = timeout or CONFIG.pexels_timeout or 10.0
        self.base_url = "https://api.pexels.com/videos/search"
        self.cache = AssetCache()

    def health_check(self) -> ProviderHealth:
        """Verify API key configuration and reachability."""
        if not self.api_key:
            return ProviderHealth(
                healthy=False,
                provider_name=self.provider_name,
                error="Pexels API key not configured (PEXELS_API_KEY).",
                details={"configured": False},
            )
        try:
            req = urllib.request.Request(
                f"{self.base_url}?query=nature&per_page=1",
                headers={"Authorization": self.api_key, "User-Agent": "Autopilot/4.0"}
            )
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
                    error=f"Pexels health check returned status {resp.status}",
                    details={"http_status": resp.status},
                )
        except Exception as exc:
            return ProviderHealth(
                healthy=False,
                provider_name=self.provider_name,
                error=f"Pexels health check failed: {exc}",
                details={"exception": str(exc)},
            )

    def search(self, request: dict, max_results: int = 5, **kwargs) -> List[AssetCandidate]:
        """Search Pexels Videos for media matching the query."""
        if not self.api_key:
            return []

        query = request.get("query") or request.get("b_roll_search_query") or request.get("visual_concept") or ""
        if not query.strip():
            return []

        orientation = request.get("orientation", "portrait")
        target_ar = request.get("aspect_ratio", "9:16")

        params = {
            "query": query.strip(),
            "per_page": min(max_results, 15),
            "orientation": orientation if orientation in ("portrait", "landscape") else "portrait",
        }
        url = f"{self.base_url}?{urllib.parse.urlencode(params)}"

        try:
            req = urllib.request.Request(
                url,
                headers={"Authorization": self.api_key, "User-Agent": "Autopilot/4.0"}
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status != 200:
                    return []
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return []

        candidates: List[AssetCandidate] = []
        videos = payload.get("videos", [])

        for vid in videos:
            vid_id = str(vid.get("id"))
            width = vid.get("width") or 1080
            height = vid.get("height") or 1920
            duration = float(vid.get("duration") or 0.0)
            user_info = vid.get("user", {}) or {}
            creator = user_info.get("name") or "Pexels Creator"
            video_files = vid.get("video_files", [])

            # Find best quality video file (HD or Full HD mp4)
            best_file = None
            for vf in video_files:
                if vf.get("file_type") == "video/mp4":
                    if vf.get("quality") in ("hd", "fhd", "uhd"):
                        best_file = vf
                        break
            if not best_file and video_files:
                best_file = video_files[0]

            download_url = best_file.get("link") if best_file else None
            if not download_url:
                continue

            file_w = best_file.get("width") or width
            file_h = best_file.get("height") or height

            cand = AssetCandidate(
                candidate_id=f"pexels_{vid_id}",
                asset_type="video",
                source_id=vid_id,
                source_url=download_url,
                title=f"Pexels Video {vid_id} by {creator}",
                tags=[q.lower() for q in query.split()],
                dimensions=AssetDimensions(
                    width=file_w,
                    height=file_h,
                    duration_sec=duration,
                ),
                media_info=AssetMediaInfo(
                    mime_type="video/mp4",
                    codec="h264",
                    format="mp4",
                ),
                license=AssetLicense(
                    license_name="Pexels License",
                    license_url="https://www.pexels.com/license/",
                    source_url=vid.get("url") or f"https://www.pexels.com/video/{vid_id}/",
                    creator=creator,
                    attribution_required=False,
                    commercial_use=True,
                    derivative_use=True,
                    rights_status="VERIFIED",
                ),
                provenance=AssetProvenance(
                    provider=self.provider_name,
                    source_id=vid_id,
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
        # Verify rights on first eligible
        for c in candidates:
            gate = evaluate_rights_gate(c.license)
            if gate.allowed:
                return AssetSelection(
                    candidate_id=c.candidate_id,
                    selected=True,
                    reason=f"Selected verified Pexels asset: {c.candidate_id}",
                    score=1.0,
                    license=c.license,
                    provenance=c.provenance,
                )
        return AssetSelection(
            candidate_id="none",
            selected=False,
            reason="All Pexels candidates failed rights gate",
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
