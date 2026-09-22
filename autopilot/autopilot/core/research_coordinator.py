"""RESEARCH COORDINATOR — Phase 2 Deep Research System.
Coordinates Tier-1 Wikipedia and Tier-2 Crawl4AI providers, normalizes evidence,
performs strict source deduplication, provenance enforcement, and quality filtering.
"""
from __future__ import annotations
import hashlib
import logging
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from autopilot.core.contracts import (
    ResearchEvidenceRecord,
    ResearchBundle,
)
from autopilot.providers.wikipedia_provider import WikipediaProvider
from autopilot.providers.mock_search import MockSearchProvider

logger = logging.getLogger(__name__)


def normalize_source_url(url: str) -> str:
    """Canonicalize a URL for deduplication by stripping tracking params and fragments."""
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlparse(url.strip())
        # Filter tracking query parameters
        clean_query = [
            (k, v) for k, v in urllib.parse.parse_qsl(parsed.query)
            if not k.startswith("utm_") and k not in {"fbclid", "gclid", "ref"}
        ]
        new_query = urllib.parse.urlencode(clean_query)
        clean_path = parsed.path.rstrip("/") if parsed.path != "/" else "/"
        return urllib.parse.urlunparse((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            clean_path,
            "",
            new_query,
            "",
        ))
    except Exception:
        return url.strip()


class ResearchCoordinator:
    """Orchestrates research retrieval across Wikipedia and Crawl4AI with normalization and deduplication."""

    def __init__(
        self,
        wikipedia_provider: Optional[WikipediaProvider] = None,
        crawl4ai_provider: Optional[Any] = None,
        mock_provider: Optional[MockSearchProvider] = None,
    ):
        self.wiki = wikipedia_provider or WikipediaProvider()
        # Crawl4AI is NEVER constructed eagerly. It is resolved lazily (and
        # guarded) the first time a strategy actually routes to it, so that
        # Wikipedia/mock-only research jobs can never import Crawl4AI.
        self._crawl4ai = crawl4ai_provider
        self._crawl4ai_attempted = crawl4ai_provider is not None
        self.mock = mock_provider or MockSearchProvider()

    def _get_crawl4ai(self) -> Optional[Any]:
        """Lazily resolve the Crawl4AI provider through the guarded factory.

        Returns the provider, or ``None`` when Crawl4AI cannot be imported.
        Never blocks indefinitely and never raises.
        """
        if not self._crawl4ai_attempted:
            self._crawl4ai_attempted = True
            from autopilot.providers.crawl4ai_provider import create_crawl4ai_provider
            self._crawl4ai = create_crawl4ai_provider()
        return self._crawl4ai

    def _search_crawl4ai(self, topic: str, max_results: int = 5) -> List[ResearchEvidenceRecord]:
        """Run Crawl4AI search when available; otherwise fail closed with [].

        Structured, non-raising path: an unavailable Crawl4AI simply yields no
        evidence rather than blocking or crashing the research stage.
        """
        provider = self._get_crawl4ai()
        if provider is None:
            logger.warning("crawl4ai provider unavailable; skipping crawl4ai research for topic %r", topic)
            return []
        try:
            return provider.search(topic, max_results=max_results)
        except Exception as exc:  # noqa: BLE001 — provider failure must not crash research
            logger.warning(f"Crawl4AI search failed for topic '{topic}': {exc}")
            return []

    def _fetch_crawl4ai_page(self, url: str) -> Optional[ResearchEvidenceRecord]:
        """Fetch a single URL via Crawl4AI when available; otherwise None."""
        provider = self._get_crawl4ai()
        if provider is None:
            logger.warning("crawl4ai provider unavailable; cannot fetch explicit URL %r", url)
            return None
        try:
            return provider.fetch_page(url)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Crawl4AI fetch failed for url '{url}': {exc}")
            return None

    def coordinate_research(
        self,
        topic: str,
        strategy: str = "wikipedia_first",
        max_results: int = 5,
        allow_mock: bool = False,
    ) -> ResearchBundle:
        """Executes research strategy and returns a normalized, deduplicated ResearchBundle."""
        raw_candidates: List[ResearchEvidenceRecord] = []
        is_url = topic.strip().startswith("http://") or topic.strip().startswith("https://")
        chosen_strategy = strategy

        if is_url:
            chosen_strategy = "explicit_url"
            # Route directly to Crawl4AI for explicit URL
            page_rec = self._fetch_crawl4ai_page(topic.strip())
            if page_rec:
                raw_candidates.append(page_rec)
        elif strategy == "crawl4ai_only":
            raw_candidates.extend(self._search_crawl4ai(topic, max_results=max_results))
        elif strategy == "combined":
            # Tier-1: Wikipedia
            raw_candidates.extend(self._search_wikipedia(topic, max_results=max_results))
            # Tier-2: Crawl4AI (lazy + guarded)
            raw_candidates.extend(self._search_crawl4ai(topic, max_results=max_results))
        elif strategy == "mock":
            if allow_mock:
                raw_candidates.extend(self._search_mock(topic, max_results=max_results))
        else:  # wikipedia_first
            chosen_strategy = "wikipedia_first"
            wiki_results = self._search_wikipedia(topic, max_results=max_results)
            raw_candidates.extend(wiki_results)
            # If Wikipedia yielded sparse results (< 2), augment with Crawl4AI
            if len(wiki_results) < 2:
                crawl_results = self._search_crawl4ai(topic, max_results=max_results - len(wiki_results))
                raw_candidates.extend(crawl_results)

        total_evaluated = len(raw_candidates)

        # ------------------------------------------------------------------
        # Normalization, Quality Gate, and Deduplication
        # ------------------------------------------------------------------
        filtered_candidates = [
            rec for rec in raw_candidates if self._is_valid_evidence(rec, allow_mock=allow_mock)
        ]
        low_quality_filtered = len(raw_candidates) - len(filtered_candidates)
        deduped = self._deduplicate_evidence(filtered_candidates)
        duplicates_filtered = len(filtered_candidates) - len(deduped)
        normalized_sources = deduped[:max_results]

        summary = f"Discovered {len(normalized_sources)} verified research sources for '{topic}' via {chosen_strategy}."
        return ResearchBundle(
            topic=topic,
            sources=normalized_sources,
            summary=summary,
            routing_strategy=chosen_strategy,
            total_sources_evaluated=total_evaluated,
            duplicates_filtered=duplicates_filtered,
            low_quality_filtered=low_quality_filtered,
        )

    def _is_valid_evidence(self, rec: ResearchEvidenceRecord, allow_mock: bool = False) -> bool:
        """Evaluates whether an evidence record meets quality criteria."""
        if not rec or not rec.is_valid_and_non_empty():
            return False
        clean_snippet = (rec.excerpt or "").lower()
        if "lorem ipsum" in clean_snippet:
            return False
        if "synthetic fixture" in clean_snippet or "deterministic demo content" in clean_snippet:
            return allow_mock
        if rec.provider == "mock":
            return allow_mock
        return True

    def _deduplicate_evidence(self, records: List[ResearchEvidenceRecord]) -> List[ResearchEvidenceRecord]:
        """Deduplicates records by canonical URL and normalized title."""
        seen_urls: set[str] = set()
        seen_titles: set[str] = set()
        deduped: List[ResearchEvidenceRecord] = []

        for rec in records:
            norm_url = normalize_source_url(rec.url)
            norm_title = re.sub(r"\W+", " ", (rec.title or "").lower()).strip()

            if norm_url and norm_url in seen_urls:
                continue
            if norm_title and norm_title in seen_titles:
                continue

            if norm_url:
                seen_urls.add(norm_url)
            if norm_title:
                seen_titles.add(norm_title)

            rec.url = norm_url
            deduped.append(rec)

        return deduped

    def _search_wikipedia(self, topic: str, max_results: int = 5) -> List[ResearchEvidenceRecord]:
        """Wrapper to fetch from WikipediaProvider and convert to ResearchEvidenceRecord."""
        try:
            raw_wiki = self.wiki.search(topic, max_results=max_results)
            records: List[ResearchEvidenceRecord] = []
            for item in raw_wiki:
                title = item.get("title", "")
                snippet = item.get("snippet", "")
                url = item.get("url", "")
                source_id = item.get("source_id") or f"wiki-{title.replace(' ', '_')}"
                records.append(
                    ResearchEvidenceRecord(
                        evidence_id=f"ev-{source_id}",
                        source_id=source_id,
                        title=title,
                        url=url,
                        publisher=item.get("publisher", "Wikipedia"),
                        retrieved_at=datetime.now(timezone.utc).isoformat(),
                        excerpt=snippet,
                        relevant_passage=snippet,
                        provider="wikipedia",
                        confidence_score=0.95,
                        provenance_metadata={"search_rank": len(records) + 1},
                    )
                )
            return records
        except Exception as exc:
            logger.warning(f"Wikipedia search failed for topic '{topic}': {exc}")
            return []

    def _search_mock(self, topic: str, max_results: int = 5) -> List[ResearchEvidenceRecord]:
        """Wrapper for MockSearchProvider in unit test environments."""
        try:
            raw_mock = self.mock.search(topic, max_results=max_results)
            records: List[ResearchEvidenceRecord] = []
            for item in raw_mock:
                src_id = item.get("source_id", "mock-src")
                records.append(
                    ResearchEvidenceRecord(
                        evidence_id=f"ev-{src_id}",
                        source_id=src_id,
                        title=item.get("title", "Mock Source"),
                        url=item.get("url", f"https://example.com/{src_id}"),
                        publisher="MockPublisher",
                        retrieved_at=datetime.now(timezone.utc).isoformat(),
                        excerpt=item.get("snippet") or item.get("notes", "Mock research snippet"),
                        relevant_passage=item.get("snippet", ""),
                        provider="mock",
                        confidence_score=0.5,
                        provenance_metadata={"is_mock": True},
                    )
                )
            return records
        except Exception:
            return []
