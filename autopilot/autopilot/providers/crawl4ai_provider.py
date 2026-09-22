"""Crawl4AI Research Provider — Phase 2 Deep Research Engine.
Integrates crawl4ai for deep live web retrieval, structured markdown extraction,
page metadata preservation, and normalized ResearchEvidenceRecord output.
"""
from __future__ import annotations
import asyncio
import hashlib
import importlib.util
import json
import logging
import sys
import threading
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from autopilot.core.contracts import ResearchEvidenceRecord, ResearchProviderProtocol
from autopilot.providers.contracts import ProviderHealth, CapabilityMetadata, CostUsageMetadata, ProviderErrorType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Guarded Crawl4AI import.
#
# ``import crawl4ai`` can block indefinitely on some Windows setups while its
# numpy C-extension loads (observed live in the desktop bridge as a 40-minute
# RESEARCH-stage hang with zero DB progress). To guarantee that a Research job
# can NEVER hang on this provider, the import is performed inside a bounded
# daemon thread. If it does not finish within ``timeout`` we treat Crawl4AI as
# unavailable (fail-closed) and the caller proceeds without it. The daemon
# thread (if left blocked) cannot stall the pipeline because nothing waits on
# it again, and the result is cached for the process lifetime.
# ---------------------------------------------------------------------------
_CRAWL4AI_IMPORT_STATE: Optional[bool] = None


def _crawl4ai_importable(timeout: float = 30.0) -> bool:
    """Bounded, cached availability probe for the ``crawl4ai`` package.

    Never raises and never blocks the caller longer than ``timeout`` seconds.
    """
    global _CRAWL4AI_IMPORT_STATE
    if _CRAWL4AI_IMPORT_STATE is not None:
        return _CRAWL4AI_IMPORT_STATE
    if "crawl4ai" in sys.modules:
        _CRAWL4AI_IMPORT_STATE = True
        return True
    if importlib.util.find_spec("crawl4ai") is None:
        _CRAWL4AI_IMPORT_STATE = False
        return False

    state: Dict[str, bool] = {"ok": False}

    def _load() -> None:
        try:
            import crawl4ai  # noqa: F401
            state["ok"] = True
        except Exception:  # noqa: BLE001 — availability probe, fail closed
            state["ok"] = False

    probe = threading.Thread(target=_load, name="crawl4ai_import_probe", daemon=True)
    probe.start()
    probe.join(timeout)
    _CRAWL4AI_IMPORT_STATE = bool(state.get("ok"))
    return _CRAWL4AI_IMPORT_STATE


def create_crawl4ai_provider(*args, **kwargs) -> Optional["Crawl4AIProvider"]:
    """Lazy, guarded factory.

    Returns a :class:`Crawl4AIProvider` when Crawl4AI is importable, else
    ``None``. Never raises and never hangs on the import.
    """
    try:
        if not _crawl4ai_importable():
            return None
        return Crawl4AIProvider(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 — fail closed, structured
        logger.warning("crawl4ai provider unavailable: %s", exc)
        return None


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
        # Bounded, cached probe: constructors must never block on the import.
        return _crawl4ai_importable()

    def health_check(self) -> ProviderHealth:
        if not self._crawler_available:
            return ProviderHealth(
                healthy=False,
                provider_name=self.provider_name,
                error="crawl4ai package is not installed or importable",
            )
        try:
            import crawl4ai
        except Exception as exc:  # noqa: BLE001 — probe says available; surface import error cleanly
            return ProviderHealth(
                healthy=False,
                provider_name=self.provider_name,
                error=f"crawl4ai import failed: {exc}",
            )
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
