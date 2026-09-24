"""Official YouTube Analytics & Statistics Adapter — Milestone 8.
Uses official YouTube Data API v3 (videos.list with part=statistics,contentDetails,snippet).
Zero browser scraping. Zero unofficial endpoints.
All tests mock the HTTP transport.
"""
from __future__ import annotations
import hashlib
import json
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Callable

from autopilot.core.config import Config, CONFIG
from autopilot.core.contracts import (
    AnalyticsSnapshot,
    MetricObservation,
    AnalyticsProvenance,
    PerformanceWindow,
    MetricType,
)
from autopilot.providers.contracts import (
    AnalyticsProvider,
    ProviderHealth,
    CapabilityMetadata,
    CostUsageMetadata,
    ProviderErrorType,
    REGISTRY,
)
from autopilot.providers.youtube_publisher import redact_secrets


def parse_iso8601_duration(duration_str: str) -> float:
    """Parses ISO 8601 duration string like 'PT1M30S', 'PT45S', 'PT1H2M10S' into seconds."""
    if not duration_str or not duration_str.startswith("PT"):
        return 0.0
    pattern = re.compile(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?')
    match = pattern.match(duration_str)
    if not match:
        return 0.0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return float(hours * 3600 + minutes * 60 + seconds)


class YouTubeAnalyticsProvider:
    provider_name: str = "youtube"
    capability: CapabilityMetadata = CapabilityMetadata(
        max_resolution="4k",
        supports_9_16=True,
        local_only=False,
        license_note="Official YouTube Data API v3 statistics",
    )
    error_type: ProviderErrorType = ProviderErrorType.NOT_AVAILABLE
    cost_meta: CostUsageMetadata = CostUsageMetadata(
        estimated_usd=0.0,
        provider_type="remote",
    )

    def __init__(self, config: Optional[Config] = None, http_client: Optional[Callable[[str, Dict[str, str]], Dict[str, Any]]] = None):
        self.config = config or CONFIG
        self.http_client = http_client

    def _resolve_token(self) -> Optional[str]:
        """Resolve YouTube OAuth token. P0-04 fix: delegates to shared resolver."""
        from autopilot.providers.youtube_oauth import resolve_youtube_access_token
        return resolve_youtube_access_token(self.config)

    def health_check(self) -> ProviderHealth:
        token = self._resolve_token()
        if not token:
            return ProviderHealth(
                healthy=False,
                provider_name=self.provider_name,
                error="YouTube credentials not configured (YOUTUBE_ACCESS_TOKEN or YOUTUBE_TOKEN_PATH)",
                details={"configured": False, "mode": "official_api"},
            )
        return ProviderHealth(
            healthy=True,
            provider_name=self.provider_name,
            error="",
            details={"configured": True, "mode": "official_api"},
        )

    def fetch_snapshot(
        self,
        remote_id: str,
        platform: str = "youtube",
        window: str = "lifetime",
        job_id: Optional[str] = None,
        content_id: Optional[str] = None,
        **kwargs,
    ) -> AnalyticsSnapshot:
        """Fetches video performance statistics from YouTube Data API v3."""
        if not remote_id or not remote_id.strip():
            raise ValueError("remote_id cannot be empty")

        url = f"https://www.googleapis.com/youtube/v3/videos?part=statistics,contentDetails,snippet&id={remote_id.strip()}"
        raw_data: Dict[str, Any]

        if self.http_client:
            token = self._resolve_token() or "mock-bearer-token"
            raw_data = self.http_client(url, {"Authorization": f"Bearer {token}"})
        else:
            token = self._resolve_token()
            if not token:
                raise ValueError("YouTube API credentials not configured. Provide YOUTUBE_ACCESS_TOKEN or mock client.")

            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                method="GET",
            )
            try:
                with urllib.request.urlopen(req, timeout=15.0) as resp:
                    resp_bytes = resp.read()
                    raw_data = json.loads(resp_bytes.decode("utf-8"))
            except urllib.error.HTTPError as err:
                body = err.read().decode("utf-8", errors="replace")
                safe_err = redact_secrets(f"HTTP {err.code}: {body}")
                raise RuntimeError(f"YouTube API error: {safe_err}")
            except Exception as exc:
                raise RuntimeError(f"YouTube request failed: {redact_secrets(str(exc))}")

        raw_str = json.dumps(raw_data, sort_keys=True)
        raw_hash = hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

        items = raw_data.get("items", [])
        if not items:
            raise ValueError(f"Video not found on YouTube: {remote_id}")

        item = items[0]
        stats = item.get("statistics", {})
        content_details = item.get("contentDetails", {})

        try:
            views = float(stats.get("viewCount", 0))
            likes = float(stats.get("likeCount", 0))
            comments = float(stats.get("commentCount", 0))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Malformed statistics in YouTube API response: {exc}")

        if views < 0 or likes < 0 or comments < 0:
            raise ValueError("Impossible negative metric detected in YouTube response")

        dur_iso = content_details.get("duration", "")
        duration_sec = parse_iso8601_duration(dur_iso)

        now_iso = datetime.now(timezone.utc).isoformat()
        perf_window = PerformanceWindow(window) if window in [w.value for w in PerformanceWindow] else PerformanceWindow.LIFETIME

        metrics: Dict[str, MetricObservation] = {
            "views": MetricObservation(
                metric_name="views",
                raw_name="viewCount",
                raw_value=views,
                normalized_value=views,
                unit="count",
                metric_type=MetricType.MEASURED,
                observed_at=now_iso,
                window=perf_window,
            ),
            "likes": MetricObservation(
                metric_name="likes",
                raw_name="likeCount",
                raw_value=likes,
                normalized_value=likes,
                unit="count",
                metric_type=MetricType.MEASURED,
                observed_at=now_iso,
                window=perf_window,
            ),
            "comments": MetricObservation(
                metric_name="comments",
                raw_name="commentCount",
                raw_value=comments,
                normalized_value=comments,
                unit="count",
                metric_type=MetricType.MEASURED,
                observed_at=now_iso,
                window=perf_window,
            ),
        }

        if duration_sec > 0:
            metrics["video_duration_seconds"] = MetricObservation(
                metric_name="video_duration_seconds",
                raw_name="duration",
                raw_value=duration_sec,
                normalized_value=duration_sec,
                unit="seconds",
                metric_type=MetricType.MEASURED,
                observed_at=now_iso,
                window=perf_window,
            )

        snapshot_id = f"snap-yt-{remote_id[:12]}-{window}-{raw_hash[:8]}"

        return AnalyticsSnapshot(
            snapshot_id=snapshot_id,
            job_id=job_id or f"job-{remote_id}",
            content_id=content_id or job_id or remote_id,
            platform="youtube",
            remote_id=remote_id,
            window=perf_window,
            observed_at=now_iso,
            retrieved_at=now_iso,
            provider=self.provider_name,
            metrics=metrics,
            derived_metrics={},
            provenance=AnalyticsProvenance(
                provider=self.provider_name,
                source="api",
                remote_content_id=remote_id,
                observed_at=now_iso,
                retrieved_at=now_iso,
                raw_response_hash=raw_hash,
            ),
            is_synthetic=False,
            metadata={
                "title": item.get("snippet", {}).get("title", ""),
                "published_at": item.get("snippet", {}).get("publishedAt", ""),
            },
        )
