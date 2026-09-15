"""Deterministic Mock Trend Provider — Milestone 9.
Generates structured trend signals for offline testing without external API spend or scraping.
All signals carry explicit provenance and synthetic test indicators.
"""
from __future__ import annotations
import hashlib
from datetime import datetime, timezone
from typing import List, Optional

from autopilot.core.contracts import TrendSignal, ProvenanceRecord
from autopilot.providers.contracts import (
    TrendProvider,
    ProviderHealth,
    CapabilityMetadata,
    CostUsageMetadata,
    ProviderErrorType,
    REGISTRY,
)


MOCK_TREND_FIXTURES = [
    {
        "topic": "Quantum Computing Post-Quantum Cryptography Breakthrough",
        "category": "technology",
        "freshness": 0.95,
        "relevance": 0.90,
        "evidence": "NIST announces standardization of lattice-based encryption algorithms to secure global banking against quantum decryption.",
        "url": "https://example.org/trends/quantum-cryptography-2026",
    },
    {
        "topic": "Neural Interface Non-Invasive Brain Wave Communication",
        "category": "technology",
        "freshness": 0.88,
        "relevance": 0.85,
        "evidence": "Clinical trials demonstrate high-accuracy speech decoding using acoustic-optical headbands without surgical implants.",
        "url": "https://example.org/trends/neural-interfaces-2026",
    },
    {
        "topic": "Nuclear Fusion Steady-State High-Confinement Milestone",
        "category": "science",
        "freshness": 0.92,
        "relevance": 0.88,
        "evidence": "Magnetic confinement tokamak sustains net energy gain equilibrium for over 1000 continuous seconds.",
        "url": "https://example.org/trends/fusion-milestone-2026",
    },
    {
        "topic": "Deep Ocean Mariana Trench Microbial Biosphere Discovery",
        "category": "science",
        "freshness": 0.82,
        "relevance": 0.80,
        "evidence": "Autonomous submersibles map novel sulfur-metabolizing extremophiles inhabiting hadal volcanic vents.",
        "url": "https://example.org/trends/ocean-extremophiles-2026",
    },
    {
        "topic": "Bronze Age Trade Route Archaeological Satellite Mapping",
        "category": "history",
        "freshness": 0.78,
        "relevance": 0.75,
        "evidence": "LiDAR and multi-spectral imaging reveal uncharted commercial trade corridors connecting ancient Anatolia and Mesopotamia.",
        "url": "https://example.org/trends/bronze-age-trade-2026",
    },
    {
        "topic": "Antikythera Mechanism Astronomical Gear Calculation Decoded",
        "category": "history",
        "freshness": 0.72,
        "relevance": 0.70,
        "evidence": "Computed micro-tomography resolves previously illegible gear teeth confirming 354-day lunar calendar synchrony.",
        "url": "https://example.org/trends/antikythera-mechanism-2026",
    },
    {
        "topic": "Global Sovereign Debt Restructuring And Central Bank Reserves",
        "category": "finance",
        "freshness": 0.85,
        "relevance": 0.80,
        "evidence": "International settlement banks report multi-currency reserve shifts and algorithmic treasury hedging.",
        "url": "https://example.org/trends/sovereign-debt-2026",
    },
    {
        "topic": "Solid-State Battery Production Yield Breakthrough",
        "category": "technology",
        "freshness": 0.90,
        "relevance": 0.92,
        "evidence": "Automotive battery pilot lines report ceramic separator defects drop below 0.01% at scale.",
        "url": "https://example.org/trends/solid-state-batteries-2026",
    },
]


class MockTrendProvider:
    provider_name: str = "mock_trend"
    capability: CapabilityMetadata = CapabilityMetadata(
        max_resolution="N/A",
        supports_9_16=True,
        local_only=True,
        license_note="Synthetic deterministic trend signals for offline testing",
    )
    error_type: ProviderErrorType = ProviderErrorType.NOT_AVAILABLE
    cost_meta: CostUsageMetadata = CostUsageMetadata(
        estimated_usd=0.0,
        provider_type="mock",
    )

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            healthy=True,
            provider_name=self.provider_name,
            error="",
            details={"mode": "synthetic_mock_trends", "status": "AVAILABLE"},
        )

    def discover_trends(
        self,
        category: Optional[str] = None,
        limit: int = 10,
        **kwargs,
    ) -> List[TrendSignal]:
        """Deterministically generates mock trend signals filtered by optional category."""
        fixtures = MOCK_TREND_FIXTURES
        if category and category.lower() != "all":
            cat_clean = category.lower().strip()
            fixtures = [f for f in fixtures if f["category"].lower() == cat_clean]

        now_iso = datetime.now(timezone.utc).isoformat()
        signals: List[TrendSignal] = []

        for item in fixtures[:limit]:
            topic = item["topic"]
            cat = item["category"]
            sig_hash = hashlib.sha256(f"{topic}:{cat}".encode("utf-8")).hexdigest()[:10]
            sig_id = f"trend-{sig_hash}"

            sig = TrendSignal(
                signal_id=sig_id,
                topic=topic,
                source=self.provider_name,
                source_url=item["url"],
                detected_at=now_iso,
                freshness_score=float(item["freshness"]),
                relevance_score=float(item["relevance"]),
                category=cat,
                confidence=0.85,
                evidence_text=item["evidence"],
                provenance=ProvenanceRecord(
                    provider=self.provider_name,
                    source_ids=[item["url"]],
                    deterministic_idempotency_key=sig_id,
                ),
            )
            signals.append(sig)

        return signals


# Register mock provider
REGISTRY.register(MockTrendProvider())
