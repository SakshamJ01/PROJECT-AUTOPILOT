"""Regression tests: Crawl4AI must be an optional/lazy backend and never block jobs.

Locks in the fix for the desktop E2E blocker where ``import crawl4ai`` (numpy
C-extension load) could hang the RESEARCH stage for 40+ minutes with zero DB
progress. These tests pin the required invariants:

1. Importing the pipeline never imports Crawl4AI.
2. Wikipedia-only/default research jobs complete without touching Crawl4AI.
3. Crawl4AI unavailability fails closed (quick, structured, non-raising).
4. The availability probe is bounded: a blocked import can never hang callers.
"""
import importlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from autopilot.core.research_coordinator import ResearchCoordinator
from autopilot.providers import crawl4ai_provider as c4a_provider

REPO_ROOT = Path(__file__).resolve().parent.parent


def _crawl4ai_related(name: str) -> bool:
    return (
        name == "crawl4ai"
        or name.startswith("crawl4ai.")
        or name == "autopilot.providers.crawl4ai_provider"
    )


def _fresh_interpreter(code: str, timeout: float = 180.0) -> str:
    """Run code in a clean interpreter with the autopilot package importable."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert proc.returncode == 0, (
        f"fresh interpreter failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    return proc.stdout


def test_pipeline_import_does_not_import_crawl4ai():
    """Importing the pipeline (and coordinator) must not pull in Crawl4AI."""
    out = _fresh_interpreter(
        "import sys\n"
        "import autopilot.core.pipeline\n"
        "bad = [m for m in sys.modules if m == 'crawl4ai' or m.startswith('crawl4ai.')"
        " or m == 'autopilot.providers.crawl4ai_provider']\n"
        "assert not bad, f'pipeline import pulled in crawl4ai: {bad}'\n"
        "print('PIPELINE_OK')\n"
    )
    assert "PIPELINE_OK" in out


def test_wikipedia_only_research_job_never_loads_crawl4ai():
    """A wikipedia-first job with sufficient results never touches Crawl4AI."""
    code = (
        "import sys\n"
        "from unittest.mock import MagicMock\n"
        "from autopilot.core.research_coordinator import ResearchCoordinator\n"
        "wiki = MagicMock()\n"
        "def fake_search(topic, max_results=5):\n"
        "    return [\n"
        "        {'title': f'Article {i}', 'snippet': f'Detailed verified content about {topic} part {i}.',\n"
        "         'url': f'https://en.wikipedia.org/wiki/T{i}', 'source_id': f'w{i}'}\n"
        "        for i in range(5)\n"
        "    ]\n"
        "wiki.search.side_effect = fake_search\n"
        "coord = ResearchCoordinator(wikipedia_provider=wiki)\n"
        "bundle = coord.coordinate_research(topic='Quantum Computing', strategy='wikipedia_first', max_results=5)\n"
        "assert len(bundle.sources) == 5, bundle.sources\n"
        "bad = [m for m in sys.modules if m == 'crawl4ai' or m.startswith('crawl4ai.')\n"
        "       or m == 'autopilot.providers.crawl4ai_provider']\n"
        "assert not bad, f'wikipedia-first job pulled in crawl4ai: {bad}'\n"
        'print("WIKI_OK")\n'
    )
    out = _fresh_interpreter(code)
    assert "WIKI_OK" in out


def test_crawl4ai_unavailable_fails_closed_quickly(monkeypatch):
    """Unavailable Crawl4AI yields non-raising, structured None/[] results."""
    monkeypatch.setattr(c4a_provider, "_CRAWL4AI_IMPORT_STATE", False)
    with patch.object(c4a_provider.Crawl4AIProvider, "_detect_crawl4ai", return_value=False):
        provider = c4a_provider.Crawl4AIProvider()
        assert provider._crawler_available is False
        health = provider.health_check()
        assert health.healthy is False
        assert "crawl4ai" in health.error
        assert provider.fetch_page("https://example.com/any") is None
        assert provider.crawl_url("https://example.com/any") is None
        assert provider.search("some query") == []

    monkeypatch.setattr(c4a_provider, "_CRAWL4AI_IMPORT_STATE", False)
    assert c4a_provider.create_crawl4ai_provider() is None

    monkeypatch.setattr(c4a_provider, "_CRAWL4AI_IMPORT_STATE", True)
    with patch.object(
        c4a_provider.Crawl4AIProvider, "__init__", side_effect=RuntimeError("boom")
    ):
        assert c4a_provider.create_crawl4ai_provider() is None


def test_combined_strategy_succeeds_without_crawl4ai(monkeypatch):
    """Combined strategy still yields wikipedia evidence when Crawl4AI is absent."""
    monkeypatch.setattr(c4a_provider, "_CRAWL4AI_IMPORT_STATE", False)
    coord = ResearchCoordinator()
    wiki = MagicMock()
    wiki.search.return_value = [
        {
            "title": "Deep Sea Exploration",
            "snippet": "Scientific deep-sea exploration uses remotely operated vehicles.",
            "url": "https://en.wikipedia.org/wiki/Deep-sea_exploration",
            "source_id": "w-deepsea",
        }
    ]
    coord.wiki = wiki
    bundle = coord.coordinate_research(topic="Deep Sea Exploration", strategy="combined", max_results=5)
    assert len(bundle.sources) >= 1
    assert all(rec.provider == "wikipedia" for rec in bundle.sources)


def test_import_probe_is_bounded_when_import_blocks(monkeypatch):
    """A blocked import must fail the probe closed instead of hanging callers."""
    removed = {}
    for k in list(sys.modules):
        if _crawl4ai_related(k):
            removed[k] = sys.modules.pop(k)

    class _BlockedThread:
        started = False

        def __init__(self, target, name, daemon):
            _BlockedThread.started = True

        def start(self):
            pass

        def join(self, timeout=None):
            return None

    fake_threading = SimpleNamespace(Thread=_BlockedThread)
    monkeypatch.setattr(c4a_provider, "_CRAWL4AI_IMPORT_STATE", None)
    monkeypatch.setattr(c4a_provider, "threading", fake_threading)
    with patch.object(importlib.util, "find_spec", return_value=object()):
        ok = c4a_provider._crawl4ai_importable(timeout=0.5)
    assert ok is False
    assert _BlockedThread.started is True

    sys.modules.update(removed)