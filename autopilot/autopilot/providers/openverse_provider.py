"""Openverse Asset Provider — Real Openverse API v1 implementation.
Translates Openverse API responses into canonical AssetCandidate models.
Safe, rights-aware, deterministic, and gracefully handles network errors.
"""
from __future__ import annotations
import json
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


def map_openverse_license(license_code: Optional[str], license_version: Optional[str] = None, license_url: Optional[str] = None, creator: Optional[str] = None, source_url: Optional[str] = None) -> AssetLicense:
    """Map Openverse license code to explicit AssetLicense with verification status."""
    code = (license_code or "").lower().strip()
    lic_name = f"CC-{code.upper()}" if code and not code.startswith("cc") and code not in ("pdm", "publicdomain") else (code.upper() or "UNKNOWN")
    if code == "cc0":
        lic_name = "CC0-1.0"
    elif code == "pdm":
        lic_name = "PDM-1.0 (Public Domain Mark)"

    if code in ("cc0", "pdm", "publicdomain"):
        return AssetLicense(
            license_name=lic_name,
            license_url=license_url or "https://creativecommons.org/publicdomain/zero/1.0/",
            source_url=source_url or "",
            creator=creator,
            attribution_required=False,
            commercial_use=True,
            derivative_use=True,
            rights_status="VERIFIED",
        )
    elif code in ("by", "by-sa"):
        ver = f"-{license_version}" if license_version else ""
        return AssetLicense(
            license_name=f"CC {code.upper()}{ver}",
            license_url=license_url or f"https://creativecommons.org/licenses/{code}/{license_version or '4.0'}/",
            source_url=source_url or "",
            creator=creator,
            attribution_required=True,
            commercial_use=True,
            derivative_use=True,
            rights_status="VERIFIED",
        )
    elif "nc" in code or "nd" in code:
        # Non-commercial or No-derivatives: reject by default for commercial-safe content factory
        return AssetLicense(
            license_name=lic_name,
            license_url=license_url or "",
            source_url=source_url or "",
            creator=creator,
            attribution_required=True,
            commercial_use=False if "nc" in code else True,
            derivative_use=False if "nd" in code else True,
            rights_status="REJECTED",
        )
    elif code:
        return AssetLicense(
            license_name=lic_name,
            license_url=license_url or "",
            source_url=source_url or "",
            creator=creator,
            attribution_required=True,
            commercial_use=None,
            derivative_use=None,
            rights_status="PARTIALLY_VERIFIED",
        )
    else:
        return AssetLicense(
            license_name="UNKNOWN",
            license_url="",
            source_url=source_url or "",
            creator=creator,
            attribution_required=False,
            commercial_use=False,
            derivative_use=False,
            rights_status="UNKNOWN",
        )


class OpenverseAssetProvider(AssetProvider):
    provider_name = "openverse"
    capability = CapabilityMetadata(
        max_resolution="4k",
        supports_9_16=True,
        local_only=False,
        license_note="Openverse API — CC / Public Domain media discovery (License accuracy must be verified)",
    )
    error_type = ProviderErrorType.NOT_AVAILABLE
    cost_meta = CostUsageMetadata(estimated_usd=0.0, provider_type="openverse_api")

    def __init__(self, base_url: Optional[str] = None, timeout: Optional[float] = None):
        self.base_url = (base_url or CONFIG.openverse_base_url).rstrip("/") + "/"
        self.timeout = timeout or CONFIG.openverse_timeout

    def health_check(self) -> ProviderHealth:
        """Check Openverse API health without throwing unhandled exceptions."""
        if not CONFIG.openverse_enabled:
            return ProviderHealth(
                healthy=False,
                provider_name=self.provider_name,
                error="Openverse provider is disabled in configuration",
                details={"enabled": False},
            )
        try:
            url = f"{self.base_url}images/?q=test&page_size=1"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "PROJECT-AUTOPILOT/1.0 (local-content-factory; health-check)"},
            )
            with urllib.request.urlopen(req, timeout=min(self.timeout, 5.0)) as resp:
                status_ok = resp.status == 200
                return ProviderHealth(
                    healthy=status_ok,
                    provider_name=self.provider_name,
                    error="" if status_ok else f"HTTP {resp.status}",
                    details={"status_code": resp.status, "base_url": self.base_url},
                )
        except Exception as exc:
            return ProviderHealth(
                healthy=False,
                provider_name=self.provider_name,
                error=f"Openverse unreachable: {exc}",
                details={"base_url": self.base_url, "exception": str(exc)},
            )

    def parse_api_response(self, raw_json: str | dict) -> List[AssetCandidate]:
        """Convert Openverse API response into canonical AssetCandidate models."""
        if isinstance(raw_json, str):
            data = json.loads(raw_json)
        else:
            data = raw_json

        results = data.get("results", [])
        candidates: List[AssetCandidate] = []
        for item in results:
            media_url = item.get("url")
            if not media_url:
                continue

            openverse_id = str(item.get("id") or "")
            source_landing = item.get("foreign_landing_url") or media_url
            creator = item.get("creator")
            lic_code = item.get("license")
            lic_ver = item.get("license_version")
            lic_url = item.get("license_url")
            filetype = item.get("filetype") or "jpeg"
            filesize = item.get("filesize") or 0
            width = item.get("width")
            height = item.get("height")

            license_model = map_openverse_license(
                license_code=lic_code,
                license_version=lic_ver,
                license_url=lic_url,
                creator=creator,
                source_url=source_landing,
            )

            provenance = AssetProvenance(
                provider=self.provider_name,
                source_url=source_landing,
                source_id=openverse_id,
                retrieval_timestamp=datetime.now(timezone.utc).isoformat(),
            )

            dimensions = AssetDimensions(
                width=width,
                height=height,
            )

            media_info = AssetMediaInfo(
                mime_type=f"image/{filetype.lower()}",
                file_size_bytes=filesize if isinstance(filesize, int) else 0,
            )

            raw_tags = item.get("tags") or []
            tags_list = [t.get("name") if isinstance(t, dict) else str(t) for t in raw_tags if t]

            candidate = AssetCandidate(
                candidate_id=f"openverse-{openverse_id}",
                asset_type="image",
                source_url=media_url,  # direct download URL
                source_id=openverse_id,
                title=item.get("title") or "Openverse Media Item",
                dimensions=dimensions,
                media_info=media_info,
                license=license_model,
                score=0.0,
                provenance=provenance,
                path_local=None,
                is_duplicate=False,
                tags=tags_list,
            )
            candidates.append(candidate)
        return candidates

    def search(self, request: dict | Any, max_results: int = 5, **kwargs) -> List[AssetCandidate]:
        """Query Openverse API for images matching query and criteria."""
        query = ""
        aspect_ratio = None
        if isinstance(request, dict):
            query = request.get("query") or request.get("q") or ""
            aspect_ratio = request.get("aspect_ratio")
        else:
            query = getattr(request, "query", "")
            aspect_ratio = getattr(request, "aspect_ratio", None)

        if not query or not query.strip():
            return []

        # Build query parameters
        params: Dict[str, Any] = {
            "q": query.strip(),
            "page_size": max(1, min(max_results, 20)),
            "license_type": "commercial,modification",
        }

        # Map requested aspect ratio to Openverse tall/square/wide
        if aspect_ratio:
            ar_str = str(aspect_ratio).lower()
            if ar_str in ("9:16", "portrait", "tall", "vertical"):
                params["aspect_ratio"] = "tall"
            elif ar_str in ("16:9", "landscape", "wide", "horizontal"):
                params["aspect_ratio"] = "wide"
            elif ar_str in ("1:1", "square"):
                params["aspect_ratio"] = "square"

        def _do_request(p: dict) -> List[AssetCandidate]:
            u = f"{self.base_url}images/?{urllib.parse.urlencode(p)}"
            r = urllib.request.Request(
                u,
                headers={
                    "User-Agent": "PROJECT-AUTOPILOT/1.0 (local-content-factory; rights-aware-asset-search)",
                    "Accept": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(r, timeout=self.timeout) as response:
                    content = response.read().decode("utf-8")
                    return self.parse_api_response(content)
            except urllib.error.HTTPError as exc:
                raise RuntimeError(f"Openverse API error HTTP {exc.code}: {exc.reason}") from exc
            except urllib.error.URLError as exc:
                raise RuntimeError(f"Openverse network connection failed: {exc.reason}") from exc

        candidates = _do_request(params)
        if not candidates and "aspect_ratio" in params:
            params_no_ar = dict(params)
            params_no_ar.pop("aspect_ratio", None)
            candidates = _do_request(params_no_ar)

        if not candidates:
            # Fallback to last/first substantive word from query
            words = [w.strip() for w in query.strip().split() if len(w.strip()) > 2]
            if len(words) > 1:
                substantive_q = words[-1] if len(words[-1]) >= 4 else words[0]
                candidates = _do_request({"q": substantive_q, "page_size": max(1, min(max_results, 20)), "license_type": "commercial,modification"})

        return candidates


    def select(self, candidates: List[AssetCandidate], criteria: Optional[dict] = None) -> AssetSelection:
        """Select highest scored rights-cleared candidate."""
        from autopilot.core.asset_scoring import score_candidates
        from autopilot.core.rights_gate import evaluate_rights_gate

        if not candidates:
            return AssetSelection(
                selection_id="sel-empty",
                selected_candidates=[],
                selected_id=None,
                status="rejected",
                reason="No candidate assets available from search",
            )

        scored = score_candidates(candidates, criteria or {})
        selected_cand = None
        for cand in scored:
            gate_res = evaluate_rights_gate(cand.license)
            if gate_res.allowed:
                selected_cand = cand
                break

        if selected_cand:
            return AssetSelection(
                selection_id=f"sel-{selected_cand.candidate_id}",
                selected_candidates=scored,
                selected_id=selected_cand.candidate_id,
                status="selected",
                reason=f"Selected candidate {selected_cand.candidate_id} with score {selected_cand.score:.2f} and license {selected_cand.license.license_name}",
            )
        else:
            return AssetSelection(
                selection_id="sel-none-cleared",
                selected_candidates=scored,
                selected_id=None,
                status="rejected",
                reason="No candidate passed the rights and license gate",
            )

    def download(self, candidate: AssetCandidate, out_path: str, **kwargs) -> str:
        """Safely download media file using asset cache downloader."""
        from autopilot.core.asset_cache import safe_download_media
        media_url = candidate.source_url
        if not media_url:
            raise ValueError(f"Candidate {candidate.candidate_id} missing download source_url")
        return safe_download_media(media_url, out_path, expected_hash=candidate.provenance.original_hash_sha256)

    def normalize(self, artifact_path: str, out_path: str, **kwargs) -> str:
        """Normalize downloaded media into 9:16 vertical standard dimensions."""
        from autopilot.core.asset_normalizer import normalize_asset
        return normalize_asset(artifact_path, out_path, **kwargs)
