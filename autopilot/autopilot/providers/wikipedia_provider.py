"""Wikipedia / MediaWiki $0 research provider."""
from __future__ import annotations
import urllib.request, urllib.parse, json, time
from autopilot.providers.research_contracts import SearchProvider
from autopilot.providers.contracts import ProviderHealth, CapabilityMetadata, CostUsageMetadata, ProviderErrorType
from autopilot.core.contracts import ResearchSource

class WikipediaProvider(SearchProvider):
    provider_name = "wikipedia"
    capability = CapabilityMetadata(local_only=False, license_note="Public Wikimedia API; $0")
    error_type = ProviderErrorType.UNCONFIGURED
    cost_meta = CostUsageMetadata(estimated_usd=0.0, provider_type="public_api")
    def health_check(self) -> ProviderHealth:
        try:
            req = urllib.request.Request("https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=test&format=json&srlimit=1", headers={"User-Agent":"autopilot-wikipedia/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                healthy = resp.status == 200
            return ProviderHealth(healthy=healthy, provider_name=self.provider_name, error="" if healthy else str(resp.status))
        except Exception as e:
            return ProviderHealth(healthy=False, provider_name=self.provider_name, error=str(e))
    def search(self, query: str, max_results: int = 10, **kwargs) -> list[dict]:
        try:
            url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode({"action":"query","list":"search","srsearch":query,"format":"json","srlimit":max_results})
            req = urllib.request.Request(url, headers={"User-Agent":"autopilot-wikipedia/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            results = []
            for item in data.get("query",{}).get("search",[]):
                title = item.get("title")
                source_id = f"wiki-{title.replace(' ','_')}"
                results.append({"source_id":source_id,"title":title,"publisher":"Wikipedia","url":f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ','_'))}","snippet":item.get("snippet",""),"retrieved_at":"2026-09-11","provider":"wikipedia"})
            return results
        except Exception:
            return []
