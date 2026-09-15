"""Mock / Local Research Provider — Phase 2.
Deterministic synthetic fixtures; clearly marked test/demo content.
No external APIs, no scraping, no paid services.
"""
from __future__ import annotations
from autopilot.providers.research_contracts import SearchProvider, WebResearchProvider
from autopilot.providers.contracts import ProviderHealth, CapabilityMetadata, CostUsageMetadata, ProviderErrorType
from autopilot.core.contracts import ResearchSource, ResearchEvidence, ResearchResult, ResearchReport, ProvenanceRecord


class MockSearchProvider(SearchProvider):
    provider_name = "mock_search"
    capability = CapabilityMetadata(
        max_resolution="1080p", supports_9_16=True, local_only=True,
        license_note="Deterministic mock provider for Phase 2 development",
    )
    error_type = ProviderErrorType.UNCONFIGURED
    cost_meta = CostUsageMetadata(estimated_usd=0.0, provider_type="local")

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(healthy=True, provider_name=self.provider_name, error="")

    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
        # Deterministic fixtures based on normalized query
        sources = [
            {
                "source_id": f"mock-src-001-{query[:10]}",
                "url": f"https://example.org/article/{query.replace(' ', '-')[:20]}",
                "title": f"Research note on {query}",
                "publisher": "Mock Open Source",
                "published_at": "2026-01-01",
                "accessed_at": "2026-09-11",
                "source_type": "article",
                "credibility": "medium",
                "notes": "Synthetic fixture for automated testing only.",
            },
            {
                "source_id": f"mock-src-002-{query[:10]}",
                "url": f"https://example.org/data/{query.replace(' ', '-')[:20]}",
                "title": f"Data overview: {query}",
                "publisher": "Mock Public Data",
                "published_at": "2025-06-15",
                "accessed_at": "2026-09-11",
                "source_type": "database",
                "credibility": "high",
                "notes": "Synthetic fixture — not real-world citation.",
            },
        ][:max_results]
        return sources


class MockWebResearchProvider(WebResearchProvider):
    provider_name = "mock_web"
    capability = CapabilityMetadata(
        max_resolution="1080p", supports_9_16=True, local_only=True,
        license_note="Deterministic mock provider for Phase 2 development",
    )
    error_type = ProviderErrorType.UNCONFIGURED
    cost_meta = CostUsageMetadata(estimated_usd=0.0, provider_type="local")

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(healthy=True, provider_name=self.provider_name, error="")

    def fetch_and_extract(self, url: str, timeout: float = 10.0, **kwargs) -> dict:
        return {
            "url": url,
            "title": f"Mock extraction for {url[:40]}",
            "snippet": "Synthetic evidence snippet for testing pipeline validation.",
            "relevance_score": 0.7,
            "status": "fetched",
            "provenance": ProvenanceRecord(provider=self.provider_name),
        }
