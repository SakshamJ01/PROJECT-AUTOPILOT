"""Wikimedia Commons Asset Provider — free / CC image + video search.

Searches Wikimedia Commons (namespace 6 = File) via the MediaWiki API,
resolves the best image/video file per result, and returns canonical
AssetCandidate models. No API key required.

License mapping: Commons files carry structured license metadata
(LicenseShortName). CC0 / Public Domain / CC-BY / CC-BY-SA map to VERIFIED;
NC / ND / unknown map to REJECTED / PARTIALLY_VERIFIED.
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

_SEARCH_CACHE: Dict[str, Tuple[float, List[AssetCandidate]]] = {}
_LAST_REQUEST_TIME: float = 0.0
_CIRCUIT_BROKEN_UNTIL: float = 0.0
_MIN_REQUEST_INTERVAL: float = 1.0
_SEARCH_CACHE_TTL_SEC: float = 3600.0

_MIME_BY_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".svg": "image/svg+xml", ".tif": "image/tiff",
    ".tiff": "image/tiff", ".webm": "video/webm", ".ogv": "video/ogg",
    ".mp4": "video/mp4", ".mov": "video/quicktime",
}

_VIDEO_EXTS = (".webm", ".ogv", ".mp4", ".mov")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".tif", ".tiff")


def _map_commons_license(short_name: Optional[str], license_url: Optional[str] = None) -> AssetLicense:
    """Map a Wikimedia Commons LicenseShortName to an explicit AssetLicense."""
    s = (short_name or "").lower().strip()
    if not s:
        return AssetLicense(
            license_name="UNKNOWN", license_url=license_url or "", source_url="",
            attribution_required=False, commercial_use=False, derivative_use=False,
            rights_status="UNKNOWN",
        )
    if any(k in s for k in ("cc0", "public domain", "pd-self", "pd-old")):
        return AssetLicense(
            license_name="CC0 / Public Domain", license_url=license_url or "", source_url="",
            attribution_required=False, commercial_use=True, derivative_use=True,
            rights_status="VERIFIED",
        )
    if "by-nc" in s or "by-nd" in s or "noncommercial" in s:
        return AssetLicense(
            license_name="CC Non-Commercial / No-Derivs",
            license_url=license_url or "", source_url="",
            attribution_required=True, commercial_use=False, derivative_use=False,
            rights_status="REJECTED",
        )
    if "by-sa" in s or "by" in s:
        return AssetLicense(
            license_name="CC Attribution" + ("-ShareAlike" if "by-sa" in s else ""),
            license_url=license_url or "", source_url="",
            attribution_required=True, commercial_use=True, derivative_use=True,
            rights_status="VERIFIED",
        )
    return AssetLicense(
        license_name="Unknown License", license_url=license_url or "", source_url="",
        attribution_required=True, commercial_use=None, derivative_use=None,
        rights_status="PARTIALLY_VERIFIED",
    )


class WikimediaCommonsAssetProvider(AssetProvider):
    provider_name = "wikimedia_commons"
    capability = CapabilityMetadata(
        max_resolution="4k",
        supports_9_16=True,
        local_only=False,
        license_note="Wikimedia Commons — free / CC media (verify per-file license)",
    )
    error_type = ProviderErrorType.NOT_AVAILABLE
    cost_meta = CostUsageMetadata(estimated_usd=0.0, provider_type="commons_api")

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    def _api_get(self, params: dict) -> dict:
        url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
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
                error="Wikimedia Commons circuit breaker active", details={},
            )
        try:
            data = self._api_get({
                "action": "query", "meta": "siteinfo", "format": "json",
            })
            healthy = "query" in data
            return ProviderHealth(
                healthy=healthy, provider_name=self.provider_name,
                error="" if healthy else "unexpected response",
                details={},
            )
        except Exception as exc:
            return ProviderHealth(
                healthy=False, provider_name=self.provider_name,
                error=f"Wikimedia Commons unreachable: {exc}", details={},
            )

    def _search_files(self, query: str, max_results: int) -> List[dict]:
        global _LAST_REQUEST_TIME, _CIRCUIT_BROKEN_UNTIL
        if time.time() < _CIRCUIT_BROKEN_UNTIL:
            return []
        elapsed = time.time() - _LAST_REQUEST_TIME
        if elapsed < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
        _LAST_REQUEST_TIME = time.time()
        data = self._api_get({
            "action": "query", "list": "search", "srsearch": query,
            "srnamespace": "6", "srlimit": max_results, "format": "json",
        })
        return data.get("query", {}).get("search", [])

    def _get_file_info(self, title: str) -> Optional[dict]:
        """Resolve the best file (image or video) for a Commons title."""
        try:
            data = self._api_get({
                "action": "query", "prop": "imageinfo",
                "iiprop": "url|size|mime|extmetadata",
                "titles": title, "format": "json",
            })
        except Exception:
            return None
        pages = data.get("query", {}).get("pages", {})
        for _pid, page in pages.items():
            info = (page.get("imageinfo") or [{}])[0]
            url = info.get("url")
            if not url:
                continue
            ext = "." + url.lower().rsplit(".", 1)[-1].split("?")[0] if "." in url else ""
            is_video = ext in _VIDEO_EXTS
            meta = info.get("extmetadata", {})
            lic_short = meta.get("LicenseShortName", {}).get("value")
            lic_url = meta.get("License", {}).get("value")
            return {
                "url": url,
                "ext": ext,
                "is_video": is_video,
                "width": info.get("width"),
                "height": info.get("height"),
                "mime": info.get("mime") or _MIME_BY_EXT.get(ext, "application/octet-stream"),
                "size": info.get("size", 0),
                "license_short": lic_short,
                "license_url": lic_url,
                "artist": meta.get("Artist", {}).get("value", ""),
            }
        return None

    def parse_results(self, results: List[dict]) -> List[AssetCandidate]:
        candidates: List[AssetCandidate] = []
        for r in results:
            title = r.get("title", "")
            if not title:
                continue
            info = self._get_file_info(title)
            if not info:
                continue
            is_video = info["is_video"]
            license_model = _map_commons_license(info["license_short"], info["license_url"])
            # Strip HTML tags from artist
            import re
            artist = re.sub(r"<[^>]+>", "", info["artist"])[:200]
            provenance = AssetProvenance(
                provider=self.provider_name,
                source_url=info["url"],
                source_id=title,
                retrieval_timestamp=datetime.now(timezone.utc).isoformat(),
            )
            dimensions = AssetDimensions(
                width=info.get("width"),
                height=info.get("height"),
            )
            media_info = AssetMediaInfo(
                mime_type=info["mime"],
                file_size_bytes=info.get("size", 0),
            )
            candidate = AssetCandidate(
                candidate_id=f"commons-{title}",
                asset_type="video" if is_video else "image",
                source_url=info["url"],
                source_id=title,
                title=title.replace("File:", "").replace("_", " "),
                dimensions=dimensions,
                media_info=media_info,
                license=license_model,
                score=0.0,
                provenance=provenance,
                path_local=None,
                is_duplicate=False,
                tags=["wikimedia", "commons"],
            )
            candidates.append(candidate)
        return candidates

    def search(self, request: dict, max_results: int = 5, **kwargs) -> List[AssetCandidate]:
        global _CIRCUIT_BROKEN_UNTIL, _SEARCH_CACHE
        query = ""
        if isinstance(request, dict):
            query = request.get("query") or request.get("q") or ""
        if not query.strip():
            return []
        if time.time() < _CIRCUIT_BROKEN_UNTIL:
            return []

        cache_key = f"{query.strip().lower()}:{max_results}"
        if cache_key in _SEARCH_CACHE:
            ts, cands = _SEARCH_CACHE[cache_key]
            if time.time() - ts < _SEARCH_CACHE_TTL_SEC:
                return [c.model_copy(deep=True) for c in cands]

        try:
            results = self._search_files(query.strip(), max_results)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                _CIRCUIT_BROKEN_UNTIL = time.time() + 60.0
            return []
        except Exception:
            return []

        candidates = self.parse_results(results)
        if candidates:
            _SEARCH_CACHE[cache_key] = (time.time(), [c.model_copy(deep=True) for c in candidates])
        return candidates

    def select(self, candidates: List[AssetCandidate], criteria: Optional[dict] = None) -> AssetSelection:
        from autopilot.core.asset_scoring import score_candidates
        from autopilot.core.rights_gate import evaluate_rights_gate

        if not candidates:
            return AssetSelection(
                selection_id="sel-empty", selected_candidates=[], selected_id=None,
                status="rejected", reason="No candidate assets available from Wikimedia Commons",
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
        if not candidate.source_url:
            raise ValueError(f"Candidate {candidate.candidate_id} missing download source_url")
        return safe_download_media(candidate.source_url, out_path)

    def normalize(self, artifact_path: str, out_path: str, **kwargs) -> str:
        from autopilot.core.asset_normalizer import normalize_asset
        return normalize_asset(artifact_path, out_path, **kwargs)
