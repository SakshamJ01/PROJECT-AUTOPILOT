"""Deterministic Local Mock Analytics Provider — Milestone 8.
Generates synthetic metrics for offline testing and verification.
All metrics are clearly marked with MetricType.SYNTHETIC and is_synthetic=True.
"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional

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


class MockAnalyticsProvider:
    provider_name: str = "mock"
    capability: CapabilityMetadata = CapabilityMetadata(
        max_resolution="N/A",
        supports_9_16=True,
        local_only=True,
        license_note="Synthetic deterministic mock metrics for offline testing",
    )
    error_type: ProviderErrorType = ProviderErrorType.NOT_AVAILABLE
    cost_meta: CostUsageMetadata = CostUsageMetadata(
        estimated_usd=0.0,
        provider_type="mock",
    )

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            healthy=True,
            provider_name=self.provider_name,
            error="",
            details={"mode": "synthetic_mock", "status": "AVAILABLE"},
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
        """Deterministically generates synthetic performance metrics for a given remote_id and window."""
        if not remote_id:
            raise ValueError("remote_id cannot be empty")

        # Deterministic pseudo-random seed based on remote_id
        seed_src = f"{remote_id}"
        seed_hash = hashlib.sha256(seed_src.encode("utf-8")).hexdigest()
        seed_int = int(seed_hash[:8], 16)

        # Scale metrics based on window
        window_multipliers = {
            "1h": 0.05,
            "24h": 0.25,
            "7d": 0.70,
            "28d": 0.95,
            "lifetime": 1.0,
        }
        mult = window_multipliers.get(window, 1.0)

        base_views = 1000 + (seed_int % 50000)
        views = max(10, int(base_views * mult))
        likes = max(1, int(views * (0.04 + ((seed_int >> 4) % 50) / 1000.0)))
        comments = max(0, int(views * (0.005 + ((seed_int >> 8) % 20) / 1000.0)))
        shares = max(0, int(views * (0.002 + ((seed_int >> 12) % 15) / 1000.0)))
        avg_dur = round(15.0 + ((seed_int >> 16) % 250) / 10.0, 1)  # 15.0 - 40.0 sec
        total_watch = round(views * avg_dur, 1)
        impressions = views * 5
        clicks = views

        perf_window = PerformanceWindow(window) if window in [w.value for w in PerformanceWindow] else PerformanceWindow.LIFETIME
        now_iso = datetime.now(timezone.utc).isoformat()

        raw_payload = {
            "remote_id": remote_id,
            "platform": platform,
            "window": window,
            "views": views,
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "average_view_duration_seconds": avg_dur,
            "watch_time_seconds": total_watch,
            "impressions": impressions,
            "clicks": clicks,
            "synthetic": True,
        }
        raw_hash = hashlib.sha256(json.dumps(raw_payload, sort_keys=True).encode("utf-8")).hexdigest()

        metrics: Dict[str, MetricObservation] = {
            "views": MetricObservation(
                metric_name="views",
                raw_name="views",
                raw_value=float(views),
                normalized_value=float(views),
                unit="count",
                metric_type=MetricType.SYNTHETIC,
                observed_at=now_iso,
                window=perf_window,
            ),
            "likes": MetricObservation(
                metric_name="likes",
                raw_name="likes",
                raw_value=float(likes),
                normalized_value=float(likes),
                unit="count",
                metric_type=MetricType.SYNTHETIC,
                observed_at=now_iso,
                window=perf_window,
            ),
            "comments": MetricObservation(
                metric_name="comments",
                raw_name="comments",
                raw_value=float(comments),
                normalized_value=float(comments),
                unit="count",
                metric_type=MetricType.SYNTHETIC,
                observed_at=now_iso,
                window=perf_window,
            ),
            "shares": MetricObservation(
                metric_name="shares",
                raw_name="shares",
                raw_value=float(shares),
                normalized_value=float(shares),
                unit="count",
                metric_type=MetricType.SYNTHETIC,
                observed_at=now_iso,
                window=perf_window,
            ),
            "average_view_duration_seconds": MetricObservation(
                metric_name="average_view_duration_seconds",
                raw_name="average_view_duration_seconds",
                raw_value=avg_dur,
                normalized_value=avg_dur,
                unit="seconds",
                metric_type=MetricType.SYNTHETIC,
                observed_at=now_iso,
                window=perf_window,
            ),
            "watch_time_seconds": MetricObservation(
                metric_name="watch_time_seconds",
                raw_name="watch_time_seconds",
                raw_value=total_watch,
                normalized_value=total_watch,
                unit="seconds",
                metric_type=MetricType.SYNTHETIC,
                observed_at=now_iso,
                window=perf_window,
            ),
            "impressions": MetricObservation(
                metric_name="impressions",
                raw_name="impressions",
                raw_value=float(impressions),
                normalized_value=float(impressions),
                unit="count",
                metric_type=MetricType.SYNTHETIC,
                observed_at=now_iso,
                window=perf_window,
            ),
            "clicks": MetricObservation(
                metric_name="clicks",
                raw_name="clicks",
                raw_value=float(clicks),
                normalized_value=float(clicks),
                unit="count",
                metric_type=MetricType.SYNTHETIC,
                observed_at=now_iso,
                window=perf_window,
            ),
        }

        snapshot_id = f"snap-{remote_id[:12]}-{window}-{raw_hash[:8]}"

        return AnalyticsSnapshot(
            snapshot_id=snapshot_id,
            job_id=job_id or f"job-{remote_id}",
            content_id=content_id or job_id or remote_id,
            platform=platform,
            remote_id=remote_id,
            window=perf_window,
            observed_at=now_iso,
            retrieved_at=now_iso,
            provider=self.provider_name,
            metrics=metrics,
            derived_metrics={},
            provenance=AnalyticsProvenance(
                provider=self.provider_name,
                source="synthetic",
                remote_content_id=remote_id,
                observed_at=now_iso,
                retrieved_at=now_iso,
                raw_response_hash=raw_hash,
            ),
            is_synthetic=True,
            metadata={"seed_hash": seed_hash},
        )


# Register mock provider
REGISTRY.register(MockAnalyticsProvider())
