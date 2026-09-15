"""Simple deterministic file cache for research results — Phase 2.
Cache key derived from topic + normalized query + provider config hash.
Provenance preserved; never mixes unrelated jobs.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from autopilot.core.config import CONFIG


def cache_key(topic: str, normalized_query: str | None, provider_name: str = "local") -> str:
    raw = f"{topic}|{normalized_query or ''}|{provider_name}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def cache_path(key: str) -> Path:
    d = CONFIG.get_artifacts_dir() / "research_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key}.json"


def save_cache(key: str, report_json: dict) -> None:
    path = cache_path(key)
    path.write_text(json.dumps(report_json, indent=2, default=str), encoding="utf-8")


def load_cache(key: str) -> dict | None:
    path = cache_path(key)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if "sources_found" not in data and data.get("results"):
        data["sources_found"] = len(data["results"][0].get("evidence_items", [])) if data["results"] else 0
    return data


def invalidate_cache(key: str) -> None:
    path = cache_path(key)
    if path.exists():
        path.unlink()
