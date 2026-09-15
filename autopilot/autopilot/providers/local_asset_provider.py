"""Local deterministic asset provider — Phase 3 / M3.
Uses fixtures/ directory (fixture_image.png, fixture_video.mp4) for offline tests.
Clearly marked as test/development provider.
"""
from __future__ import annotations
import hashlib
from pathlib import Path
from autopilot.providers.asset_contracts import AssetProvider
from autopilot.providers.contracts import ProviderHealth, CapabilityMetadata, CostUsageMetadata, ProviderErrorType
from autopilot.core.contracts import AssetCandidate, AssetSelection, AssetArtifact, AssetLicense, AssetProvenance

FIXTURES_DIR = Path(__file__).resolve().parent

class LocalAssetProvider(AssetProvider):
    provider_name = "local"
    capability = CapabilityMetadata(max_resolution="1080p", supports_9_16=True, local_only=True, license_note="Local fixture assets — synthetic/development only")
    error_type = ProviderErrorType.UNCONFIGURED
    cost_meta = CostUsageMetadata(estimated_usd=0.0, provider_type="local")

    def health_check(self) -> ProviderHealth:
        fixtures_ok = FIXTURES_DIR.exists() and any(FIXTURES_DIR.iterdir())
        return ProviderHealth(healthy=fixtures_ok, provider_name=self.provider_name, details={"fixtures_dir": str(FIXTURES_DIR), "mode": "local"})

    def _fixture_path(self, name: str) -> str:
        return str(FIXTURES_DIR / name)

    def search(self, request: dict, max_results: int = 5, **kwargs) -> list:
        # Return deterministic fixtures based on query keywords
        candidates = []
        q = (request.get("query") or "").lower()
        # Always include at least one image and one video fixture
        fixtures = [
            ("fixture_image.png", "image", "Public domain test image"),
            ("fixture_video.mp4", "video", "Public domain test video"),
        ]
        for fname, atype, title in fixtures:
            fp = self._fixture_path(fname)
            p = Path(fp)
            if p.exists():
                checksum = hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.exists() else ""
                candidates.append(AssetCandidate(
                    candidate_id=f"local-{fname}-{checksum}",
                    asset_type=atype,
                    source_url=str(p.resolve()),
                    source_id=fname,
                    title=title,
                    dimensions={"width": 720, "height": 1280} if atype == "video" else {"width": 640, "height": 360},
                    media_info={"mime_type": "image/png" if atype == "image" else "video/mp4", "file_size_bytes": p.stat().st_size},
                    license=AssetLicense(license_name="CC0-1.0", rights_status="VERIFIED", source_url=str(p.resolve()), license_url="https://creativecommons.org/publicdomain/zero/1.0/", commercial_use=True, derivative_use=True),
                    score=0.9,
                    provenance=AssetProvenance(provider=self.provider_name, source_id=fname, retrieval_timestamp="2026-09-11T12:00:00Z", original_hash_sha256=checksum),
                    path_local=str(p),
                    is_duplicate=False,
                ))
        return candidates[:max_results]

    def select(self, candidates: list, criteria: dict = None) -> AssetSelection:
        # Deterministic selection: pick first valid with known license
        selected = None
        for c in candidates:
            if c.license.rights_status in ("VERIFIED", "PARTIALLY_VERIFIED"):
                selected = c
                break
        return AssetSelection(
            selection_id=f"sel-{selected.candidate_id if selected else 'none'}",
            selected_candidates=candidates,
            selected_id=selected.candidate_id if selected else None,
            status="selected" if selected else "rejected",
            reason="Local fixture selected" if selected else "No verified fixture available",
        )

    def download(self, candidate: AssetCandidate, out_path: str, **kwargs) -> str:
        import shutil
        src = Path(candidate.path_local or self._fixture_path(candidate.source_id or "fixture_image.png"))
        if not src.exists():
            raise FileNotFoundError(f"Fixture not found: {src}")
        dest = Path(out_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dest))
        return str(dest)

    def normalize(self, artifact_path: str, out_path: str, **kwargs) -> str:
        from autopilot.core.asset_normalizer import normalize_asset
        return normalize_asset(artifact_path, out_path, **kwargs)
