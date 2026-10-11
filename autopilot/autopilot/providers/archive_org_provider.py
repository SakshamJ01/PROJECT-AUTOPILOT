"""Archive.org Asset Provider — Internet Archive search + download.

Searches the Internet Archive's advancedsearch API for public-domain / CC
video footage and images, resolves the best downloadable file per item, and
returns canonical AssetCandidate models. No API key required.

License mapping: Archive.org items carry a licenseurl field; public-domain
and CC licenses map to VERIFIED, non-commercial / unknown map to REJECTED /
PARTIALLY_VERIFIED. All Archive.org content is free to download.
"""
from __future__ import annotations

import json
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from autopilot.providers.asset_contracts import AssetProvider
from autopilot.providers.contracts import (
    ProviderHealth, CapabilityMetadata, CostUsageMetadata, ProviderErrorType,
)
from autopilot.core.contracts import (
    AssetCandidate, AssetSelection, AssetLicense,
    AssetProvenance, AssetDimensions, AssetMediaInfo,
)

# Module-level cache, throttling, and circuit breaker
_SEARCH_CACHE: Dict[str, Tuple[float, List[AssetCandidate]]] = {}
_LAST_REQUEST_TIME: float = 0.0
_CIRCUIT_BROKEN_UNTIL: float = 0.0
_MIN_REQUEST_INTERVAL: float = 1.0
_SEARCH_CACHE_TTL_SEC: float = 3600.0

_MIME_BY_EXT = {
    ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
    ".mpg": "video/mpeg", ".mpeg": "video/mpeg", ".avi": "video/x-msavi",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".tif": "image/tiff", ".tiff": "image/tiff",
}

_VIDEO_EXTS = (".mp4", ".webm", ".mov", ".mpg", ".mpeg", ".avi")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif")


def _map_archive_license(license_url: Optional[str]) -> AssetLicense:
    """Map an Archive.org licenseurl to an explicit AssetLicense."""
    u = (license_url or "").lower()
    if not u:
        return AssetLicense(
            license_name="UNKNOWN", license_url="", source_url="",
            attribution_required=False, commercial_use=False, derivative_use=False,
            rights_status="UNKNOWN",
        )
    if any(k in u for k in ("publicdomain", "usgov", "cc0")):
        return AssetLicense(
            license_name="Public Domain", license_url=license_url or "", source_url="",
            attribution_required=False, commercial_use=True, derivative_use=True,
            rights_status="VERIFIED",
        )
    if "by-nc" in u or "by-nd" in u:
        return AssetLicense(
            license_name="CC Non-Commercial / No-Derivs",
            license_url=license_url or "", source_url="",
            attribution_required=True, commercial_use=False, derivative_use=False,
            rights_status="REJECTED",
        )
    if "by" in u:
        return AssetLicense(
            license_name="CC Attribution", license_url=license_url or "", source_url="",
            attribution_required=True, commercial_use=True, derivative_use=True,
            rights_status="VERIFIED",
        )
    return AssetLicense(
        license_name="Unknown License", license_url=license_url or "", source_url="",
        attribution_required=True, commercial_use=None, derivative_use=None,
        rights_status="PARTIALLY_VERIFIED",
    )


class ArchiveOrgAssetProvider(AssetProvider):
    provider_name = "archive_org"
    capability = CapabilityMetadata(
        max_resolution="1080p",
        supports_9_16=True,
        local_only=False,
        license_note="Internet Archive — public domain / CC media (verify per-item license)",
    )
    error_type = ProviderErrorType.NOT_AVAILABLE
    cost_meta = CostUsageMetadata(estimated_usd=0.0, provider_type="archive_api")

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    def _api_get(self, url: str) -> dict:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "PROJECT-AUTOPILOT/1.0 (asset-fetcher)"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def health_check(self) -> ProviderHealth:
        global _CIRCUIT_BROKEN_UNTIL
        if time.time() < _CIRCUIT_BROKEN_UNTIL:
            return ProviderHealth(
                healthy=False, provider_name=self.provider_name,
                error="Archive.org circuit breaker active", details={},
            )
        try:
            url = "https://archive.org/advancedsearch.php?q=test&fl[]=identifier&rows=1&output=json"
            data = self._api_get(url)
            healthy = data.get("responseHeader", {}).get("status", -1) == 0
            return ProviderHealth(
                healthy=healthy, provider_name=self.provider_name,
                error="" if healthy else "unexpected response",
                details={"status": data.get("responseHeader", {}).get("status")},
            )
        except Exception as exc:
            return ProviderHealth(
                healthy=False, provider_name=self.provider_name,
                error=f"Archive.org unreachable: {exc}", details={},
            )

    def _search_items(self, query: str, mediatype: str, max_results: int) -> List[dict]:
        global _LAST_REQUEST_TIME, _CIRCUIT_BROKEN_UNTIL
        if time.time() < _CIRCUIT_BROKEN_UNTIL:
            return []
        elapsed = time.time() - _LAST_REQUEST_TIME
        if elapsed < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
        params = urllib.parse.urlencode({
            "q": f"({query}) AND mediatype:{mediatype}",
            "fl[]": ["identifier", "title", "description", "licenseurl", "mediatype"],
            "rows": max_results,
            "page": 1,
            "output": "json",
        })
        url = f"https://archive.org/advancedsearch.php?{params}"
        _LAST_REQUEST_TIME = time.time()
        data = self._api_get(url)
        return data.get("response", {}).get("docs", [])

    def _get_best_file(self, identifier: str, want_video: bool) -> Optional[dict]:
        """Resolve the best downloadable file for an Archive.org item."""
        try:
            data = self._api_get(f"https://archive.org/metadata/{identifier}")
        except Exception:
            return None
        files = data.get("files", [])
        wanted_exts = _VIDEO_EXTS if want_video else _IMAGE_EXTS
        candidates = [
            f for f in files
            if f.get("name", "").lower().endswith(wanted_exts)
        ]
        if not candidates:
            return None
        # Prefer larger files (higher quality) within a reasonable bound
        def _size(f: dict) -> int:
            try:
                return int(f.get("size", 0))
            except (TypeError, ValueError):
                return 0
        candidates.sort(key=_size, reverse=True)
        best = candidates[0]
        name = best.get("name", "")
        return {
            "name": name,
            "format": (best.get("format") or ""),
            "size": _size(best),
            "download_url": f"https://archive.org/download/{identifier}/{urllib.parse.quote(name)}",
            "mimetype": _MIME_BY_EXT.get(name.lower().rsplit(".", 1)[-1], "application/octet-stream"),
        }

    def parse_items(self, docs: List[dict], want_video: bool) -> List[AssetCandidate]:
        """Convert Archive.org search docs into AssetCandidate models."""
        candidates: List[AssetCandidate] = []
        for doc in docs:
            identifier = doc.get("identifier")
            if not identifier:
                continue
            title = doc.get("title") or identifier
            desc = (doc.get("description") or "")
            if isinstance(desc, list):
                desc = " ".join(str(d) for d in desc)
            lic_url = doc.get("licenseurl")
            license_model = _map_archive_license(lic_url)
            provenance = AssetProvenance(
                provider=self.provider_name,
                source_url=f"https://archive.org/details/{identifier}",
                source_id=identifier,
                retrieval_timestamp=datetime.now(timezone.utc).isoformat(),
            )
            media_info = AssetMediaInfo(
                mime_type="video/mp4" if want_video else "image/jpeg",
                file_size_bytes=0,
            )
            candidate = AssetCandidate(
                candidate_id=f"archiveorg-{identifier}",
                asset_type="video" if want_video else "image",
                source_url=f"https://archive.org/details/{identifier}",
                source_id=identifier,
                title=title if isinstance(title, str) else str(title),
                dimensions=AssetDimensions(),
                media_info=media_info,
                license=license_model,
                score=0.0,
                provenance=provenance,
                path_local=None,
                is_duplicate=False,
                tags=["archive.org", doc.get("mediatype", "")],
            )
            candidates.append(candidate)
        return candidates

    def search(self, request: dict, max_results: int = 5, **kwargs) -> List[AssetCandidate]:
        global _CIRCUIT_BROKEN_UNTIL, _SEARCH_CACHE
        query = ""
        want_video = True
        if isinstance(request, dict):
            query = request.get("query") or request.get("q") or ""
            ar = str(request.get("aspect_ratio", "")).lower()
            asset_type = str(request.get("asset_type", "")).lower()
            if asset_type == "image" or ar in ("1:1", "square"):
                want_video = False
        if not query.strip():
            return []
        if time.time() < _CIRCUIT_BROKEN_UNTIL:
            return []

        mediatype = "movies" if want_video else "image"
        cache_key = f"{query.strip().lower()}:{mediatype}:{max_results}"
        if cache_key in _SEARCH_CACHE:
            ts, cands = _SEARCH_CACHE[cache_key]
            if time.time() - ts < _SEARCH_CACHE_TTL_SEC:
                return [c.model_copy(deep=True) for c in cands]

        try:
            docs = self._search_items(query.strip(), mediatype, max_results)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                _CIRCUIT_BROKEN_UNTIL = time.time() + 60.0
            return []
        except Exception:
            return []

        candidates = self.parse_items(docs, want_video)
        if candidates:
            _SEARCH_CACHE[cache_key] = (time.time(), [c.model_copy(deep=True) for c in candidates])
        return candidates

    def resolve_download(self, candidate: AssetCandidate) -> str | None:
        """Resolve a real download URL for an Archive.org candidate."""
        identifier = candidate.provenance.source_id
        want_video = candidate.asset_type == "video"
        best = self._get_best_file(identifier, want_video)
        if not best:
            return None
        # Stash the resolved URL + size onto the candidate for download()
        candidate.source_url = best["download_url"]
        candidate.media_info.mime_type = best["mimetype"]
        candidate.media_info.file_size_bytes = best["size"]
        return best["download_url"]

    def select(self, candidates: List[AssetCandidate], criteria: Optional[dict] = None) -> AssetSelection:
        from autopilot.core.asset_scoring import score_candidates
        from autopilot.core.rights_gate import evaluate_rights_gate

        if not candidates:
            return AssetSelection(
                selection_id="sel-empty", selected_candidates=[], selected_id=None,
                status="rejected", reason="No candidate assets available from Archive.org",
            )
        scored = score_candidates(candidates, criteria or {})
        selected_cand = None
        for cand in scored:
            if evaluate_rights_gate(cand.license).allowed:
                selected_cand = cand
                break
        if selected_cand:
            return AssetSelection(
                selection_id=f"sel-{selected_cand.candidate_id}",
                selected_candidates=scored,
                selected_id=selected_cand.candidate_id,
                status="selected",
                reason=f"Selected {selected_cand.candidate_id} (score {selected_cand.score:.2f})",
            )
        return AssetSelection(
            selection_id="sel-none-cleared", selected_candidates=scored,
            selected_id=None, status="rejected",
            reason="No candidate passed the rights and license gate",
        )

    def download(self, candidate: AssetCandidate, out_path: str, **kwargs) -> str:
        from autopilot.core.asset_cache import safe_download_media
        url = self.resolve_download(candidate)
        if not url:
            raise ValueError(f"Cannot resolve download URL for Archive.org item {candidate.provenance.source_id}")
        return safe_download_media(url, out_path)

    def normalize(self, artifact_path: str, out_path: str, **kwargs) -> str:
        from autopilot.core.asset_normalizer import normalize_asset
        return normalize_asset(artifact_path, out_path, **kwargs)
