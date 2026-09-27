"""Wikipedia / MediaWiki $0 research provider with caching and rate limiting."""
from __future__ import annotations
import urllib.request
import urllib.parse
import urllib.error
import json
import time
import re
from datetime import datetime, timezone
from autopilot.providers.research_contracts import SearchProvider
from autopilot.providers.contracts import ProviderHealth, CapabilityMetadata, CostUsageMetadata, ProviderErrorType
from autopilot.core.contracts import ResearchSource

_WIKI_CACHE: dict[str, tuple[float, list[dict]]] = {}
_LAST_WIKI_REQUEST_TIME: float = 0.0
_MIN_WIKI_INTERVAL: float = 0.5  # seconds
_WIKI_USER_AGENT = "autopilot-wikipedia/1.0 (https://github.com/project-autopilot; research-agent@autopilot.local) ProjectAutopilot/1.0"


class WikipediaProvider(SearchProvider):
    provider_name = "wikipedia"
    capability = CapabilityMetadata(local_only=False, license_note="Public Wikimedia API; $0")
    error_type = ProviderErrorType.UNCONFIGURED
    cost_meta = CostUsageMetadata(estimated_usd=0.0, provider_type="public_api")

    def health_check(self) -> ProviderHealth:
        try:
            req = urllib.request.Request(
                "https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=test&format=json&srlimit=1",
                headers={"User-Agent": _WIKI_USER_AGENT},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                healthy = resp.status == 200
            return ProviderHealth(healthy=healthy, provider_name=self.provider_name, error="" if healthy else str(resp.status))
        except Exception as e:
            return ProviderHealth(healthy=False, provider_name=self.provider_name, error=str(e))

    def _clean_query(self, raw_query: str) -> str:
        """Strip punctuation and question marks for MediaWiki full-text search."""
        cleaned = re.sub(r"[?!.,\"';:()\[\]{}]", " ", raw_query)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    def _extract_core_nouns(self, query: str) -> str:
        """Extract substantive words from query if full query yielded zero hits."""
        stop_words = {
            "what", "is", "the", "a", "an", "are", "was", "were", "actually",
            "why", "how", "who", "when", "where", "about", "and", "or", "to",
            "in", "for", "with", "on", "at", "by", "from", "of"
        }
        words = [w for w in re.findall(r"\b\w+\b", query) if w.lower() not in stop_words and len(w) > 2]
        return " ".join(words[:4]) if words else query

    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
        global _LAST_WIKI_REQUEST_TIME, _WIKI_CACHE
        if not query or not query.strip():
            return []

        clean_q = self._clean_query(query)
        cache_key = f"{clean_q.lower()}:{max_results}"
        if cache_key in _WIKI_CACHE:
            ts, items = _WIKI_CACHE[cache_key]
            if time.time() - ts < 3600.0:
                return list(items)

        def _fetch(term: str) -> list[dict]:
            global _LAST_WIKI_REQUEST_TIME
            elapsed = time.time() - _LAST_WIKI_REQUEST_TIME
            if elapsed < _MIN_WIKI_INTERVAL:
                time.sleep(_MIN_WIKI_INTERVAL - elapsed)

            url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode({
                "action": "query",
                "list": "search",
                "srsearch": term,
                "format": "json",
                "srlimit": max_results,
            })
            req = urllib.request.Request(url, headers={"User-Agent": _WIKI_USER_AGENT})
            _LAST_WIKI_REQUEST_TIME = time.time()
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            results = []
            now_str = datetime.now(timezone.utc).isoformat()
            for item in data.get("query", {}).get("search", []):
                title = item.get("title") or "Article"
                source_id = f"wiki-{title.replace(' ', '_')}"
                # Strip HTML tags from MediaWiki snippet
                snippet = re.sub(r"<[^>]+>", "", item.get("snippet", ""))
                results.append({
                    "source_id": source_id,
                    "title": title,
                    "publisher": "Wikipedia",
                    "url": f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}",
                    "snippet": snippet,
                    "retrieved_at": now_str,
                    "provider": "wikipedia",
                })
            return results

        try:
            hits = _fetch(clean_q)
            if not hits:
                core_nouns = self._extract_core_nouns(clean_q)
                if core_nouns and core_nouns.lower() != clean_q.lower():
                    hits = _fetch(core_nouns)
            if hits:
                _WIKI_CACHE[cache_key] = (time.time(), list(hits))
            return hits
        except Exception:
            return []
