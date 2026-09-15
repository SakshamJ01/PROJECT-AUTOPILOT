"""Typed provider contracts — Phase 0 skeleton.
Every external capability is behind a provider interface.
Implementations are NOT required yet; only contracts.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable
from pydantic import BaseModel, Field
from enum import Enum


class ProviderErrorType(str, Enum):
    UNCONFIGURED = "unconfigured"
    NOT_AVAILABLE = "not_available"
    INVALID_INPUT = "invalid_input"
    TIMEOUT = "timeout"
    RENDER_FAILED = "render_failed"
    QA_FAILED = "qa_failed"
    PUBLISH_FAILED = "publish_failed"
    ANALYTICS_FAILED = "analytics_failed"
    TREND_FAILED = "trend_failed"


class CapabilityMetadata(BaseModel):
    max_resolution: str = "1080p"
    supports_9_16: bool = True
    local_only: bool = True
    license_note: str = ""


class CostUsageMetadata(BaseModel):
    estimated_usd: float = 0.0
    provider_type: str = "local"
    tokens_in: int = 0
    tokens_out: int = 0


class ProviderHealth(BaseModel):
    healthy: bool = False
    provider_name: str
    error: str = ""
    details: dict = Field(default_factory=dict)


@runtime_checkable
class LLMProvider(Protocol):
    provider_name: str
    capability: CapabilityMetadata
    error_type: ProviderErrorType
    cost_meta: CostUsageMetadata

    def health_check(self) -> ProviderHealth: ...
    def generate_script(self, topic: str, **kwargs) -> dict: ...


@runtime_checkable
class TTSProvider(Protocol):
    provider_name: str
    capability: CapabilityMetadata
    error_type: ProviderErrorType
    cost_meta: CostUsageMetadata

    def health_check(self) -> ProviderHealth: ...
    def synthesize(self, text: str, out_path: str, **kwargs) -> str: ...


@runtime_checkable
class ASRProvider(Protocol):
    provider_name: str
    capability: CapabilityMetadata
    error_type: ProviderErrorType
    cost_meta: CostUsageMetadata

    def health_check(self) -> ProviderHealth: ...
    def transcribe(self, audio_path: str, **kwargs) -> dict: ...


@runtime_checkable
class AssetProvider(Protocol):
    provider_name: str
    capability: CapabilityMetadata
    error_type: ProviderErrorType
    cost_meta: CostUsageMetadata

    def health_check(self) -> ProviderHealth: ...
    def search_assets(self, query: str, **kwargs) -> list[dict]: ...
    def download_asset(self, asset_ref: str, out_path: str, **kwargs) -> str: ...


@runtime_checkable
class PublisherProvider(Protocol):
    provider_name: str
    capability: CapabilityMetadata
    error_type: ProviderErrorType
    cost_meta: CostUsageMetadata

    def health_check(self) -> ProviderHealth: ...
    def upload_video(self, video_path: str, metadata: dict, **kwargs) -> dict: ...
    def schedule(self, job_id: str, schedule_time: str, **kwargs) -> dict: ...


@runtime_checkable
class AnalyticsProvider(Protocol):
    provider_name: str
    capability: CapabilityMetadata
    error_type: ProviderErrorType
    cost_meta: CostUsageMetadata

    def health_check(self) -> ProviderHealth: ...
    def fetch_snapshot(self, remote_id: str, platform: str = "youtube", window: str = "lifetime", **kwargs) -> Any: ...


@runtime_checkable
class TrendProvider(Protocol):
    provider_name: str
    capability: CapabilityMetadata
    error_type: ProviderErrorType
    cost_meta: CostUsageMetadata

    def health_check(self) -> ProviderHealth: ...
    def discover_trends(self, category: Optional[str] = None, limit: int = 10, **kwargs) -> list[Any]: ...


# Registry for Phase 0 — providers register themselves; missing ones are graceful.
class ProviderRegistry:
    def __init__(self):
        self._providers: dict[str, object] = {}

    def register(self, provider: object) -> None:
        name = getattr(provider, "provider_name", None)
        if not name:
            raise ValueError("Provider missing provider_name")
        self._providers[name] = provider

    def get(self, name: str) -> object | None:
        return self._providers.get(name)

    def list_available(self) -> list[str]:
        return list(self._providers.keys())

    def health_all(self) -> dict[str, ProviderHealth]:
        result: dict[str, ProviderHealth] = {}
        for name, provider in self._providers.items():
            try:
                result[name] = provider.health_check()
            except Exception as exc:
                result[name] = ProviderHealth(
                    healthy=False,
                    provider_name=name,
                    error=str(exc),
                )
        return result


# Default global registry (Phase 0: empty until implementations added)
REGISTRY = ProviderRegistry()
