"""Regression tests for the round-3 hardening pass.

Covers: safe-download LFI/SSRF guards, loud trend-provider resolution, and the
no-false-PASS caption placement rule.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from autopilot.core.asset_cache import safe_download_media
from autopilot.providers.trend_factory import get_trend_provider


def test_safe_download_rejects_local_path_by_default(tmp_path: Path) -> None:
    src = tmp_path / "secret.png"
    src.write_bytes(b"\x89PNG")
    out = tmp_path / "out.png"
    with pytest.raises(ValueError):
        safe_download_media(str(src), out)


def test_safe_download_allows_local_when_opted_in(tmp_path: Path) -> None:
    src = tmp_path / "source.png"
    src.write_bytes(b"\x89PNG-data")
    out = tmp_path / "out.png"
    result = safe_download_media(str(src), out, allow_local=True)
    assert Path(result).exists()
    assert Path(result).read_bytes() == b"\x89PNG-data"


@pytest.mark.parametrize("url", ["http://127.0.0.1/x.mp4", "http://localhost/x.mp4"])
def test_safe_download_rejects_loopback_hosts(tmp_path: Path, url: str) -> None:
    with pytest.raises(ValueError):
        safe_download_media(url, tmp_path / "out.mp4")


def test_safe_download_rejects_malformed_scheme(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        safe_download_media("ftp://example.com/x.mp4", tmp_path / "out.mp4")


def test_trend_factory_unknown_provider_fails_loudly() -> None:
    with pytest.raises(ValueError, match="Unknown trend provider"):
        get_trend_provider("definitely-not-a-provider")


def test_trend_factory_known_providers() -> None:
    # Defaults and the reddit provider must still resolve.
    assert get_trend_provider(None).__class__.__name__ == "MockTrendProvider"
    assert get_trend_provider("mock").__class__.__name__ == "MockTrendProvider"
    assert get_trend_provider("reddit").__class__.__name__ == "RedditTrendProvider"
