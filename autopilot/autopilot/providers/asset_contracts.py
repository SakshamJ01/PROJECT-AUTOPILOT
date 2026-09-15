"""Asset provider contract — Phase 3 / M3."""
from __future__ import annotations
from typing import Protocol, runtime_checkable
from autopilot.providers.contracts import ProviderHealth, CapabilityMetadata, CostUsageMetadata, ProviderErrorType
from autopilot.core.contracts import AssetCandidate, AssetSelection, AssetArtifact

@runtime_checkable
class AssetProvider(Protocol):
    provider_name: str
    capability: CapabilityMetadata
    error_type: ProviderErrorType
    cost_meta: CostUsageMetadata

    def health_check(self) -> ProviderHealth: ...
    def search(self, request: dict, max_results: int = 5, **kwargs) -> list[AssetCandidate]: ...
    def select(self, candidates: list[AssetCandidate], criteria: dict = None) -> AssetSelection: ...
    def download(self, candidate: AssetCandidate, out_path: str, **kwargs) -> str: ...
    def normalize(self, artifact_path: str, out_path: str, **kwargs) -> str: ...
