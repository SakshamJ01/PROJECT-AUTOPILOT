"""Search / Research provider contracts — Phase 2.
Clean interfaces; no mandatory external engine.
"""
from __future__ import annotations
from typing import Protocol, runtime_checkable
from pydantic import BaseModel, Field
from enum import Enum
from autopilot.providers.contracts import ProviderHealth, CapabilityMetadata, CostUsageMetadata, ProviderErrorType


class SearchProvider(Protocol):
    provider_name: str
    capability: CapabilityMetadata
    error_type: ProviderErrorType
    cost_meta: CostUsageMetadata

    def health_check(self) -> ProviderHealth: ...
    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]: ...


class WebResearchProvider(Protocol):
    provider_name: str
    capability: CapabilityMetadata
    error_type: ProviderErrorType
    cost_meta: CostUsageMetadata

    def health_check(self) -> ProviderHealth: ...
    def fetch_and_extract(self, url: str, timeout: float = 10.0, **kwargs) -> dict: ...
