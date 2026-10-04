"""Pexels Asset Provider — Tier 1 Video & Image Provider.
Connects to Pexels Video/Photo API v1.
Safe, commercial-ready, rights-verified, and resilient to network/API key failures.
"""
from __future__ import annotations
import json
import os
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

# Module-level rate limiting, circuit breaker, and caching (mirrors openverse).
_LAST_PEXELS_REQUEST_TIME: float = 0.0
_PEXELS_CIRCUIT_BROKEN_UNTIL: float = 0.0
_MIN_PEXELS_REQUEST_INTERVAL: float = 0.5
_PEXELS_SEARCH_CACHE: Dict[str, Tuple[float, List[AssetCandidate]]] = {}
_PEXELS_SEARCH_CACHE_TTL: float = 3600.0


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
        # An explicitly supplied key (including "") is authoritative. Only
        # fall back to environment/config when the caller passes nothing.
        if api_key is None:
            api_key = os.environ.get("PEXELS_API_KEY") or CONFIG.pexels_api_key or ""
        self.api_key = api_key
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
        """Search Pexels Videos for media matching the query.

        Includes retries with exponential backoff (429/5xx), client-side rate
        limiting, a circuit breaker, and short-TTL query caching.
        """
        global _LAST_PEXELS_REQUEST_TIME, _PEXELS_CIRCUIT_BROKEN_UNTIL, _PEXELS_SEARCH_CACHE

        if not self.api_key:
            return []

        query = request.get("query") or request.get("b_roll_search_query") or request.get("visual_concept") or ""
        if not query.strip():
            return []

        if time.time() < _PEXELS_CIRCUIT_BROKEN_UNTIL:
            return []

        orientation = request.get("orientation", "portrait")

        cache_key = f"{query.strip().lower()}:{orientation}:{max_results}"
        if cache_key in _PEXELS_SEARCH_CACHE:
            cached_ts, cached_cands = _PEXELS_SEARCH_CACHE[cache_key]
            if time.time() - cached_ts < _PEXELS_SEARCH_CACHE_TTL:
                return [c.model_copy(deep=True) for c in cached_cands]

        params = {
            "query": query.strip(),
            "per_page": min(max_results, 15),
            "orientation": orientation if orientation in ("portrait", "landscape") else "portrait",
        }
        url = f"{self.base_url}?{urllib.parse.urlencode(params)}"

        payload = None
        last_exc: Optional[Exception] = None
        for attempt in range(3):
            # Rate limiting
            elapsed = time.time() - _LAST_PEXELS_REQUEST_TIME
            if elapsed < _MIN_PEXELS_REQUEST_INTERVAL:
                time.sleep(_MIN_PEXELS_REQUEST_INTERVAL - elapsed)
            try:
                _LAST_PEXELS_REQUEST_TIME = time.time()
                req = urllib.request.Request(
                    url,
                    headers={"Authorization": self.api_key, "User-Agent": "Autopilot/4.0"}
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    if resp.status == 200:
                        payload = json.loads(resp.read().decode("utf-8"))
                        break
                    last_exc = RuntimeError(f"Pexels search returned status {resp.status}")
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    # Break the circuit for a real cooldown window, otherwise
                    # every subsequent request hammers a rate-limited API.
                    _PEXELS_CIRCUIT_BROKEN_UNTIL = time.time() + 60.0
                    return []
                if exc.code >= 500 and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                return []
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                return []
        if payload is None:
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

            # Pick the best mp4 file that actually fits the download budget.
            # The target output is 1080x1920, so a huge 4K master is wasted
            # bandwidth and gets rejected by the download size guard.
            quality_rank = {"fhd": 4, "hd": 3, "sd": 2, "uhd": 1}
            max_bytes = CONFIG.asset_max_download_bytes
            mp4_files = [vf for vf in video_files if vf.get("file_type") == "video/mp4"]
            if mp4_files:
                def _fits(vf) -> bool:
                    size = vf.get("file_size")
                    return not size or int(size) <= max_bytes

                affordable = [vf for vf in mp4_files if _fits(vf)]
                pool = affordable or mp4_files
                best_file = max(
                    pool,
                    key=lambda vf: (quality_rank.get(vf.get("quality"), 0), vf.get("width") or 0),
                )
                if not affordable:
                    # Nothing fits the budget: take the smallest available.
                    best_file = min(
                        mp4_files,
                        key=lambda vf: (vf.get("file_size") or 0, -(vf.get("width") or 0)),
                    )
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
                    # Stable Pexels asset ID is known at search time, before any
                    # download. This is what asset_pipeline's hard dedup keys on.
                    provider_asset_ref=f"pexels:{vid_id}",
                ),
            )
            candidates.append(cand)

        # Cache this query result
        if candidates:
            _PEXELS_SEARCH_CACHE[cache_key] = (time.time(), [c.model_copy(deep=True) for c in candidates])
        return candidates[:max_results]

    def select(self, candidates: List[AssetCandidate], criteria: Optional[dict] = None) -> AssetSelection:
        """Select the best-scoring rights-cleared candidate (never score=1.0)."""
        from autopilot.core.asset_scoring import score_candidates

        if not candidates:
            return AssetSelection(
                selection_id="sel-empty",
                selected_candidates=[],
                selected_id=None,
                status="rejected",
                reason="No candidate assets provided for selection",
            )
        scored = score_candidates(candidates, criteria or {})
        for c in scored:
            gate = evaluate_rights_gate(c.license)
            if gate.allowed and c.score > 0.0:
                return AssetSelection(
                    selection_id=f"sel-{c.candidate_id}",
                    selected_candidates=scored,
                    selected_id=c.candidate_id,
                    status="selected",
                    reason=f"Selected Pexels asset {c.candidate_id} (score {c.score:.3f}, license {c.license.license_name})",
                    score=c.score,
                    semantic_score=c.provenance.semantic_score,
                )
        return AssetSelection(
            selection_id="sel-none-cleared",
            selected_candidates=scored,
            selected_id=None,
            status="rejected",
            reason="All Pexels candidates failed rights gate or visual-semantic gate",
        )

    def download(self, candidate: AssetCandidate, out_path: str, **kwargs) -> str:
        """Download asset to disk safely using the safe media downloader + cache."""
        dest = Path(out_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not candidate.source_url:
            raise ValueError(f"Pexels candidate {candidate.candidate_id} has no download URL")
        return safe_download_media(
            candidate.source_url,
            dest,
            timeout=self.timeout,
            max_bytes=CONFIG.asset_max_download_bytes,
        )

    def normalize(self, artifact_path: str, out_path: str, **kwargs) -> str:
        """Normalize downloaded asset to target 9:16 format."""
        # normalize_asset returns the output path as a string.
        norm_result = normalize_asset(artifact_path, out_path, **kwargs)
        return str(norm_result or out_path)
