"""Phase 2 tests for Crawl4AI provider and Research Coordinator integration.
Covers:
- Crawl4AI provider contract
- Research Coordinator routing (Wikipedia-first, Crawl4AI-only, Combined)
- Research evidence normalization and provenance preservation
- Evidence deduplication and quality filtering (rejecting empty/inaccessible/synthetic content)
"""
import pytest
from unittest.mock import patch, MagicMock
from autopilot.providers.crawl4ai_provider import Crawl4AIProvider
from autopilot.core.research_coordinator import ResearchCoordinator
from autopilot.core.contracts import (
    ResearchEvidenceRecord,
    ResearchBundle,
    ResearchProviderProtocol,
)


def test_crawl4ai_provider_contract():
    """Verify Crawl4AIProvider adheres to ResearchProviderProtocol and metadata."""
    provider = Crawl4AIProvider()
    assert isinstance(provider, ResearchProviderProtocol)
    assert provider.provider_name == "crawl4ai"
    assert provider.tier == 2
    health = provider.health()
    assert health.healthy is True
    assert "crawl4ai_version" in health.details
    assert health.details["license"] == "Apache 2.0"


def test_crawl4ai_crawl_url_normalization():
    """Verify Crawl4AIProvider.crawl_url returns normalized evidence records."""
    provider = Crawl4AIProvider()

    mock_record = ResearchEvidenceRecord(
        evidence_id="ev-c4a-test",
        source_id="src-c4a-test",
        title="AI Breakthroughs 2026",
        url="https://techcrunch.com/2026/09/ai-breakthroughs",
        publisher="techcrunch.com",
        excerpt="Artificial Intelligence is transforming computing across multiple sectors.",
        provider="crawl4ai",
        confidence_score=0.9,
        provenance_metadata={"engine": "crawl4ai"},
    )

    with patch.object(provider, "_run_async_crawl", return_value=mock_record):
        record = provider.crawl_url("https://techcrunch.com/2026/09/ai-breakthroughs", topic="Artificial Intelligence")

    assert record is not None
    assert record.title == "AI Breakthroughs 2026"
    assert record.publisher == "techcrunch.com"
    assert "Artificial Intelligence is transforming computing" in record.excerpt
    assert record.provider == "crawl4ai"
    assert record.confidence_score >= 0.8
    assert record.provenance_metadata["engine"] == "crawl4ai"


def test_crawl4ai_crawl_url_failure_returns_none():
    """Failed page crawls or exceptions return None gracefully without crashing."""
    provider = Crawl4AIProvider()
    with patch.object(provider, "_run_async_crawl", side_effect=RuntimeError("Browser connection timeout")):
        record = provider.crawl_url("https://unreachable-domain-xyz.com/news", topic="AI")
    assert record is None


def test_research_coordinator_routing_explicit_url():
    """When query is a URL, ResearchCoordinator routes to Crawl4AI."""
    mock_crawl = MagicMock(spec=Crawl4AIProvider)
    mock_crawl.provider_name = "crawl4ai"
    mock_crawl.fetch_page.return_value = ResearchEvidenceRecord(
        evidence_id="ev-c4a-01",
        source_id="src-c4a-01",
        title="URL Target Article",
        url="https://nature.com/articles/s41586-quantum",
        publisher="nature.com",
        excerpt="Quantum supremacy demonstrated in optical circuits with high fidelity.",
        provider="crawl4ai",
        confidence_score=0.95,
    )

    coord = ResearchCoordinator(
        crawl4ai_provider=mock_crawl,
        wikipedia_provider=None,
    )

    bundle = coord.coordinate_research(topic="https://nature.com/articles/s41586-quantum")
    assert bundle.routing_strategy == "explicit_url"
    assert len(bundle.sources) == 1
    assert bundle.sources[0].url == "https://nature.com/articles/s41586-quantum"
    mock_crawl.fetch_page.assert_called_once()


def test_research_coordinator_deduplication():
    """Verify that duplicate evidence URLs/titles are deduplicated."""
    coord = ResearchCoordinator()

    records = [
        ResearchEvidenceRecord(
            evidence_id="ev-1",
            source_id="src-1",
            title="Quantum Computing",
            url="https://en.wikipedia.org/wiki/Quantum_computing",
            publisher="wikipedia.org",
            excerpt="Quantum computing is a rapidly-emerging technology.",
            provider="wikipedia",
        ),
        ResearchEvidenceRecord(
            evidence_id="ev-2",
            source_id="src-2",
            title="Quantum Computing",
            url="https://en.wikipedia.org/wiki/Quantum_computing#Overview",  # anchor variation
            publisher="wikipedia.org",
            excerpt="Duplicate snippet from same page.",
            provider="wikipedia",
        ),
        ResearchEvidenceRecord(
            evidence_id="ev-3",
            source_id="src-3",
            title="Superconducting Qubits",
            url="https://arxiv.org/abs/2609.12345",
            publisher="arxiv.org",
            excerpt="Novel architecture for superconducting qubits.",
            provider="crawl4ai",
        ),
    ]

    deduped = coord._deduplicate_evidence(records)
    assert len(deduped) == 2
    urls = [d.url for d in deduped]
    assert "https://en.wikipedia.org/wiki/Quantum_computing" in urls
    assert "https://arxiv.org/abs/2609.12345" in urls


def test_research_coordinator_quality_filtering_rejects_empty_and_synthetic():
    """Verify that empty, very short, or obviously synthetic snippets are rejected."""
    coord = ResearchCoordinator()

    valid_record = ResearchEvidenceRecord(
        evidence_id="ev-valid",
        source_id="src-valid",
        title="Valid Scientific Source",
        url="https://science.org/breakthrough",
        publisher="science.org",
        excerpt="Detailed empirical evidence describing reproducible experimental outcomes across multiple laboratory trials.",
        provider="crawl4ai",
    )

    empty_record = ResearchEvidenceRecord(
        evidence_id="ev-empty",
        source_id="src-empty",
        title="Empty Page",
        url="https://empty.org",
        publisher="empty.org",
        excerpt="",
        provider="crawl4ai",
    )

    synthetic_record = ResearchEvidenceRecord(
        evidence_id="ev-synth",
        source_id="src-synth",
        title="Fake Story",
        url="https://example.com/mock",
        publisher="example.com",
        excerpt="This is a deterministic demo content synthetic fixture generated for mock test.",
        provider="mock",
    )

    assert coord._is_valid_evidence(valid_record, allow_mock=False) is True
    assert coord._is_valid_evidence(empty_record, allow_mock=False) is False
    assert coord._is_valid_evidence(synthetic_record, allow_mock=False) is False
    # Mock allowed only if allow_mock=True
    assert coord._is_valid_evidence(synthetic_record, allow_mock=True) is True
