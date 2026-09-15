"""Analytics Engine & Performance Intelligence — Milestone 8.
Orchestrates platform-neutral analytics ingestion, normalization,
derived metric calculation, snapshot persistence, and content linking.
Zero autonomous actions; observation and structured intelligence only.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
import uuid
from typing import Dict, Any, List, Optional

from autopilot.core.config import Config, CONFIG
from autopilot.core.logging import StructuredLogger
from autopilot.core.artifacts import job_artifact_dir
from autopilot.core.contracts import (
    AnalyticsSnapshot,
    MetricObservation,
    DerivedMetric,
    PerformanceWindow,
    MetricType,
    ContentPerformance,
    VideoPerformance,
)
from autopilot.db.manager import DBManager
from autopilot.providers.mock_analytics import MockAnalyticsProvider
from autopilot.providers.youtube_analytics import YouTubeAnalyticsProvider


class AnalyticsEngine:
    def __init__(self, config: Optional[Config] = None, db: Optional[DBManager] = None):
        self.config = config or CONFIG
        self.db = db or DBManager(self.config.db_path)
        self.db.init_schema()

    def get_provider(self, provider_name: str, **kwargs):
        """Resolves an analytics provider instance."""
        p_name = provider_name.lower().strip()
        if p_name in ("mock", "synthetic", "local"):
            return MockAnalyticsProvider()
        elif p_name in ("youtube", "yt"):
            http_client = kwargs.get("http_client")
            return YouTubeAnalyticsProvider(config=self.config, http_client=http_client)
        else:
            raise ValueError(f"Unknown analytics provider: '{provider_name}'. Available: 'mock', 'youtube'")

    def calculate_derived_metrics(
        self,
        metrics: Dict[str, MetricObservation],
        published_at: Optional[str] = None,
        observed_at: Optional[str] = None,
    ) -> Dict[str, DerivedMetric]:
        """Calculates deterministic derived metrics with strict zero-division protection."""
        derived: Dict[str, DerivedMetric] = {}
        now_iso = datetime.now(timezone.utc).isoformat()

        views = metrics.get("views").normalized_value if "views" in metrics else 0.0
        likes = metrics.get("likes").normalized_value if "likes" in metrics else 0.0
        comments = metrics.get("comments").normalized_value if "comments" in metrics else 0.0
        shares = metrics.get("shares").normalized_value if "shares" in metrics else 0.0
        avg_dur = metrics.get("average_view_duration_seconds").normalized_value if "average_view_duration_seconds" in metrics else 0.0
        vid_dur = metrics.get("video_duration_seconds").normalized_value if "video_duration_seconds" in metrics else 0.0

        # 1. Engagement Rate: (likes + comments + shares) / views
        if views > 0:
            eng_val = round((likes + comments + shares) / views, 4)
            derived["engagement_rate"] = DerivedMetric(
                metric_name="engagement_rate",
                value=eng_val,
                formula="(likes + comments + shares) / views",
                input_metrics={"views": views, "likes": likes, "comments": comments, "shares": shares},
                calculated_at=now_iso,
                confidence=1.0,
            )

            # 2. Like Ratio: likes / views
            like_val = round(likes / views, 4)
            derived["like_ratio"] = DerivedMetric(
                metric_name="like_ratio",
                value=like_val,
                formula="likes / views",
                input_metrics={"views": views, "likes": likes},
                calculated_at=now_iso,
                confidence=1.0,
            )

            # 3. Comment Ratio: comments / views
            comm_val = round(comments / views, 4)
            derived["comment_ratio"] = DerivedMetric(
                metric_name="comment_ratio",
                value=comm_val,
                formula="comments / views",
                input_metrics={"views": views, "comments": comments},
                calculated_at=now_iso,
                confidence=1.0,
            )

        # 4. View Velocity: views / hours_since_publication
        if views > 0 and published_at:
            try:
                # Handle Z or offset
                pub_clean = published_at.replace("Z", "+00:00")
                pub_dt = datetime.fromisoformat(pub_clean)
                obs_dt = datetime.fromisoformat(observed_at.replace("Z", "+00:00")) if observed_at else datetime.now(timezone.utc)
                delta_sec = (obs_dt - pub_dt).total_seconds()
                if delta_sec > 60.0:  # at least 1 minute
                    hours = delta_sec / 3600.0
                    vel = round(views / hours, 2)
                    derived["view_velocity_per_hour"] = DerivedMetric(
                        metric_name="view_velocity_per_hour",
                        value=vel,
                        formula="views / hours_since_publication",
                        input_metrics={"views": views, "hours": round(hours, 2)},
                        calculated_at=now_iso,
                        confidence=0.9,
                    )
            except Exception:
                pass

        # 5. Completion Rate: avg_duration / video_duration
        if avg_dur > 0 and vid_dur > 0:
            comp_rate = min(1.0, round(avg_dur / vid_dur, 4))
            derived["completion_rate"] = DerivedMetric(
                metric_name="completion_rate",
                value=comp_rate,
                formula="average_view_duration / video_duration",
                input_metrics={"average_view_duration": avg_dur, "video_duration": vid_dur},
                calculated_at=now_iso,
                confidence=1.0,
            )

        return derived

    def sync_job(
        self,
        job_id: str,
        platform: Optional[str] = None,
        provider_name: Optional[str] = None,
        window: str = "lifetime",
        dry_run: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        """Synchronizes analytics metrics for a single job."""
        logger = StructuredLogger(job_id=job_id, stage="analytics")
        logger.info("analytics_sync_started", details={"job_id": job_id, "window": window, "dry_run": dry_run})

        # 1. Resolve publication record
        pub_row = None
        with self.db._connect() as conn:
            pub_row = conn.execute(
                "SELECT * FROM publish_records WHERE job_id = ? ORDER BY created_at DESC LIMIT 1",
                (job_id,),
            ).fetchone()

        target_platform = platform or (pub_row["platform"] if pub_row else "youtube")
        remote_id = pub_row["remote_video_id"] if (pub_row and pub_row["remote_video_id"]) else f"vid-{job_id}"
        content_id = pub_row["content_id"] if pub_row else job_id
        published_at = pub_row["created_at"] if pub_row else None

        # 2. Select provider
        p_name = provider_name or self.config.analytics_default_provider
        provider = self.get_provider(p_name, **kwargs)

        if dry_run:
            logger.info("analytics_sync_dry_run", details={"remote_id": remote_id, "provider": provider.provider_name})
            return {
                "job_id": job_id,
                "remote_id": remote_id,
                "platform": target_platform,
                "provider": provider.provider_name,
                "window": window,
                "dry_run": True,
                "status": "simulated",
            }

        # 3. Fetch snapshot from provider
        snapshot = provider.fetch_snapshot(
            remote_id=remote_id,
            platform=target_platform,
            window=window,
            job_id=job_id,
            content_id=content_id,
            **kwargs,
        )

        # 4. Calculate deterministic derived metrics
        derived = self.calculate_derived_metrics(
            metrics=snapshot.metrics,
            published_at=published_at,
            observed_at=snapshot.observed_at,
        )
        snapshot.derived_metrics = derived

        # 5. Persist to SQLite
        recorded_id = self.db.record_analytics_snapshot(snapshot)

        # 5b. Persist normalized VideoPerformance snapshot for historical trend & attribution
        try:
            topic_val = None
            channel_val = None
            hook_val = None
            dur_val_attr = None
            prod_eng = None
            voice_val = None
            motif_val = None
            with self.db._connect() as conn:
                jrow = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
                if jrow:
                    topic_val = jrow["topic"]
                    channel_val = jrow["channel_id"]
                    try:
                        mjson = json.loads(jrow["metadata_json"] or "{}")
                        hook_val = mjson.get("hook")
                        dur_val_attr = mjson.get("estimated_duration_seconds") or mjson.get("duration_sec")
                        prod_eng = mjson.get("production_engine")
                        voice_val = mjson.get("voice_id")
                        motif_val = mjson.get("visual_motif")
                    except Exception:
                        pass

            views_num = int(snapshot.metrics["views"].normalized_value) if "views" in snapshot.metrics else 0
            likes_num = int(snapshot.metrics["likes"].normalized_value) if "likes" in snapshot.metrics else 0
            comm_num = int(snapshot.metrics["comments"].normalized_value) if "comments" in snapshot.metrics else 0
            shares_num = int(snapshot.metrics["shares"].normalized_value) if "shares" in snapshot.metrics else 0
            wt_sec = float(snapshot.metrics["watch_time_seconds"].normalized_value) if "watch_time_seconds" in snapshot.metrics else 0.0
            avg_view_dur = float(snapshot.metrics["average_view_duration_seconds"].normalized_value) if "average_view_duration_seconds" in snapshot.metrics else 0.0
            ret_rate = derived.get("completion_rate").value if "completion_rate" in derived else None
            ctr_val = float(snapshot.metrics["ctr"].normalized_value) if "ctr" in snapshot.metrics else None
            imp_val = int(snapshot.metrics["impressions"].normalized_value) if "impressions" in snapshot.metrics else None
            sub_val = int(snapshot.metrics["subscribers_gained"].normalized_value) if "subscribers_gained" in snapshot.metrics else None

            v_perf = VideoPerformance(
                performance_id=f"perf-{uuid.uuid4().hex[:12]}",
                job_id=job_id,
                platform=target_platform,
                remote_id=remote_id,
                collected_at=snapshot.observed_at or datetime.now(timezone.utc).isoformat(),
                views=views_num,
                likes=likes_num,
                comments=comm_num,
                shares=shares_num,
                watch_time_seconds=wt_sec,
                avg_view_duration_seconds=avg_view_dur,
                retention_rate=ret_rate,
                ctr=ctr_val,
                impressions=imp_val,
                subscriber_change=sub_val,
                raw_metrics={k: m.normalized_value for k, m in snapshot.metrics.items()},
                topic=topic_val,
                channel_id=channel_val,
                hook=hook_val,
                duration_sec=dur_val_attr,
                production_engine=prod_eng,
                voice_id=voice_val,
                visual_motif=motif_val,
            )
            self.db.record_video_performance(v_perf)
        except Exception as perf_err:
            # P1-03 fix: don't silently swallow — warn so data loss is visible.
            logger.warning("video_performance_record_failed", error=str(perf_err))

        # 6. Save artifact
        try:
            art_dir = job_artifact_dir(job_id, base_dir=self.config.get_artifacts_dir()) / "analytics"
            art_dir.mkdir(parents=True, exist_ok=True)
            snap_file = art_dir / f"snapshot_{snapshot.snapshot_id}.json"
            snap_file.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
            latest_file = art_dir / "latest.json"
            latest_file.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
        except Exception:
            pass

        logger.info("analytics_sync_completed", details={"snapshot_id": recorded_id, "metrics_count": len(snapshot.metrics)})

        return {
            "job_id": job_id,
            "snapshot_id": recorded_id,
            "remote_id": remote_id,
            "platform": target_platform,
            "provider": provider.provider_name,
            "window": window,
            "metrics": {k: m.normalized_value for k, m in snapshot.metrics.items()},
            "derived_metrics": {k: d.value for k, d in snapshot.derived_metrics.items()},
            "is_synthetic": snapshot.is_synthetic,
            "status": "success",
        }

    def sync_all(
        self,
        platform: Optional[str] = None,
        provider_name: Optional[str] = None,
        window: str = "lifetime",
        limit: int = 25,
        dry_run: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        """Synchronizes metrics for all published jobs up to the batch limit."""
        target_jobs = []
        with self.db._connect() as conn:
            if platform:
                rows = conn.execute(
                    "SELECT DISTINCT job_id FROM publish_records WHERE platform = ? ORDER BY created_at DESC LIMIT ?",
                    (platform, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT DISTINCT job_id FROM publish_records ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            target_jobs = [r["job_id"] for r in rows]

        # P1-02 fix: Only include jobs that actually have a publish record.
        # Jobs in APPROVED/RENDERED have no remote_id and cannot be synced —
        # attempting it wastes API calls and produces noise.
        if not target_jobs:
            return {
                "synced": 0,
                "errors": 0,
                "jobs": [],
                "note": "No published jobs found; nothing to sync.",
            }

        results = []
        for job_id in target_jobs:
            try:
                res = self.sync_job(
                    job_id=job_id,
                    platform=platform,
                    provider_name=provider_name,
                    window=window,
                    dry_run=dry_run,
                    **kwargs,
                )
                results.append(res)
            except Exception as exc:
                results.append({
                    "job_id": job_id,
                    "status": "error",
                    "error": str(exc),
                })

        return {
            "total_targeted": len(target_jobs),
            "synced_count": len([r for r in results if r.get("status") in ("success", "simulated")]),
            "dry_run": dry_run,
            "results": results,
        }

    def get_performance_report(
        self,
        job_id: Optional[str] = None,
        platform: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Generates a structured performance report joining content features with outcomes."""
        report = []
        with self.db._connect() as conn:
            query = "SELECT job_id FROM jobs"
            params: List[Any] = []
            if job_id:
                query += " WHERE job_id = ?"
                params.append(job_id)
            query += " ORDER BY updated_at DESC LIMIT ?"
            params.append(limit)

            job_rows = conn.execute(query, tuple(params)).fetchall()

        for j in job_rows:
            perf = self.db.get_content_performance(j["job_id"])
            # Only include jobs that actually have analytics data (P1-02 alignment).
            if not perf or not perf.latest_snapshot:
                continue

            snap = perf.latest_snapshot
            views = snap.metrics.get("views").normalized_value if (snap and "views" in snap.metrics) else 0.0
            likes = snap.metrics.get("likes").normalized_value if (snap and "likes" in snap.metrics) else 0.0
            comments = snap.metrics.get("comments").normalized_value if (snap and "comments" in snap.metrics) else 0.0
            eng_rate = snap.derived_metrics.get("engagement_rate").value if (snap and "engagement_rate" in snap.derived_metrics) else 0.0

            report.append({
                "job_id": perf.job_id,
                "topic": perf.topic or "N/A",
                "platform": perf.platform or "youtube",
                "remote_id": perf.remote_id or "N/A",
                "views": int(views),
                "likes": int(likes),
                "comments": int(comments),
                "engagement_rate": eng_rate,
                "snapshots_recorded": len(perf.snapshot_history),
                "is_synthetic": snap.is_synthetic if snap else False,
                "latest_observed_at": snap.observed_at if snap else None,
            })

        return report

    def attribute_channel_performance(self, channel_id: str) -> Dict[str, Any]:
        """Answers: 'What kinds of videos perform better on this channel?'
        Aggregates performance by duration, hook pattern, and production engine.
        """
        snapshots = self.db.get_channel_performance_history(channel_id)
        if not snapshots:
            return {
                "channel_id": channel_id,
                "total_videos": 0,
                "status": "insufficient_data",
                "message": f"No performance records found for channel '{channel_id}'.",
                "top_durations": [],
                "top_hooks": [],
                "top_engines": [],
                "recommendation": "Produce at least 3 videos to establish baseline channel intelligence.",
            }

        total_vids = len(snapshots)
        total_views = sum(s.get("views", 0) for s in snapshots)
        avg_views = round(total_views / total_vids, 1) if total_vids else 0.0

        durations: Dict[str, List[int]] = {"short (<30s)": [], "medium (30-50s)": [], "long (>50s)": []}
        hooks: Dict[str, List[int]] = {}
        engines: Dict[str, List[int]] = {}

        for s in snapshots:
            v = s.get("views", 0)
            d = s.get("duration_sec")
            if d is not None:
                if d < 30:
                    durations["short (<30s)"].append(v)
                elif d <= 50:
                    durations["medium (30-50s)"].append(v)
                else:
                    durations["long (>50s)"].append(v)

            hk = s.get("hook")
            if hk:
                hk_key = hk[:30] + "..." if len(hk) > 30 else hk
                hooks.setdefault(hk_key, []).append(v)

            eng = s.get("production_engine") or "default"
            engines.setdefault(eng, []).append(v)

        def _calc_rankings(dmap: Dict[str, List[int]]) -> List[Dict[str, Any]]:
            res = []
            for k, vals in dmap.items():
                if vals:
                    res.append({
                        "category": k,
                        "sample_size": len(vals),
                        "avg_views": round(sum(vals) / len(vals), 1),
                        "max_views": max(vals),
                    })
            res.sort(key=lambda x: x["avg_views"], reverse=True)
            return res

        dur_ranking = _calc_rankings(durations)
        hook_ranking = _calc_rankings(hooks)
        engine_ranking = _calc_rankings(engines)

        rec = "Maintain balanced cadence."
        if dur_ranking and dur_ranking[0]["sample_size"] >= 2:
            rec = f"Videos with {dur_ranking[0]['category']} outperform others with {dur_ranking[0]['avg_views']} avg views."

        return {
            "channel_id": channel_id,
            "total_videos": total_vids,
            "average_views": avg_views,
            "top_durations": dur_ranking,
            "top_hooks": hook_ranking,
            "top_engines": engine_ranking,
            "recommendation": rec,
            "status": "ready",
        }
