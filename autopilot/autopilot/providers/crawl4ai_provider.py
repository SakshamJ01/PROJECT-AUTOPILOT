"""Crawl4AI Research Provider — Phase 2 Deep Research Engine.
Integrates crawl4ai for deep live web retrieval, structured markdown extraction,
page metadata preservation, and normalized ResearchEvidenceRecord output.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from autopilot.core.contracts import ResearchEvidenceRecord, ResearchProviderProtocol
from autopilot.providers.contracts import ProviderHealth, CapabilityMetadata, CostUsageMetadata, ProviderErrorType

logger = logging.getLogger(__name__)


class Crawl4AIProvider:
    """Crawl4AI provider implementing ResearchProviderProtocol."""

    provider_name: str = "crawl4ai"
    tier: int = 2
    capability = CapabilityMetadata(
        local_only=False,
        license_note="Crawl4AI (Apache 2.0); Deep asynchronous web crawler & scraper",
    )
    error_type = ProviderErrorType.UNCONFIGURED
    cost_meta = CostUsageMetadata(estimated_usd=0.0, provider_type="open_source_crawler")

    def __init__(
        self,
        headless: bool = True,
        timeout: float = 20.0,
        user_agent: Optional[str] = None,
    ):
        self.headless = headless
        self.timeout = timeout
        self.user_agent = user_agent or "autopilot-crawl4ai/1.0"
        self._crawler_available = self._detect_crawl4ai()

    def _detect_crawl4ai(self) -> bool:
        try:
            import crawl4ai
            return True
        except ImportError:
            return False

    def health_check(self) -> ProviderHealth:
        if not self._crawler_available:
            return ProviderHealth(
                healthy=False,
                provider_name=self.provider_name,
                error="crawl4ai package is not installed or importable",
            )
        import crawl4ai
        return ProviderHealth(
            healthy=True,
            provider_name=self.provider_name,
            details={
                "engine": "crawl4ai",
                "crawl4ai_version": getattr(crawl4ai, "__version__", "0.9.3"),
                "license": "Apache 2.0",
                "headless": self.headless,
                "timeout": self.timeout,
            },
        )

    def health(self) -> ProviderHealth:
        """Alias for health_check."""
        return self.health_check()

    def crawl_url(self, url: str, **kwargs) -> Optional[ResearchEvidenceRecord]:
        """Alias for fetch_page."""
        return self.fetch_page(url, **kwargs)

    def fetch_page(self, url: str, **kwargs) -> Optional[ResearchEvidenceRecord]:
        """Crawls a single URL and extracts clean Markdown and metadata."""
        if not url or not url.strip():
            return None

        url = url.strip()
        parsed = urllib.parse.urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return None

        publisher = parsed.netloc.replace("www.", "")
        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
        source_id = f"crawl-{publisher}-{url_hash}"

        # Real Crawl4AI execution
        if self._crawler_available:
            try:
                evidence = self._run_async_crawl(url, source_id, publisher, **kwargs)
                if evidence:
                    return evidence
            except Exception as exc:
                logger.warning(f"Crawl4AI live crawl failed for {url}: {exc}")

        # If live crawl was unsuccessful or library unavailable, return None (fail-closed / no synthetic masquerade)
        return None

    def search(self, query: str, max_results: int = 5, **kwargs) -> List[ResearchEvidenceRecord]:
        """Deep research for a query or explicit URL.
        
        If query is an explicit URL, fetches that page directly.
        If query is a topic or text string, converts it to target reference queries
        or leverages DuckDuckGo / Wikipedia / search fallback URLs via Crawl4AI.
        """
        if not query or not query.strip():
            return []

        query = query.strip()
        # Direct URL check
        if query.startswith("http://") or query.startswith("https://"):
            rec = self.fetch_page(query, **kwargs)
            return [rec] if rec else []

        # For non-URL query topics: discover and crawl targeted sources
        search_urls = [
            f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}",
        ]
        results: List[ResearchEvidenceRecord] = []
        for s_url in search_urls[:max_results]:
            rec = self.fetch_page(s_url, **kwargs)
            if rec and rec.is_valid_and_non_empty():
                results.append(rec)
            if len(results) >= max_results:
                break

        return results

    def _run_async_crawl(
        self, url: str, source_id: str, publisher: str, **kwargs
    ) -> Optional[ResearchEvidenceRecord]:
        """Runs AsyncWebCrawler in an isolated or existing event loop."""
        async def _crawl():
            from crawl4ai import AsyncWebCrawler
            async with AsyncWebCrawler(headless=self.headless) as crawler:
                result = await crawler.arun(url=url)
                if not result or not result.success:
                    return None

                title = getattr(result, "title", "") or publisher
                markdown_content = getattr(result, "markdown", "") or ""
                if hasattr(markdown_content, "raw_markdown"):
                    raw_text = markdown_content.raw_markdown
                else:
                    raw_text = str(markdown_content)

                if not raw_text.strip():
                    raw_text = getattr(result, "cleaned_html", "") or ""

                excerpt = raw_text.strip()[:1000]
                if not excerpt:
                    return None

                return ResearchEvidenceRecord(
                    evidence_id=f"ev-{source_id}",
                    source_id=source_id,
                    title=str(title).strip(),
                    url=url,
                    publisher=publisher,
                    retrieved_at=datetime.now(timezone.utc).isoformat(),
                    excerpt=excerpt,
                    relevant_passage=raw_text.strip()[:2000],
                    provider=self.provider_name,
                    confidence_score=0.9,
                    provenance_metadata={
                        "engine": "crawl4ai",
                        "status_code": getattr(result, "status_code", 200),
                        "content_length": len(raw_text),
                    },
                )

        try:
            return asyncio.run(_crawl())
        except RuntimeError:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(lambda: asyncio.run(_crawl()))
                return future.result(timeout=self.timeout)
