"""SQLite persistence — Phase 0.
Schema designed to be migrated without rewriting core.
"""
from __future__ import annotations
import sqlite3
import json
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional

DB_SCHEMA_VERSION = 11

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    channel_id TEXT,
    topic TEXT,
    status TEXT NOT NULL DEFAULT 'IDEA',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    idempotency_key TEXT UNIQUE,
    provenance_json TEXT DEFAULT '{}',
    metadata_json TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_idempotency ON jobs(idempotency_key);

CREATE TABLE IF NOT EXISTS workflow_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    reason TEXT,
    occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    idempotency_key TEXT,
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_events_job ON workflow_events(job_id);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    artifact_type TEXT NOT NULL, -- media | script | provenance | thumbnail
    checksum_sha256 TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_artifacts_job ON artifacts(job_id);

CREATE TABLE IF NOT EXISTS errors (
    error_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    stage TEXT NOT NULL,
    error_type TEXT NOT NULL,
    message TEXT NOT NULL,
    occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    details_json TEXT DEFAULT '{}',
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_errors_job ON errors(job_id);

CREATE TABLE IF NOT EXISTS research_requests (
    request_id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    language TEXT DEFAULT 'en',
    normalized_query TEXT,
    max_sources INTEGER DEFAULT 10,
    provider_config TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'pending'
);
CREATE INDEX IF NOT EXISTS idx_research_topic ON research_requests(topic);

CREATE TABLE IF NOT EXISTS research_reports (
    report_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    summary TEXT,
    provenance_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (request_id) REFERENCES research_requests(request_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_reports_request ON research_reports(request_id);

CREATE TABLE IF NOT EXISTS research_evidence (
    evidence_id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    snippet TEXT,
    relevance_score REAL DEFAULT 0.5,
    status TEXT DEFAULT 'discovered',
    provenance_json TEXT DEFAULT '{}',
    FOREIGN KEY (report_id) REFERENCES research_reports(report_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_evidence_report ON research_evidence(report_id);

CREATE TABLE IF NOT EXISTS research_cache (
    cache_key TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    normalized_query TEXT,
    provider_name TEXT DEFAULT 'local',
    report_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cache_key ON research_cache(cache_key);

CREATE TABLE IF NOT EXISTS voice_artifacts (
    artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    content_id TEXT NOT NULL,
    segment_id TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    provider TEXT DEFAULT 'mock_tts',
    model_voice TEXT,
    duration_sec REAL,
    checksum_sha256 TEXT,
    provenance_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_voice_job ON voice_artifacts(job_id);
CREATE INDEX IF NOT EXISTS idx_voice_segment ON voice_artifacts(segment_id);

CREATE TABLE IF NOT EXISTS asset_artifacts (
    artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    content_id TEXT NOT NULL,
    scene_id TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    asset_type TEXT DEFAULT 'image',
    checksum_sha256 TEXT,
    provenance_json TEXT DEFAULT '{}',
    license_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_asset_job ON asset_artifacts(job_id);

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS qa_runs (
    report_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    content_id TEXT,
    status TEXT NOT NULL,
    publish_allowed INTEGER NOT NULL DEFAULT 1,
    qa_version TEXT DEFAULT 'v1.0.0',
    profile TEXT DEFAULT 'vertical_short',
    metrics_json TEXT DEFAULT '{}',
    receipt_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_qa_job ON qa_runs(job_id);

CREATE TABLE IF NOT EXISTS qa_checks (
    check_id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL,
    category TEXT NOT NULL,
    status TEXT NOT NULL,
    severity TEXT NOT NULL,
    measured_value TEXT,
    expected_value TEXT,
    message TEXT,
    timestamp TEXT NOT NULL,
    FOREIGN KEY (report_id) REFERENCES qa_runs(report_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_qa_checks_report ON qa_checks(report_id);

CREATE TABLE IF NOT EXISTS qa_findings (
    finding_id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL,
    check_id TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL,
    evidence_json TEXT DEFAULT '{}',
    timestamp TEXT NOT NULL,
    FOREIGN KEY (report_id) REFERENCES qa_runs(report_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_qa_findings_report ON qa_findings(report_id);

CREATE TABLE IF NOT EXISTS qa_metrics (
    metric_id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    value_numeric REAL,
    value_text TEXT,
    unit TEXT,
    status TEXT NOT NULL,
    FOREIGN KEY (report_id) REFERENCES qa_runs(report_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_qa_metrics_report ON qa_metrics(report_id);

CREATE TABLE IF NOT EXISTS publish_records (
    publish_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    content_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    provider TEXT NOT NULL,
    status TEXT NOT NULL,
    visibility TEXT NOT NULL,
    remote_video_id TEXT,
    remote_url TEXT,
    idempotency_key TEXT NOT NULL,
    media_checksum_sha256 TEXT NOT NULL,
    metadata_json TEXT DEFAULT '{}',
    receipt_json TEXT DEFAULT '{}',
    scheduled_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_publish_job ON publish_records(job_id);
CREATE INDEX IF NOT EXISTS idx_publish_idempotency ON publish_records(idempotency_key);

CREATE TABLE IF NOT EXISTS publish_attempts (
    attempt_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    publish_request_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    status TEXT NOT NULL,
    error_type TEXT,
    error_message TEXT,
    details_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_publish_attempts_job ON publish_attempts(job_id);
CREATE INDEX IF NOT EXISTS idx_publish_attempts_req ON publish_attempts(publish_request_id);

CREATE TABLE IF NOT EXISTS queue_items (
    queue_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    content_id TEXT,
    channel_id TEXT NOT NULL DEFAULT 'default',
    priority INTEGER NOT NULL DEFAULT 2, -- 1=low, 2=normal, 3=high
    status TEXT NOT NULL DEFAULT 'queued', -- queued, running, succeeded, failed, retry_wait, cancelled, blocked, dead_letter
    stage TEXT NOT NULL DEFAULT 'RESEARCH', -- RESEARCH, SCRIPT, VOICE, ASSETS, RENDER, QA, PUBLISH, COMPLETE
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    scheduled_at TEXT,
    started_at TEXT,
    completed_at TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    next_retry_at TEXT,
    last_error TEXT,
    worker_id TEXT,
    lease_expires_at TEXT,
    manifest_id TEXT,
    payload_json TEXT DEFAULT '{}',
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_queue_status_sched ON queue_items(status, scheduled_at, priority);
CREATE INDEX IF NOT EXISTS idx_queue_job ON queue_items(job_id);
CREATE INDEX IF NOT EXISTS idx_queue_lease ON queue_items(lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_queue_channel ON queue_items(channel_id);

CREATE TABLE IF NOT EXISTS batch_manifests (
    manifest_id TEXT PRIMARY KEY,
    name TEXT,
    profile TEXT NOT NULL DEFAULT 'short_vertical',
    total_items INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'submitted',
    raw_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_manifest_status ON batch_manifests(status);

CREATE TABLE IF NOT EXISTS analytics_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    content_id TEXT,
    platform TEXT NOT NULL DEFAULT 'youtube',
    remote_id TEXT NOT NULL,
    window TEXT NOT NULL DEFAULT 'lifetime',
    observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    retrieved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    provider TEXT NOT NULL DEFAULT 'mock',
    is_synthetic INTEGER NOT NULL DEFAULT 0,
    raw_payload_hash TEXT,
    metadata_json TEXT DEFAULT '{}',
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_analytics_snapshots_job ON analytics_snapshots(job_id);
CREATE INDEX IF NOT EXISTS idx_analytics_snapshots_remote ON analytics_snapshots(platform, remote_id);
CREATE INDEX IF NOT EXISTS idx_analytics_snapshots_hash ON analytics_snapshots(raw_payload_hash);
CREATE INDEX IF NOT EXISTS idx_analytics_snapshots_time ON analytics_snapshots(job_id, window, observed_at);

CREATE TABLE IF NOT EXISTS metric_observations (
    observation_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    raw_name TEXT,
    raw_value REAL NOT NULL DEFAULT 0.0,
    normalized_value REAL NOT NULL DEFAULT 0.0,
    unit TEXT NOT NULL DEFAULT 'count',
    metric_type TEXT NOT NULL DEFAULT 'measured',
    observed_at TEXT NOT NULL,
    window TEXT NOT NULL DEFAULT 'lifetime',
    FOREIGN KEY (snapshot_id) REFERENCES analytics_snapshots(snapshot_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_metric_obs_snapshot ON metric_observations(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_metric_obs_name ON metric_observations(metric_name);

CREATE TABLE IF NOT EXISTS derived_metrics (
    derived_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    value REAL NOT NULL DEFAULT 0.0,
    formula TEXT,
    inputs_json TEXT DEFAULT '{}',
    calculated_at TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    FOREIGN KEY (snapshot_id) REFERENCES analytics_snapshots(snapshot_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_derived_metrics_snapshot ON derived_metrics(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_derived_metrics_name ON derived_metrics(metric_name);

-- Milestone 9: Autonomous Ideation & Feedback Loop
CREATE TABLE IF NOT EXISTS autonomy_runs (
    run_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL DEFAULT 'default',
    autonomy_level INTEGER NOT NULL DEFAULT 0,
    strategy_version TEXT DEFAULT 'strat-v1',
    status TEXT NOT NULL DEFAULT 'running', -- running, completed, failed, interrupted
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    signals_discovered INTEGER NOT NULL DEFAULT 0,
    candidates_generated INTEGER NOT NULL DEFAULT 0,
    proposals_created INTEGER NOT NULL DEFAULT 0,
    jobs_queued INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    config_json TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_autonomy_runs_status ON autonomy_runs(status);

CREATE TABLE IF NOT EXISTS trend_signals (
    signal_id TEXT PRIMARY KEY,
    run_id TEXT,
    topic TEXT NOT NULL,
    source TEXT NOT NULL,
    source_url TEXT,
    detected_at TEXT NOT NULL,
    freshness_score REAL NOT NULL DEFAULT 0.8,
    relevance_score REAL NOT NULL DEFAULT 0.8,
    category TEXT DEFAULT 'general',
    confidence REAL NOT NULL DEFAULT 0.8,
    evidence_text TEXT,
    provenance_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES autonomy_runs(run_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_trend_signals_topic ON trend_signals(topic);
CREATE INDEX IF NOT EXISTS idx_trend_signals_run ON trend_signals(run_id);

CREATE TABLE IF NOT EXISTS topic_candidates (
    candidate_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    proposed_topic TEXT NOT NULL,
    angle TEXT,
    hook_hypothesis TEXT,
    content_format TEXT DEFAULT 'short_vertical',
    rationale TEXT,
    supporting_signals_json TEXT DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0.8,
    estimated_effort INTEGER NOT NULL DEFAULT 1,
    duplicate_risk REAL NOT NULL DEFAULT 0.0,
    commercial_relevance REAL NOT NULL DEFAULT 0.5,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES autonomy_runs(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_topic_candidates_run ON topic_candidates(run_id);
CREATE INDEX IF NOT EXISTS idx_topic_candidates_topic ON topic_candidates(proposed_topic);

CREATE TABLE IF NOT EXISTS topic_scores (
    score_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL UNIQUE,
    freshness REAL NOT NULL DEFAULT 0.0,
    relevance REAL NOT NULL DEFAULT 0.0,
    historical_performance_factor REAL NOT NULL DEFAULT 0.5,
    content_novelty REAL NOT NULL DEFAULT 1.0,
    production_effort_factor REAL NOT NULL DEFAULT 0.5,
    duplicate_risk_penalty REAL NOT NULL DEFAULT 0.0,
    total_score REAL NOT NULL DEFAULT 0.0,
    breakdown_json TEXT DEFAULT '{}',
    explanation TEXT,
    calculated_at TEXT NOT NULL,
    FOREIGN KEY (candidate_id) REFERENCES topic_candidates(candidate_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_topic_scores_candidate ON topic_scores(candidate_id);
CREATE INDEX IF NOT EXISTS idx_topic_scores_total ON topic_scores(total_score);

CREATE TABLE IF NOT EXISTS idea_proposals (
    proposal_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    channel_id TEXT DEFAULT 'default',
    candidate_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed', -- proposed, approved, rejected, queued
    decision_reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at TEXT,
    FOREIGN KEY (run_id) REFERENCES autonomy_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (candidate_id) REFERENCES topic_candidates(candidate_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_idea_proposals_status ON idea_proposals(status);
CREATE INDEX IF NOT EXISTS idx_idea_proposals_run ON idea_proposals(run_id);

CREATE TABLE IF NOT EXISTS autonomy_decisions (
    decision_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    action TEXT NOT NULL, -- approve, reject, queue, block
    autonomy_level INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    checks_json TEXT DEFAULT '[]',
    decided_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (proposal_id) REFERENCES idea_proposals(proposal_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_autonomy_decisions_proposal ON autonomy_decisions(proposal_id);

CREATE TABLE IF NOT EXISTS strategy_versions (
    version_id TEXT PRIMARY KEY,
    parent_version_id TEXT,
    status TEXT NOT NULL DEFAULT 'active', -- active, proposed, deprecated
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    niche_weights_json TEXT DEFAULT '{}',
    hook_patterns_json TEXT DEFAULT '[]',
    topic_rules_json TEXT DEFAULT '{}',
    supporting_evidence_json TEXT DEFAULT '[]',
    rationale TEXT
);
CREATE INDEX IF NOT EXISTS idx_strategy_versions_status ON strategy_versions(status);

CREATE TABLE IF NOT EXISTS feedback_observations (
    observation_id TEXT PRIMARY KEY,
    source_job_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    channel_id TEXT NOT NULL DEFAULT 'default',
    topic TEXT NOT NULL,
    observed_views INTEGER NOT NULL DEFAULT 0,
    observed_engagement_rate REAL NOT NULL DEFAULT 0.0,
    performance_tier TEXT NOT NULL DEFAULT 'average', -- top, average, low
    association_note TEXT,
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_feedback_obs_job ON feedback_observations(source_job_id);
CREATE INDEX IF NOT EXISTS idx_feedback_obs_tier ON feedback_observations(performance_tier);
CREATE INDEX IF NOT EXISTS idx_feedback_obs_channel ON feedback_observations(channel_id);

-- Milestone 10: Multi-Channel Scaling & Channel Profiles
CREATE TABLE IF NOT EXISTS channel_profiles (
    channel_id TEXT PRIMARY KEY,
    channel_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active', -- active, disabled
    profile_version TEXT NOT NULL DEFAULT 'v1',
    active_strategy_version_id TEXT NOT NULL DEFAULT 'strat-v1',
    profile_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_channel_profiles_status ON channel_profiles(status);

CREATE TABLE IF NOT EXISTS channel_profile_versions (
    version_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    profile_version TEXT NOT NULL,
    strategy_version_id TEXT NOT NULL,
    profile_snapshot_json TEXT NOT NULL DEFAULT '{}',
    change_summary TEXT DEFAULT 'Initial version',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (channel_id) REFERENCES channel_profiles(channel_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_channel_versions_channel ON channel_profile_versions(channel_id);

CREATE TABLE IF NOT EXISTS channel_daily_quotas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT NOT NULL,
    date_str TEXT NOT NULL, -- YYYY-MM-DD
    queued_count INTEGER NOT NULL DEFAULT 0,
    published_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(channel_id, date_str)
);
CREATE INDEX IF NOT EXISTS idx_channel_quotas_lookup ON channel_daily_quotas(channel_id, date_str);
CREATE INDEX IF NOT EXISTS idx_jobs_channel ON jobs(channel_id);

CREATE TABLE IF NOT EXISTS video_performance_snapshots (
    performance_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT 'youtube',
    remote_id TEXT,
    collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    views INTEGER NOT NULL DEFAULT 0,
    likes INTEGER NOT NULL DEFAULT 0,
    comments INTEGER NOT NULL DEFAULT 0,
    shares INTEGER NOT NULL DEFAULT 0,
    watch_time_seconds REAL NOT NULL DEFAULT 0.0,
    avg_view_duration_seconds REAL NOT NULL DEFAULT 0.0,
    retention_rate REAL,
    ctr REAL,
    impressions INTEGER,
    subscriber_change INTEGER,
    raw_metrics_json TEXT DEFAULT '{}',
    topic TEXT,
    channel_id TEXT,
    hook TEXT,
    duration_sec REAL,
    production_engine TEXT,
    voice_id TEXT,
    visual_motif TEXT,
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_perf_snapshots_job ON video_performance_snapshots(job_id);
CREATE INDEX IF NOT EXISTS idx_perf_snapshots_channel ON video_performance_snapshots(channel_id);
CREATE INDEX IF NOT EXISTS idx_perf_snapshots_collected ON video_performance_snapshots(collected_at);

CREATE TABLE IF NOT EXISTS publish_approvals (
    approval_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    channel_id TEXT NOT NULL DEFAULT 'default',
    status TEXT NOT NULL DEFAULT 'pending', -- pending, approved, rejected
    requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at TEXT,
    decided_by TEXT,
    notes TEXT,
    media_checksum_sha256 TEXT,
    platform TEXT DEFAULT 'youtube',
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_publish_approvals_job ON publish_approvals(job_id);
CREATE INDEX IF NOT EXISTS idx_publish_approvals_status ON publish_approvals(status);

-- Phase 3: Recurring Schedule Orchestration
CREATE TABLE IF NOT EXISTS autonomy_schedules (
    schedule_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL DEFAULT 'default',
    autonomy_level INTEGER NOT NULL DEFAULT 3,
    operation_mode TEXT, -- how a run consumes autonomy_level: level3 | level4 | level3_then_level4 (null -> derived)
    enabled INTEGER NOT NULL DEFAULT 1,
    cadence TEXT NOT NULL DEFAULT 'daily', -- hourly | daily | weekly | weekdays
    days_of_week TEXT DEFAULT '[]', -- JSON list: mon..sun (honored for weekly/weekdays)
    timezone TEXT NOT NULL DEFAULT 'UTC', -- IANA timezone name
    max_items_per_run INTEGER NOT NULL DEFAULT 10,
    dry_run INTEGER NOT NULL DEFAULT 0,
    policy TEXT NOT NULL DEFAULT 'local_only', -- Level 4 production policy tier
    include_learning INTEGER NOT NULL DEFAULT 0, -- opt-in analytics learning stage after Level 4 (off by default)
    next_run_at TEXT,
    last_run_at TEXT,
    last_run_id TEXT,
    last_run_status TEXT,
    total_runs INTEGER NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    leased_by TEXT,
    lease_until TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_schedules_due ON autonomy_schedules(enabled, next_run_at);

CREATE TABLE IF NOT EXISTS autonomy_schedule_runs (
    run_id TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL,
    channel_id TEXT NOT NULL DEFAULT 'default',
    autonomy_level INTEGER NOT NULL,
    cycle_run_id TEXT,
    status TEXT NOT NULL, -- completed | failed | skipped
    error_message TEXT,
    cycle_summary_json TEXT DEFAULT '{}',
    publish_calls INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_schedule_runs_schedule ON autonomy_schedule_runs(schedule_id);

-- Milestone 11: Analytics-Driven Feedback & Strategy Learning run records.
-- Each row is one deterministic, idempotent learning cycle: what analytics it
-- consumed (fingerprint + lineage), what signals it computed, and which bounded
-- strategy version it produced.  Audience learning only: it never drives
-- production or publishing.
CREATE TABLE IF NOT EXISTS learning_runs (
    run_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL DEFAULT 'default',
    input_fingerprint TEXT NOT NULL,
    parent_strategy_version TEXT NOT NULL,
    resulting_strategy_version TEXT,
    status TEXT NOT NULL, -- applied | no_change | insufficient | dry_run | failed
    dry_run INTEGER NOT NULL DEFAULT 0,
    observations_considered INTEGER NOT NULL DEFAULT 0,
    observations_used INTEGER NOT NULL DEFAULT 0,
    observations_excluded INTEGER NOT NULL DEFAULT 0,
    is_synthetic_input INTEGER NOT NULL DEFAULT 0,
    observation_ids_json TEXT DEFAULT '[]',
    category_signals_json TEXT DEFAULT '{}',
    deltas_json TEXT DEFAULT '[]',
    reason TEXT,
    error_message TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_learning_runs_channel ON learning_runs(channel_id);
CREATE INDEX IF NOT EXISTS idx_learning_runs_fingerprint ON learning_runs(channel_id, input_fingerprint, status);
CREATE INDEX IF NOT EXISTS idx_learning_runs_strategy ON learning_runs(resulting_strategy_version);
"""


class DBManager:
    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_path = Path(__file__).resolve().parent.parent.parent / "artifacts" / "autopilot.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(db_path)
        self.conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def init_schema(self) -> None:
        with self._connect() as conn:
            try:
                cur = conn.execute("PRAGMA table_info(publish_attempts)")
                cols = [row["name"] for row in cur.fetchall()]
                if cols and "publish_request_id" not in cols:
                    conn.execute("DROP TABLE IF EXISTS publish_attempts")
            except Exception:
                pass

            # Pre-migration column checks for existing tables so indexes in SCHEMA_SQL succeed
            try:
                cur = conn.execute("PRAGMA table_info(jobs)")
                cols = [row["name"] for row in cur.fetchall()]
                if cols and "channel_id" not in cols:
                    conn.execute("ALTER TABLE jobs ADD COLUMN channel_id TEXT DEFAULT 'default'")
            except Exception:
                pass

            try:
                cur = conn.execute("PRAGMA table_info(autonomy_runs)")
                cols = [row["name"] for row in cur.fetchall()]
                if cols and "strategy_version" not in cols:
                    conn.execute("ALTER TABLE autonomy_runs ADD COLUMN strategy_version TEXT DEFAULT 'strat-v1'")
                if cols and "channel_id" not in cols:
                    conn.execute("ALTER TABLE autonomy_runs ADD COLUMN channel_id TEXT DEFAULT 'default'")
            except Exception:
                pass

            try:
                cur = conn.execute("PRAGMA table_info(queue_items)")
                cols = [row["name"] for row in cur.fetchall()]
                if cols and "channel_id" not in cols:
                    conn.execute("ALTER TABLE queue_items ADD COLUMN channel_id TEXT DEFAULT 'default'")
            except Exception:
                pass

            try:
                cur = conn.execute("PRAGMA table_info(feedback_observations)")
                cols = [row["name"] for row in cur.fetchall()]
                if cols and "channel_id" not in cols:
                    conn.execute("ALTER TABLE feedback_observations ADD COLUMN channel_id TEXT DEFAULT 'default'")
            except Exception:
                pass

            try:
                cur = conn.execute("PRAGMA table_info(idea_proposals)")
                cols = [row["name"] for row in cur.fetchall()]
                if cols and "channel_id" not in cols:
                    conn.execute("ALTER TABLE idea_proposals ADD COLUMN channel_id TEXT DEFAULT 'default'")
            except Exception:
                pass

            try:
                cur = conn.execute("PRAGMA table_info(autonomy_schedules)")
                cols = [row["name"] for row in cur.fetchall()]
                if cols and "operation_mode" not in cols:
                    conn.execute("ALTER TABLE autonomy_schedules ADD COLUMN operation_mode TEXT")
                if cols and "include_learning" not in cols:
                    conn.execute("ALTER TABLE autonomy_schedules ADD COLUMN include_learning INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass

            # v11: publish approval loop — bind approved artifacts/platform to approval records
            try:
                cur = conn.execute("PRAGMA table_info(publish_approvals)")
                cols = [row["name"] for row in cur.fetchall()]
                if cols and "media_checksum_sha256" not in cols:
                    conn.execute("ALTER TABLE publish_approvals ADD COLUMN media_checksum_sha256 TEXT")
                if cols and "platform" not in cols:
                    conn.execute("ALTER TABLE publish_approvals ADD COLUMN platform TEXT DEFAULT 'youtube'")
            except Exception:
                pass

            conn.executescript(SCHEMA_SQL)

            # Record/upgrade version
            conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (?)", (DB_SCHEMA_VERSION,))
            conn.commit()

    def list_jobs_by_status(self, status: str, limit: int = 100) -> list[dict]:
        """List jobs in a given workflow status, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE UPPER(status) = UPPER(?) ORDER BY updated_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def count_jobs_by_status(self, status: str) -> int:
        """Count jobs in a given workflow status."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE UPPER(status) = UPPER(?)",
                (status,),
            ).fetchone()
            return int(row["n"]) if row else 0

    def create_job(self, job_id: str, channel_id: str = "default", topic: str = "", idempotency_key: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO jobs (job_id, channel_id, topic, status, idempotency_key) VALUES (?, ?, ?, ?, ?)",
                (job_id, channel_id, topic, "IDEA", idempotency_key),
            )
            conn.commit()

    def update_job_status(self, job_id: str, new_status: str, idempotency_key: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE job_id = ?",
                (new_status, job_id),
            )
            if idempotency_key:
                conn.execute(
                    "UPDATE jobs SET idempotency_key = ? WHERE job_id = ?",
                    (idempotency_key, job_id),
                )
            conn.commit()

    def transition_job(
        self,
        job_id: str,
        to_state: str,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> bool:
        """Atomically transitions a job's status and logs the corresponding workflow event."""
        with self._connect() as conn:
            row = conn.execute("SELECT status FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                return False
            from_state = row["status"]
            conn.execute(
                "UPDATE jobs SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE job_id = ?",
                (to_state, job_id),
            )
            if idempotency_key:
                conn.execute(
                    "UPDATE jobs SET idempotency_key = ? WHERE job_id = ?",
                    (idempotency_key, job_id),
                )
            conn.execute(
                "INSERT INTO workflow_events (job_id, from_state, to_state, reason, idempotency_key) VALUES (?, ?, ?, ?, ?)",
                (job_id, from_state, to_state, reason, idempotency_key),
            )
            conn.commit()
            return True

    def log_event(self, job_id: str, from_state: str, to_state: str, reason: str | None = None, idempotency_key: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO workflow_events (job_id, from_state, to_state, reason, idempotency_key) VALUES (?, ?, ?, ?, ?)",
                (job_id, from_state, to_state, reason, idempotency_key),
            )
            conn.commit()

    def record_artifact(
        self,
        job_id: str,
        artifact_path: str,
        artifact_type: str,
        checksum: str | None = None,
        checksum_sha256: str | None = None,
    ) -> None:
        chk = checksum_sha256 if checksum_sha256 is not None else checksum
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO artifacts (job_id, artifact_path, artifact_type, checksum_sha256) VALUES (?, ?, ?, ?)",
                (job_id, str(artifact_path), artifact_type, chk),
            )
            conn.commit()

    def record_error(self, job_id: str | None, stage: str, error_type: str, message: str, details: dict | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO errors (job_id, stage, error_type, message, details_json) VALUES (?, ?, ?, ?, ?)",
                (job_id, stage, error_type, message, json.dumps(details or {})),
            )
            conn.commit()

    def get_job(self, job_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            return dict(row) if row else None

    def get_events(self, job_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_events WHERE job_id = ? ORDER BY occurred_at",
                (job_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_recent_events(
        self,
        limit: int = 100,
        job_id: str | None = None,
        channel_id: str | None = None,
        since_event_id: int | None = None,
    ) -> list[dict]:
        """Recent workflow events, newest-first, optionally scoped (read-only bridge helper)."""
        conds: list[str] = []
        args: list[object] = []
        if job_id:
            conds.append("e.job_id = ?")
            args.append(job_id)
        if channel_id:
            conds.append("j.channel_id = ?")
            args.append(channel_id)
        if since_event_id is not None:
            conds.append("e.event_id > ?")
            args.append(int(since_event_id))
        where = f" WHERE {' AND '.join(conds)}" if conds else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT e.event_id, e.job_id, e.from_state, e.to_state, e.reason, e.occurred_at,
                       j.channel_id, j.topic
                FROM workflow_events e
                LEFT JOIN jobs j ON j.job_id = e.job_id
                {where}
                ORDER BY e.occurred_at DESC, e.event_id DESC
                LIMIT ?
                """,
                (*args, int(limit)),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_recent_errors(
        self,
        limit: int = 100,
        job_id: str | None = None,
        channel_id: str | None = None,
    ) -> list[dict]:
        """Recent errors, newest-first, optionally scoped (read-only bridge helper)."""
        conds: list[str] = []
        args: list[object] = []
        if job_id:
            conds.append("e.job_id = ?")
            args.append(job_id)
        if channel_id:
            conds.append("j.channel_id = ?")
            args.append(channel_id)
        where = f" WHERE {' AND '.join(conds)}" if conds else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT e.error_id, e.job_id, e.stage, e.error_type, e.message, e.occurred_at,
                       j.channel_id, j.topic
                FROM errors e
                LEFT JOIN jobs j ON j.job_id = e.job_id
                {where}
                ORDER BY e.occurred_at DESC, e.error_id DESC
                LIMIT ?
                """,
                (*args, int(limit)),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_events_for_job(self, job_id: str) -> list[dict]:
        return self.get_events(job_id)

    def get_artifacts_for_job(self, job_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE job_id = ? ORDER BY created_at",
                (job_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_errors_for_job(self, job_id: str | None = None) -> list[dict]:
        with self._connect() as conn:
            if job_id:
                rows = conn.execute("SELECT * FROM errors WHERE job_id = ? ORDER BY occurred_at", (job_id,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM errors ORDER BY occurred_at DESC LIMIT 100").fetchall()
            return [dict(r) for r in rows]

    # Phase 2 research persistence
    def record_voice_artifact(self, job_id: str, content_id: str, segment_id: str, artifact_path: str, provider: str = "mock_tts", model_voice: str = "", duration_sec: float = 0.0, checksum: str | None = None, provenance_json: str = "{}") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO voice_artifacts (job_id, content_id, segment_id, artifact_path, provider, model_voice, duration_sec, checksum_sha256, provenance_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, content_id, segment_id, str(artifact_path), provider, model_voice, duration_sec, checksum, provenance_json),
            )
            conn.commit()

    def get_voice_artifacts_for_job(self, job_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM voice_artifacts WHERE job_id = ? ORDER BY created_at", (job_id,)).fetchall()
            return [dict(r) for r in rows]

    def get_research_request(self, request_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM research_requests WHERE request_id = ?", (request_id,)).fetchone()
            return dict(row) if row else None

    def create_research_request(self, request_id: str, topic: str, language: str = "en", max_sources: int = 10, provider_config: str = "{}") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO research_requests (request_id, topic, language, max_sources, provider_config, status) VALUES (?, ?, ?, ?, ?, ?)",
                (request_id, topic, language, max_sources, provider_config, "pending"),
            )
            conn.commit()

    def update_research_request_status(self, request_id: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE research_requests SET status = ? WHERE request_id = ?", (status, request_id))
            conn.commit()

    def save_research_report(self, report_id: str, request_id: str, topic: str, status: str = "pending", summary: str | None = None, provenance_json: str = "{}") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO research_reports (report_id, request_id, topic, status, summary, provenance_json) VALUES (?, ?, ?, ?, ?, ?)",
                (report_id, request_id, topic, status, summary, provenance_json),
            )
            conn.commit()

    def record_research_evidence(self, evidence_id: str, report_id: str, source_id: str, snippet: str, relevance_score: float = 0.5, status: str = "discovered", provenance_json: str = "{}") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO research_evidence (evidence_id, report_id, source_id, snippet, relevance_score, status, provenance_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (evidence_id, report_id, source_id, snippet, relevance_score, status, provenance_json),
            )
            conn.commit()

    def get_research_report(self, report_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM research_reports WHERE report_id = ?", (report_id,)).fetchone()
            return dict(row) if row else None

    def record_asset_artifact(self, job_id: str, content_id: str, scene_id: str, artifact_path: str, asset_type: str = "image", checksum: str | None = None, provenance_json: str = "{}", license_json: str = "{}") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO asset_artifacts (job_id, content_id, scene_id, artifact_path, asset_type, checksum_sha256, provenance_json, license_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, content_id, scene_id, str(artifact_path), asset_type, checksum, provenance_json, license_json),
            )
            conn.commit()

    def get_asset_artifacts_for_job(self, job_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM asset_artifacts WHERE job_id = ? ORDER BY created_at", (job_id,)).fetchall()
            return [dict(r) for r in rows]

    def get_asset_artifacts_for_scene(self, job_id: str, scene_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM asset_artifacts WHERE job_id = ? AND scene_id = ? ORDER BY created_at", (job_id, scene_id)).fetchall()
            return [dict(r) for r in rows]

    def delete_asset_artifacts_for_job(self, job_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM asset_artifacts WHERE job_id = ?", (job_id,))
            conn.commit()

    def get_evidence_for_report(self, report_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM research_evidence WHERE report_id = ? ORDER BY relevance_score DESC", (report_id,)).fetchall()
            return [dict(r) for r in rows]

    def get_latest_research_report_for_topic(self, topic: str, provider: str | None = None) -> dict | None:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM research_reports WHERE topic = ? AND status = 'completed' ORDER BY created_at DESC", (topic,)).fetchall()
            for r in rows:
                rep = dict(r)
                if provider:
                    prov_json = rep.get("provenance_json") or "{}"
                    try:
                        prov_data = json.loads(prov_json)
                    except Exception:
                        prov_data = {}
                    report_provider = prov_data.get("provider") or ""
                    summary = (rep.get("summary") or "").lower()
                    if not report_provider:
                        if "wikipedia" in summary:
                            report_provider = "wikipedia"
                        elif "mock" in summary:
                            report_provider = "mock_search"

                    if provider == "wikipedia":
                        if report_provider and report_provider != "wikipedia":
                            continue
                        if "synthetic fixture" in summary:
                            continue
                    elif provider in ("mock", "mock_search", "local"):
                        if report_provider not in ("mock", "mock_search", "local", ""):
                            continue
                rep["evidence"] = self.get_evidence_for_report(rep["report_id"])
                if provider == "wikipedia":
                    if "synthetic fixture" in summary:
                        continue
                    if any("synthetic fixture" in (ev.get("snippet") or "").lower() for ev in rep["evidence"]):
                        continue
                    if not rep["evidence"] and report_provider != "wikipedia":
                        continue
                return rep
            return None


    # ------------------------------------------------------------------
    # Phase 5 / M5 QA persistence
    # ------------------------------------------------------------------
    def record_qa_report(self, report: Any) -> None:
        metrics_dict = {m.name: m.value_numeric if m.value_numeric is not None else m.value_text for m in report.metrics}
        receipt_json = report.receipt.model_dump_json() if report.receipt else "{}"
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO qa_runs (report_id, job_id, content_id, status, publish_allowed, qa_version, profile, metrics_json, receipt_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    report.report_id,
                    report.job_id,
                    report.content_id,
                    str(report.status.value if hasattr(report.status, "value") else report.status),
                    1 if report.publish_allowed else 0,
                    report.qa_version,
                    report.profile,
                    json.dumps(metrics_dict),
                    receipt_json,
                    report.created_at,
                ),
            )
            for chk in report.checks:
                conn.execute(
                    "INSERT OR REPLACE INTO qa_checks (check_id, report_id, category, status, severity, measured_value, expected_value, message, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        chk.check_id,
                        report.report_id,
                        chk.category,
                        str(chk.status.value if hasattr(chk.status, "value") else chk.status),
                        str(chk.severity.value if hasattr(chk.severity, "value") else chk.severity),
                        str(chk.measured_value) if chk.measured_value is not None else None,
                        str(chk.expected_value) if chk.expected_value is not None else None,
                        chk.message,
                        chk.timestamp,
                    ),
                )
            for f in report.findings:
                conn.execute(
                    "INSERT OR REPLACE INTO qa_findings (finding_id, report_id, check_id, category, severity, status, message, evidence_json, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        f.finding_id,
                        report.report_id,
                        f.check_id,
                        f.category,
                        str(f.severity.value if hasattr(f.severity, "value") else f.severity),
                        str(f.status.value if hasattr(f.status, "value") else f.status),
                        f.message,
                        json.dumps(f.evidence),
                        f.created_at,
                    ),
                )
            for m in report.metrics:
                conn.execute(
                    "INSERT OR REPLACE INTO qa_metrics (metric_id, report_id, name, category, value_numeric, value_text, unit, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        m.metric_id,
                        report.report_id,
                        m.name,
                        m.category,
                        m.value_numeric,
                        m.value_text,
                        m.unit,
                        str(m.status.value if hasattr(m.status, "value") else m.status),
                    ),
                )
            conn.commit()

    def get_qa_report(self, report_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM qa_runs WHERE report_id = ?", (report_id,)).fetchone()
            return dict(row) if row else None

    def get_qa_reports_for_job(self, job_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM qa_runs WHERE job_id = ? ORDER BY created_at DESC", (job_id,)).fetchall()
            return [dict(r) for r in rows]

    def get_qa_checks(self, report_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM qa_checks WHERE report_id = ?", (report_id,)).fetchall()
            return [dict(r) for r in rows]

    def get_qa_findings(self, report_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM qa_findings WHERE report_id = ?", (report_id,)).fetchall()
            return [dict(r) for r in rows]

    def get_qa_metrics(self, report_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM qa_metrics WHERE report_id = ?", (report_id,)).fetchall()
            return [dict(r) for r in rows]

    def get_job_by_output_checksum(self, checksum: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE checksum_sha256 = ? AND artifact_type = 'media' LIMIT 1", (checksum,)).fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------
    # Milestone 6 / M6 Publishing persistence
    # ------------------------------------------------------------------
    def record_publish_record(
        self,
        job_id: str,
        platform: str = "youtube",
        provider: str = "mock",
        visibility: str = "public",
        remote_video_id: str = "mock_vid",
        idempotency_key: str = "mock_idem",
        media_checksum_sha256: str = "mock_chk",
        status: str = "SUCCESS",
    ) -> str:
        pub_id = f"pub-{job_id}"
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO publish_records (
                    publish_id, job_id, content_id, platform, provider, status,
                    visibility, remote_video_id, remote_url, idempotency_key,
                    media_checksum_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                (
                    pub_id,
                    job_id,
                    job_id,
                    platform,
                    provider,
                    status,
                    visibility,
                    remote_video_id,
                    f"https://example.com/watch?v={remote_video_id}",
                    idempotency_key,
                    media_checksum_sha256,
                ),
            )
            conn.commit()
            return pub_id

    def record_publication(self, receipt: Any, metadata: dict | None = None) -> None:
        receipt_json = receipt.model_dump_json() if hasattr(receipt, "model_dump_json") else json.dumps(receipt)
        meta_json = json.dumps(metadata or {})
        status_str = str(receipt.publication_state.value if hasattr(receipt.publication_state, "value") else receipt.publication_state)
        platform_str = str(receipt.platform.value if hasattr(receipt.platform, "value") else receipt.platform)
        vis_str = str(receipt.visibility.value if hasattr(receipt.visibility, "value") else receipt.visibility)

        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO publish_records (
                    publish_id, job_id, content_id, platform, provider, status,
                    visibility, remote_video_id, remote_url, idempotency_key,
                    media_checksum_sha256, metadata_json, receipt_json,
                    scheduled_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    receipt.receipt_id,
                    receipt.job_id,
                    receipt.content_id,
                    platform_str,
                    receipt.provider,
                    status_str,
                    vis_str,
                    receipt.remote_video_id,
                    receipt.remote_url,
                    receipt.idempotency_key,
                    receipt.render_checksum_sha256,
                    meta_json,
                    receipt_json,
                    receipt.scheduled_time,
                    receipt.published_at if hasattr(receipt, "published_at") else datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()

    def record_publish_attempt(self, attempt: Any, job_id: str) -> None:
        details_json = json.dumps(attempt.details if hasattr(attempt, "details") else {})
        status_str = str(attempt.status.value if hasattr(attempt.status, "value") else attempt.status)
        timestamp_str = attempt.timestamp if hasattr(attempt, "timestamp") else datetime.now(timezone.utc).isoformat()
        req_id = attempt.publish_request_id if hasattr(attempt, "publish_request_id") else ""

        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO publish_attempts (
                    attempt_id, job_id, publish_request_id, attempt_number, status,
                    error_type, error_message, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    attempt.attempt_id,
                    job_id,
                    req_id,
                    attempt.attempt_number,
                    status_str,
                    attempt.error_type,
                    attempt.error_message,
                    details_json,
                    timestamp_str,
                ),
            )
            conn.commit()

    def get_publication_by_id(self, publish_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM publish_records WHERE publish_id = ?", (publish_id,)).fetchone()
            return dict(row) if row else None

    def get_publication_by_idempotency(self, idempotency_key: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM publish_records WHERE idempotency_key = ? AND status = 'SUCCESS' LIMIT 1",
                (idempotency_key,),
            ).fetchone()
            return dict(row) if row else None

    def get_publications_for_job(self, job_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM publish_records WHERE job_id = ? ORDER BY created_at DESC",
                (job_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_publish_attempts(self, publish_request_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM publish_attempts WHERE publish_request_id = ? ORDER BY attempt_number ASC",
                (publish_request_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_publish_attempts_for_job(self, job_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM publish_attempts WHERE job_id = ? ORDER BY created_at ASC",
                (job_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Milestone 7 / M7 — Batch Production & Queue Management
    # ------------------------------------------------------------------
    def enqueue_item(
        self,
        queue_id: str,
        job_id: str,
        content_id: str | None = None,
        priority: int = 2,
        stage: str = "RESEARCH",
        scheduled_at: str | None = None,
        max_attempts: int = 3,
        manifest_id: str | None = None,
        payload: dict | None = None,
        channel_id: str | None = None,
    ) -> str:
        # Ensure job exists in jobs table
        topic = payload.get("topic", "") if payload else ""
        cid = channel_id or (payload.get("channel_id", "default") if payload else "default")
        self.create_job(job_id=job_id, channel_id=cid, topic=topic)
        payload_json = json.dumps(payload or {})
        with self._connect() as conn:
            # Upsert so re-enqueueing a previously cancelled queue_id (e.g. approve -> reject -> re-approve)
            # resurfaces it as a fresh queued item instead of raising a UNIQUE constraint error (P1-06 fix).
            conn.execute(
                """
                INSERT INTO queue_items (
                    queue_id, job_id, content_id, channel_id, priority, status, stage,
                    scheduled_at, max_attempts, manifest_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)
                ON CONFLICT(queue_id) DO UPDATE SET
                    status = 'queued',
                    stage = excluded.stage,
                    priority = excluded.priority,
                    channel_id = excluded.channel_id,
                    content_id = excluded.content_id,
                    payload_json = excluded.payload_json,
                    scheduled_at = excluded.scheduled_at,
                    max_attempts = excluded.max_attempts,
                    manifest_id = excluded.manifest_id,
                    attempt_count = 0,
                    lease_expires_at = NULL,
                    next_retry_at = NULL,
                    last_error = NULL,
                    completed_at = NULL,
                    created_at = CURRENT_TIMESTAMP
                """,
                (
                    queue_id, job_id, content_id, cid, priority, stage,
                    scheduled_at, max_attempts, manifest_id, payload_json,
                ),
            )
            conn.commit()
        return queue_id

    def claim_next_queue_item(self, worker_id: str, lease_duration_sec: float = 300.0) -> dict | None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT queue_id FROM queue_items
                WHERE (status = 'queued' OR (status = 'retry_wait' AND (next_retry_at IS NULL OR datetime(next_retry_at) <= datetime('now'))))
                  AND (scheduled_at IS NULL OR datetime(scheduled_at) <= datetime('now'))
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                conn.commit()
                return None
            
            qid = row["queue_id"]
            conn.execute(
                """
                UPDATE queue_items
                SET status = 'running',
                    worker_id = ?,
                    started_at = CURRENT_TIMESTAMP,
                    lease_expires_at = datetime('now', '+' || ? || ' seconds'),
                    attempt_count = attempt_count + 1
                WHERE queue_id = ?
                """,
                (worker_id, int(lease_duration_sec), qid),
            )
            conn.commit()
            
            item_row = conn.execute("SELECT * FROM queue_items WHERE queue_id = ?", (qid,)).fetchone()
            return dict(item_row) if item_row else None

    def renew_lease(self, queue_id: str, worker_id: str, lease_duration_sec: float = 300.0) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE queue_items
                SET lease_expires_at = datetime('now', '+' || ? || ' seconds')
                WHERE queue_id = ? AND worker_id = ? AND status = 'running'
                """,
                (int(lease_duration_sec), queue_id, worker_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def claim_queue_item(
        self,
        queue_id: str,
        worker_id: str,
        lease_duration_sec: float = 300.0,
    ) -> dict | None:
        """Atomically claims a specific queue item if it is still eligible.

        Only items in ``queued`` (or ``retry_wait`` whose retry window has
        opened) are claimable.  Used by the Level 4 guarded auto-produce cycle
        so a pre-validated job cannot be claimed twice concurrently.
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT queue_id FROM queue_items
                WHERE queue_id = ?
                  AND (status = 'queued'
                       OR (status = 'retry_wait' AND (next_retry_at IS NULL OR datetime(next_retry_at) <= datetime('now'))))
                  AND (scheduled_at IS NULL OR datetime(scheduled_at) <= datetime('now'))
                """,
                (queue_id,),
            ).fetchone()
            if not row:
                conn.commit()
                return None
            conn.execute(
                """
                UPDATE queue_items
                SET status = 'running',
                    worker_id = ?,
                    started_at = CURRENT_TIMESTAMP,
                    lease_expires_at = datetime('now', '+' || ? || ' seconds'),
                    attempt_count = attempt_count + 1
                WHERE queue_id = ?
                """,
                (worker_id, int(lease_duration_sec), queue_id),
            )
            conn.commit()
            item_row = conn.execute("SELECT * FROM queue_items WHERE queue_id = ?", (queue_id,)).fetchone()
            return dict(item_row) if item_row else None

    def list_eligible_queue_items(
        self,
        channel_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Lists claimable queue items: queued, or retry_wait whose backoff has elapsed.

        ``running``/``succeeded``/``blocked``/``cancelled``/``dead_letter`` items
        are never returned so a finished job cannot be re-produced by a Level 4
        cycle (idempotency + duplicate-output protection).
        """
        with self._connect() as conn:
            if channel_id:
                rows = conn.execute(
                    """
                    SELECT * FROM queue_items
                    WHERE channel_id = ?
                      AND (status = 'queued'
                           OR (status = 'retry_wait' AND (next_retry_at IS NULL OR datetime(next_retry_at) <= datetime('now'))))
                      AND (scheduled_at IS NULL OR datetime(scheduled_at) <= datetime('now'))
                    ORDER BY priority DESC, created_at ASC
                    LIMIT ?
                    """,
                    (channel_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM queue_items
                    WHERE (status = 'queued'
                           OR (status = 'retry_wait' AND (next_retry_at IS NULL OR datetime(next_retry_at) <= datetime('now'))))
                      AND (scheduled_at IS NULL OR datetime(scheduled_at) <= datetime('now'))
                    ORDER BY priority DESC, created_at ASC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def update_queue_stage(self, queue_id: str, stage: str, status: str = "running") -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE queue_items SET stage = ?, status = ? WHERE queue_id = ?",
                (stage, status, queue_id),
            )
            conn.commit()

    def complete_queue_item(self, queue_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE queue_items
                SET status = 'succeeded',
                    stage = 'COMPLETE',
                    completed_at = CURRENT_TIMESTAMP,
                    lease_expires_at = NULL
                WHERE queue_id = ?
                """,
                (queue_id,),
            )
            conn.commit()

    def fail_queue_item(
        self,
        queue_id: str,
        error_message: str,
        retryable: bool = False,
        backoff_base_sec: float = 2.0,
    ) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT job_id, stage, attempt_count, max_attempts FROM queue_items WHERE queue_id = ?",
                (queue_id,),
            ).fetchone()
            if not row:
                return "failed"
            job_id = row["job_id"]
            stage = row["stage"] or "UNKNOWN"
            attempt_count = row["attempt_count"]
            max_attempts = row["max_attempts"]

            if retryable and attempt_count < max_attempts:
                error_type = "RETRYABLE_ERROR"
                delay = int(backoff_base_sec * (2 ** max(0, attempt_count - 1)))
                conn.execute(
                    """
                    UPDATE queue_items
                    SET status = 'retry_wait',
                        last_error = ?,
                        next_retry_at = datetime('now', '+' || ? || ' seconds'),
                        lease_expires_at = NULL
                    WHERE queue_id = ?
                    """,
                    (error_message, delay, queue_id),
                )
                conn.execute(
                    "INSERT INTO errors (job_id, stage, error_type, message, details_json) VALUES (?, ?, ?, ?, ?)",
                    (
                        job_id,
                        stage,
                        error_type,
                        error_message,
                        json.dumps({"queue_id": queue_id, "attempt": attempt_count, "max_attempts": max_attempts, "delay_sec": delay}),
                    ),
                )
                conn.commit()
                return "retry_wait"
            elif attempt_count >= max_attempts:
                error_type = "DEAD_LETTER"
                conn.execute(
                    """
                    UPDATE queue_items
                    SET status = 'dead_letter',
                        last_error = ?,
                        completed_at = CURRENT_TIMESTAMP,
                        lease_expires_at = NULL
                    WHERE queue_id = ?
                    """,
                    (error_message, queue_id),
                )
                conn.execute(
                    "INSERT INTO errors (job_id, stage, error_type, message, details_json) VALUES (?, ?, ?, ?, ?)",
                    (
                        job_id,
                        stage,
                        error_type,
                        error_message,
                        json.dumps({"queue_id": queue_id, "attempt": attempt_count, "max_attempts": max_attempts, "exhausted": True}),
                    ),
                )
                conn.commit()
                return "dead_letter"
            else:
                error_type = "FAILED"
                conn.execute(
                    """
                    UPDATE queue_items
                    SET status = 'failed',
                        last_error = ?,
                        completed_at = CURRENT_TIMESTAMP,
                        lease_expires_at = NULL
                    WHERE queue_id = ?
                    """,
                    (error_message, queue_id),
                )
                conn.execute(
                    "INSERT INTO errors (job_id, stage, error_type, message, details_json) VALUES (?, ?, ?, ?, ?)",
                    (
                        job_id,
                        stage,
                        error_type,
                        error_message,
                        json.dumps({"queue_id": queue_id, "attempt": attempt_count, "max_attempts": max_attempts}),
                    ),
                )
                conn.commit()
                return "failed"

    def block_queue_item(self, queue_id: str, reason: str) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT job_id, stage FROM queue_items WHERE queue_id = ?",
                (queue_id,),
            ).fetchone()
            job_id = row["job_id"] if row else None
            stage = (row["stage"] if row else None) or "UNKNOWN"

            conn.execute(
                """
                UPDATE queue_items
                SET status = 'blocked',
                    last_error = ?,
                    completed_at = CURRENT_TIMESTAMP,
                    lease_expires_at = NULL
                WHERE queue_id = ?
                """,
                (reason, queue_id),
            )
            conn.execute(
                "INSERT INTO errors (job_id, stage, error_type, message, details_json) VALUES (?, ?, ?, ?, ?)",
                (
                    job_id,
                    stage,
                    "BLOCKED",
                    reason,
                    json.dumps({"queue_id": queue_id}),
                ),
            )
            conn.commit()

    def update_queue_item_status(self, queue_id: str, status: str, error_message: str | None = None) -> None:
        if status == "succeeded":
            self.complete_queue_item(queue_id)
        elif status == "blocked":
            self.block_queue_item(queue_id, error_message or "Blocked")
        elif status == "cancelled":
            self.cancel_queue_item(queue_id)
        elif status == "failed":
            self.fail_queue_item(queue_id, error_message or "Failed")
        else:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE queue_items SET status = ? WHERE queue_id = ?",
                    (status, queue_id),
                )
                conn.commit()

    def cancel_queue_item(self, queue_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE queue_items
                SET status = 'cancelled',
                    completed_at = CURRENT_TIMESTAMP,
                    lease_expires_at = NULL
                WHERE queue_id = ? AND status IN ('queued', 'retry_wait', 'running')
                """,
                (queue_id,),
            )
            conn.commit()
            return cur.rowcount > 0

    def cancel_all_queued_items(self, status: str = "queued", dry_run: bool = False) -> dict[str, Any]:
        """Safely cancels all queue records whose status is exactly 'queued'.
        
        Preserves auditable cancelled state (sets status='cancelled', completed_at=CURRENT_TIMESTAMP).
        Does not delete database records. Ignores running, succeeded, failed, retry_wait, etc.
        """
        if status != "queued":
            raise ValueError(f"cancel_all_queued_items only supports status='queued', got '{status}'")

        with self._connect() as conn:
            row = conn.execute("SELECT count(*) as cnt FROM queue_items WHERE status = 'queued'").fetchone()
            found_count = row["cnt"] if row else 0

            if dry_run or found_count == 0:
                return {
                    "status": "queued",
                    "found_count": found_count,
                    "cancelled_count": 0,
                    "dry_run": dry_run,
                }

            cur = conn.execute(
                """
                UPDATE queue_items
                SET status = 'cancelled',
                    completed_at = CURRENT_TIMESTAMP,
                    lease_expires_at = NULL
                WHERE status = 'queued'
                """
            )
            conn.commit()
            cancelled_count = cur.rowcount

            return {
                "status": "queued",
                "found_count": found_count,
                "cancelled_count": cancelled_count,
                "dry_run": False,
            }

    def retry_queue_item(self, queue_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE queue_items
                SET status = 'queued',
                    attempt_count = 0,
                    next_retry_at = NULL,
                    last_error = NULL,
                    lease_expires_at = NULL,
                    started_at = NULL,
                    completed_at = NULL
                WHERE queue_id = ? AND status IN ('failed', 'dead_letter', 'cancelled', 'blocked', 'retry_wait')
                """,
                (queue_id,),
            )
            conn.commit()
            return cur.rowcount > 0

    def recover_stale_leases(self, grace_lease_sec: float = 300.0) -> list[str]:
        """Marks expired-lease jobs as retryable so another worker can take them.

        A full ``grace_lease_sec`` window is added to ``next_retry_at`` before the job
        becomes claimable again (P1: prevents an immediately-recovered job from being
        double-executed while the original worker may still be alive on a long stage).
        """
        recovered = []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT queue_id, job_id, stage, attempt_count, max_attempts FROM queue_items
                WHERE status = 'running'
                  AND lease_expires_at IS NOT NULL
                  AND datetime(lease_expires_at) < datetime('now')
                """
            ).fetchall()
            for r in rows:
                qid = r["queue_id"]
                job_id = r["job_id"]
                stage = r["stage"] or "UNKNOWN"
                att = r["attempt_count"]
                m_att = r["max_attempts"]
                if att < m_att:
                    conn.execute(
                        """
                        UPDATE queue_items
                        SET status = 'retry_wait',
                            next_retry_at = datetime('now', '+' || ? || ' seconds'),
                            lease_expires_at = NULL,
                            last_error = 'Stale worker lease recovered'
                        WHERE queue_id = ?
                        """,
                        (int(grace_lease_sec), qid),
                    )
                    conn.execute(
                        "INSERT INTO errors (job_id, stage, error_type, message, details_json) VALUES (?, ?, ?, ?, ?)",
                        (
                            job_id,
                            stage,
                            "STALE_LEASE_RECOVERED",
                            "Worker lease expired while running; rescheduled with backoff",
                            json.dumps({"queue_id": qid, "attempt": att, "max_attempts": m_att}),
                        ),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE queue_items
                        SET status = 'dead_letter',
                            completed_at = CURRENT_TIMESTAMP,
                            lease_expires_at = NULL,
                            last_error = 'Stale worker lease recovered but max attempts reached'
                        WHERE queue_id = ?
                        """,
                        (qid,),
                    )
                    conn.execute(
                        "INSERT INTO errors (job_id, stage, error_type, message, details_json) VALUES (?, ?, ?, ?, ?)",
                        (
                            job_id,
                            stage,
                            "DEAD_LETTER",
                            "Worker lease expired and max attempts reached",
                            json.dumps({"queue_id": qid, "attempt": att, "max_attempts": m_att, "exhausted": True}),
                        ),
                    )
                recovered.append(qid)
            conn.commit()
        return recovered

    def list_queue_items(self, status: str | None = None, limit: int = 50, channel_id: str | None = None) -> list[dict]:
        with self._connect() as conn:
            if status and channel_id:
                rows = conn.execute(
                    "SELECT * FROM queue_items WHERE status = ? AND channel_id = ? ORDER BY priority DESC, created_at ASC LIMIT ?",
                    (status, channel_id, limit),
                ).fetchall()
            elif status:
                rows = conn.execute(
                    "SELECT * FROM queue_items WHERE status = ? ORDER BY priority DESC, created_at ASC LIMIT ?",
                    (status, limit),
                ).fetchall()
            elif channel_id:
                rows = conn.execute(
                    "SELECT * FROM queue_items WHERE channel_id = ? ORDER BY priority DESC, created_at ASC LIMIT ?",
                    (channel_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM queue_items ORDER BY priority DESC, created_at ASC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def get_queue_status_summary(self, channel_id: str | None = None) -> dict:
        """Returns queue status summary, optionally scoped to a specific channel (P1-06 fix)."""
        with self._connect() as conn:
            if channel_id:
                rows = conn.execute(
                    "SELECT status, count(*) as count FROM queue_items WHERE channel_id = ? GROUP BY status",
                    (channel_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT status, count(*) as count FROM queue_items GROUP BY status").fetchall()
            summary = {
                "queued": 0, "running": 0, "retry_wait": 0, "succeeded": 0,
                "failed": 0, "blocked": 0, "cancelled": 0, "dead_letter": 0, "total": 0,
                "active_workers": [],
            }
            for r in rows:
                s = r["status"]
                cnt = r["count"]
                if s in summary:
                    summary[s] = cnt
                summary["total"] += cnt
            
            workers = conn.execute(
                """
                SELECT DISTINCT worker_id FROM queue_items
                WHERE status = 'running'
                  AND lease_expires_at IS NOT NULL
                  AND datetime(lease_expires_at) >= datetime('now')
                """
            ).fetchall()
            summary["active_workers"] = [w["worker_id"] for w in workers if w["worker_id"]]
            return summary

    def get_queue_item(self, queue_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM queue_items WHERE queue_id = ?", (queue_id,)).fetchone()
            if not row:
                return None
            res = dict(row)
            if "payload_json" in res and res["payload_json"]:
                try:
                    res["payload"] = json.loads(res["payload_json"])
                except Exception:
                    res["payload"] = {}
            return res

    def get_queue_item_by_job(self, job_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM queue_items WHERE job_id = ? ORDER BY created_at DESC LIMIT 1", (job_id,)).fetchone()
            if not row:
                return None
            res = dict(row)
            if "payload_json" in res and res["payload_json"]:
                try:
                    res["payload"] = json.loads(res["payload_json"])
                except Exception:
                    res["payload"] = {}
            return res

    def record_batch_manifest(
        self,
        manifest_id: str,
        name: str = "batch_production",
        profile: str = "short_vertical",
        total_items: int = 0,
        status: str = "submitted",
        raw_json: str = "{}",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO batch_manifests (
                    manifest_id, name, profile, total_items, status, raw_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (manifest_id, name, profile, total_items, status, raw_json),
            )
            conn.commit()

    def get_batch_manifest(self, manifest_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM batch_manifests WHERE manifest_id = ?", (manifest_id,)).fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------
    # Milestone 8: Analytics Persistence & Time-Series Operations
    # ------------------------------------------------------------------

    def record_analytics_snapshot(self, snapshot=None, **kwargs) -> str:
        """Persists an AnalyticsSnapshot and its observations + derived metrics in SQLite."""
        if snapshot is None:
            import uuid
            from autopilot.core.contracts import (
                AnalyticsSnapshot,
                AnalyticsProvenance,
                MetricObservation,
                DerivedMetric,
                PerformanceWindow,
            )
            now_iso = datetime.now(timezone.utc).isoformat()
            snap_id = kwargs.get("snapshot_id", f"snap-{uuid.uuid4().hex[:8]}")
            job_id = kwargs.get("job_id", "")
            remote_id = kwargs.get("remote_id", "")
            provider = kwargs.get("provider", "mock")
            is_synthetic = kwargs.get("is_synthetic", False)
            window_raw = kwargs.get("window", PerformanceWindow.WINDOW_24H)
            if isinstance(window_raw, str):
                try:
                    window_val = PerformanceWindow(window_raw)
                except Exception:
                    window_val = PerformanceWindow.WINDOW_24H
            else:
                window_val = window_raw

            raw_metrics = kwargs.get("metrics", {})
            metrics_dict = {}
            for k, v in raw_metrics.items():
                if isinstance(v, (int, float)):
                    metrics_dict[k] = MetricObservation(
                        metric_name=k,
                        raw_name=k,
                        raw_value=float(v),
                        normalized_value=float(v),
                        window=window_val,
                    )
                else:
                    metrics_dict[k] = v

            raw_derived = kwargs.get("derived_metrics", {})
            derived_dict = {}
            for k, v in raw_derived.items():
                if isinstance(v, (int, float)):
                    derived_dict[k] = DerivedMetric(
                        metric_name=k,
                        value=float(v),
                        formula="ratio",
                    )
                else:
                    derived_dict[k] = v

            prov = AnalyticsProvenance(
                provider=provider,
                source="synthetic" if is_synthetic else "api",
                remote_content_id=remote_id,
            )
            snapshot = AnalyticsSnapshot(
                snapshot_id=snap_id,
                job_id=job_id,
                content_id=kwargs.get("content_id", job_id),
                platform=kwargs.get("platform", "youtube"),
                remote_id=remote_id,
                window=window_val,
                observed_at=kwargs.get("observed_at", now_iso),
                retrieved_at=kwargs.get("retrieved_at", now_iso),
                provider=provider,
                metrics=metrics_dict,
                derived_metrics=derived_dict,
                provenance=prov,
                is_synthetic=is_synthetic,
                metadata=kwargs.get("metadata", {}),
            )

        with self._connect() as conn:
            # Check idempotency via raw_response_hash if present
            raw_hash = snapshot.provenance.raw_response_hash if snapshot.provenance else None
            window_val = snapshot.window.value if hasattr(snapshot.window, "value") else str(snapshot.window)

            if raw_hash:
                existing = conn.execute(
                    "SELECT snapshot_id FROM analytics_snapshots WHERE raw_payload_hash = ? AND job_id = ? AND window = ?",
                    (raw_hash, snapshot.job_id, window_val),
                ).fetchone()
                if existing:
                    return existing["snapshot_id"]

            meta_json = json.dumps(snapshot.metadata or {})
            conn.execute(
                """
                INSERT OR REPLACE INTO analytics_snapshots (
                    snapshot_id, job_id, content_id, platform, remote_id, window,
                    observed_at, retrieved_at, provider, is_synthetic, raw_payload_hash, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.job_id,
                    snapshot.content_id or snapshot.job_id,
                    snapshot.platform,
                    snapshot.remote_id,
                    window_val,
                    snapshot.observed_at,
                    snapshot.retrieved_at,
                    snapshot.provider,
                    1 if snapshot.is_synthetic else 0,
                    raw_hash,
                    meta_json,
                ),
            )

            # Insert observations
            for metric in snapshot.metrics.values():
                obs_id = getattr(metric, "observation_id", f"obs-{snapshot.snapshot_id}-{metric.metric_name}")
                m_type = getattr(metric, "metric_type", "measured")
                if hasattr(m_type, "value"):
                    m_type = m_type.value
                w_val = getattr(metric, "window", "lifetime")
                if hasattr(w_val, "value"):
                    w_val = w_val.value
                raw_name = getattr(metric, "raw_name", metric.metric_name)
                val = float(getattr(metric, "raw_value", getattr(metric, "metric_value", 0.0)))
                norm_val = float(getattr(metric, "normalized_value", getattr(metric, "metric_value", 0.0)))
                conn.execute(
                    """
                    INSERT OR REPLACE INTO metric_observations (
                        observation_id, snapshot_id, metric_name, raw_name, raw_value,
                        normalized_value, unit, metric_type, observed_at, window
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        obs_id,
                        snapshot.snapshot_id,
                        metric.metric_name,
                        raw_name,
                        val,
                        norm_val,
                        metric.unit,
                        str(m_type),
                        metric.observed_at,
                        str(w_val),
                    ),
                )

            # Insert derived metrics
            for derived in snapshot.derived_metrics.values():
                der_id = f"der-{snapshot.snapshot_id}-{derived.metric_name}"
                conn.execute(
                    """
                    INSERT OR REPLACE INTO derived_metrics (
                        derived_id, snapshot_id, metric_name, value, formula,
                        inputs_json, calculated_at, confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        der_id,
                        snapshot.snapshot_id,
                        derived.metric_name,
                        float(derived.value),
                        derived.formula,
                        json.dumps(derived.input_metrics or {}),
                        derived.calculated_at,
                        float(derived.confidence),
                    ),
                )

            conn.commit()
            return snapshot.snapshot_id

    def get_analytics_snapshot(self, snapshot_id: str):
        """Reconstructs an AnalyticsSnapshot from SQLite."""
        from autopilot.core.contracts import (
            AnalyticsSnapshot, MetricObservation, DerivedMetric, AnalyticsProvenance,
            PerformanceWindow, MetricType,
        )
        with self._connect() as conn:
            snap_row = conn.execute(
                "SELECT * FROM analytics_snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
            if not snap_row:
                return None

            obs_rows = conn.execute(
                "SELECT * FROM metric_observations WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchall()
            metrics = {}
            for r in obs_rows:
                metrics[r["metric_name"]] = MetricObservation(
                    metric_name=r["metric_name"],
                    raw_name=r["raw_name"] or "",
                    raw_value=float(r["raw_value"]),
                    normalized_value=float(r["normalized_value"]),
                    unit=r["unit"] or "count",
                    metric_type=MetricType(r["metric_type"]),
                    observed_at=r["observed_at"],
                    window=PerformanceWindow(r["window"]),
                )

            der_rows = conn.execute(
                "SELECT * FROM derived_metrics WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchall()
            derived_metrics = {}
            for r in der_rows:
                derived_metrics[r["metric_name"]] = DerivedMetric(
                    metric_name=r["metric_name"],
                    value=float(r["value"]),
                    formula=r["formula"] or "",
                    input_metrics=json.loads(r["inputs_json"] or "{}"),
                    calculated_at=r["calculated_at"],
                    confidence=float(r["confidence"] or 1.0),
                )

            provenance = AnalyticsProvenance(
                provider=snap_row["provider"],
                source="synthetic" if snap_row["is_synthetic"] else "api",
                remote_content_id=snap_row["remote_id"],
                observed_at=snap_row["observed_at"],
                retrieved_at=snap_row["retrieved_at"],
                raw_response_hash=snap_row["raw_payload_hash"],
            )

            return AnalyticsSnapshot(
                snapshot_id=snap_row["snapshot_id"],
                job_id=snap_row["job_id"],
                content_id=snap_row["content_id"],
                platform=snap_row["platform"],
                remote_id=snap_row["remote_id"],
                window=PerformanceWindow(snap_row["window"]),
                observed_at=snap_row["observed_at"],
                retrieved_at=snap_row["retrieved_at"],
                provider=snap_row["provider"],
                metrics=metrics,
                derived_metrics=derived_metrics,
                provenance=provenance,
                is_synthetic=bool(snap_row["is_synthetic"]),
                metadata=json.loads(snap_row["metadata_json"] or "{}"),
            )

    def list_analytics_snapshots_for_job(self, job_id: str):
        """Lists all snapshots for a given job ordered by observed timestamp descending."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT snapshot_id FROM analytics_snapshots WHERE job_id = ? ORDER BY observed_at DESC",
                (job_id,),
            ).fetchall()
            return [s for r in rows if (s := self.get_analytics_snapshot(r["snapshot_id"])) is not None]

    def get_latest_snapshot_for_job(self, job_id: str):
        """Returns the most recent AnalyticsSnapshot for a job, or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT snapshot_id FROM analytics_snapshots WHERE job_id = ? ORDER BY observed_at DESC LIMIT 1",
                (job_id,),
            ).fetchone()
            if row:
                return self.get_analytics_snapshot(row["snapshot_id"])
            return None

    def get_snapshot_by_hash(self, raw_payload_hash: str):
        """Finds an existing snapshot by raw response hash."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT snapshot_id FROM analytics_snapshots WHERE raw_payload_hash = ? ORDER BY observed_at DESC LIMIT 1",
                (raw_payload_hash,),
            ).fetchone()
            if row:
                return self.get_analytics_snapshot(row["snapshot_id"])
            return None

    def get_content_performance(self, job_id: str):
        """Joins job metadata, publication record, and historical analytics snapshots."""
        from autopilot.core.contracts import ContentPerformance
        with self._connect() as conn:
            job_row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not job_row:
                return None

            pub_row = conn.execute(
                "SELECT * FROM publish_records WHERE job_id = ? ORDER BY created_at DESC LIMIT 1",
                (job_id,),
            ).fetchone()

            snapshots = self.list_analytics_snapshots_for_job(job_id)
            latest = snapshots[0] if snapshots else None

            return ContentPerformance(
                job_id=job_id,
                content_id=pub_row["content_id"] if pub_row else job_id,
                topic=job_row["topic"],
                render_checksum_sha256=pub_row["media_checksum_sha256"] if pub_row else None,
                publication_receipt_id=pub_row["publish_id"] if pub_row else None,
                platform=pub_row["platform"] if pub_row else (latest.platform if latest else None),
                remote_id=pub_row["remote_video_id"] if pub_row else (latest.remote_id if latest else None),
                published_at=pub_row["created_at"] if pub_row else None,
                latest_snapshot=latest,
                snapshot_history=snapshots,
            )

    # ------------------------------------------------------------------
    # Milestone 9: Autonomous Ideation & Feedback Loop Persistence
    # ------------------------------------------------------------------

    def record_autonomy_run(
        self,
        run_id: str,
        autonomy_level: int,
        strategy_version: str = "strat-v1",
        config_json: str = "{}",
        channel_id: str = "default",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO autonomy_runs (
                    run_id, channel_id, autonomy_level, strategy_version, status, started_at, config_json
                ) VALUES (?, ?, ?, ?, 'running', CURRENT_TIMESTAMP, ?)
                """,
                (run_id, channel_id, autonomy_level, strategy_version, config_json),
            )
            conn.commit()

    def update_autonomy_run(
        self,
        run_id: str,
        status: Optional[str] = None,
        signals_discovered: Optional[int] = None,
        candidates_generated: Optional[int] = None,
        proposals_created: Optional[int] = None,
        jobs_queued: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> None:
        with self._connect() as conn:
            updates = []
            params = []
            if status:
                updates.append("status = ?")
                params.append(status)
                if status in ("completed", "failed", "interrupted"):
                    updates.append("completed_at = CURRENT_TIMESTAMP")
            if signals_discovered is not None:
                updates.append("signals_discovered = ?")
                params.append(signals_discovered)
            if candidates_generated is not None:
                updates.append("candidates_generated = ?")
                params.append(candidates_generated)
            if proposals_created is not None:
                updates.append("proposals_created = ?")
                params.append(proposals_created)
            if jobs_queued is not None:
                updates.append("jobs_queued = ?")
                params.append(jobs_queued)
            if error_message is not None:
                updates.append("error_message = ?")
                params.append(error_message)

            if updates:
                sql = f"UPDATE autonomy_runs SET {', '.join(updates)} WHERE run_id = ?"
                params.append(run_id)
                conn.execute(sql, tuple(params))
                conn.commit()

    def get_autonomy_run(self, run_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM autonomy_runs WHERE run_id = ?", (run_id,)).fetchone()
            return dict(row) if row else None

    def list_autonomy_runs(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM autonomy_runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

    # =====================================================================
    # Phase 3: Recurring schedule persistence & atomic orchestration
    # =====================================================================

    @staticmethod
    def _coerce_dt(value: str) -> datetime:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)

    def create_schedule(
        self,
        schedule_id: str,
        channel_id: str = "default",
        autonomy_level: int = 3,
        operation_mode: Optional[str] = None,
        cadence: str = "daily",
        timezone_name: str = "UTC",
        days_of_week: Optional[list] = None,
        max_items_per_run: int = 10,
        dry_run: bool = False,
        policy: str = "local_only",
        enabled: bool = True,
        include_learning: bool = False,
        next_run_at: Optional[str] = None,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
    ) -> bool:
        """Insert a schedule. Returns True if newly inserted, False if the id already existed."""
        now = datetime.now(timezone.utc).isoformat()
        created_at = created_at or now
        updated_at = updated_at or now
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO autonomy_schedules (
                    schedule_id, channel_id, autonomy_level, operation_mode, enabled, cadence, days_of_week,
                    timezone, max_items_per_run, dry_run, policy, include_learning, next_run_at,
                    last_run_at, last_run_id, last_run_status, total_runs, consecutive_failures,
                    leased_by, lease_until, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, 0, 0, NULL, NULL, ?, ?)
                """,
                (
                    schedule_id,
                    channel_id,
                    int(autonomy_level),
                    operation_mode,
                    1 if enabled else 0,
                    cadence,
                    json.dumps(days_of_week or []),
                    timezone_name,
                    int(max_items_per_run),
                    1 if dry_run else 0,
                    policy,
                    1 if include_learning else 0,
                    next_run_at,
                    created_at,
                    updated_at,
                ),
            )
            conn.commit()
            return cur.rowcount > 0

    def get_schedule(self, schedule_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM autonomy_schedules WHERE schedule_id = ?", (schedule_id,)).fetchone()
            return dict(row) if row else None

    def list_schedules(self, channel_id: Optional[str] = None, enabled: Optional[bool] = None) -> list[dict]:
        where, params = [], []
        if channel_id is not None:
            where.append("channel_id = ?")
            params.append(channel_id)
        if enabled is not None:
            where.append("enabled = ?")
            params.append(1 if enabled else 0)
        sql = "SELECT * FROM autonomy_schedules"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY datetime(next_run_at) ASC, schedule_id ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def update_schedule(self, schedule_id: str, **fields) -> bool:
        """Update allowed schedule columns; always refreshes updated_at."""
        allowed = {
            "channel_id", "autonomy_level", "operation_mode", "enabled", "cadence", "days_of_week",
            "timezone", "max_items_per_run", "dry_run", "policy", "include_learning", "next_run_at",
        }
        sets = ["updated_at = ?"]
        params: list = [datetime.now(timezone.utc).isoformat()]
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"Unsupported schedule field: {key}")
            sets.append(f"{key} = ?")
            if isinstance(value, bool):
                params.append(1 if value else 0)
            elif isinstance(value, (list, dict)):
                params.append(json.dumps(value))
            else:
                params.append(value)
        params.append(schedule_id)
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE autonomy_schedules SET {', '.join(sets)} WHERE schedule_id = ?", params
            )
            conn.commit()
            return cur.rowcount > 0

    def delete_schedule(self, schedule_id: str) -> bool:
        with self._connect() as conn:
            conn.execute("DELETE FROM autonomy_schedule_runs WHERE schedule_id = ?", (schedule_id,))
            cur = conn.execute("DELETE FROM autonomy_schedules WHERE schedule_id = ?", (schedule_id,))
            conn.commit()
            return cur.rowcount > 0

    def count_due_schedules(self, now_iso: Optional[str] = None) -> int:
        now_iso = now_iso or datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT count(*) as cnt FROM autonomy_schedules
                WHERE enabled = 1 AND next_run_at IS NOT NULL
                  AND datetime(next_run_at) <= datetime(?)
                  AND (lease_until IS NULL OR datetime(lease_until) < datetime(?))
                """,
                (now_iso, now_iso),
            ).fetchone()
            return int(row["cnt"])

    def claim_due_schedule(self, worker_id: str, lease_duration_sec: int = 300, now_iso: Optional[str] = None) -> Optional[dict]:
        """Atomically claim the oldest due, enabled, un-leased schedule.

        Overlap protection relies on this atomic BEGIN IMMEDIATE claim plus the
        lease fields; concurrent runners can never claim the same schedule.
        """
        now_iso = now_iso or datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    """
                    SELECT * FROM autonomy_schedules
                    WHERE enabled = 1 AND next_run_at IS NOT NULL
                      AND datetime(next_run_at) <= datetime(?)
                      AND (lease_until IS NULL OR datetime(lease_until) < datetime(?))
                    ORDER BY datetime(next_run_at) ASC, schedule_id ASC
                    LIMIT 1
                    """,
                    (now_iso, now_iso),
                ).fetchone()
                if row is None:
                    conn.rollback()
                    return None
                lease_until = (self._coerce_dt(now_iso) + timedelta(seconds=int(lease_duration_sec))).isoformat()
                conn.execute(
                    "UPDATE autonomy_schedules SET leased_by = ?, lease_until = ?, updated_at = ? WHERE schedule_id = ?",
                    (worker_id, lease_until, now_iso, row["schedule_id"]),
                )
                conn.commit()
                return dict(row)
            except Exception:
                conn.rollback()
                raise

    def claim_schedule_by_id(
        self,
        schedule_id: str,
        worker_id: str,
        lease_duration_sec: int = 300,
        now_iso: Optional[str] = None,
    ) -> Optional[dict]:
        """Atomically claim a specific schedule (used by run-now) if not leased."""
        now_iso = now_iso or datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM autonomy_schedules WHERE schedule_id = ?", (schedule_id,)
                ).fetchone()
                if row is None:
                    conn.rollback()
                    return None
                if row["lease_until"] and self._coerce_dt(row["lease_until"]) >= self._coerce_dt(now_iso):
                    conn.rollback()
                    return None
                if row["enabled"] != 1:
                    conn.rollback()
                    return None
                lease_until = (self._coerce_dt(now_iso) + timedelta(seconds=int(lease_duration_sec))).isoformat()
                conn.execute(
                    "UPDATE autonomy_schedules SET leased_by = ?, lease_until = ?, updated_at = ? WHERE schedule_id = ?",
                    (worker_id, lease_until, now_iso, schedule_id),
                )
                conn.commit()
                return dict(row)
            except Exception:
                conn.rollback()
                raise

    def complete_schedule_run(
        self,
        schedule_id: str,
        worker_id: str,
        run_id: str,
        channel_id: str,
        autonomy_level: int,
        cycle_run_id: Optional[str],
        run_status: str,
        error_message: Optional[str],
        cycle_summary_json: str,
        publish_calls: int,
        next_run_at: str,
        started_at: str,
        completed_at: str,
        consecutive_failures: int,
    ) -> bool:
        """Record a schedule run and advance/release the schedule in one transaction.

        Idempotent on run_id (INSERT OR IGNORE) and safe on re-release: the schedule
        UPDATE only matches while the caller still holds the lease, so a duplicate
        call after release is a no-op (restart safety, no double-run accounting).
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO autonomy_schedule_runs (
                        run_id, schedule_id, channel_id, autonomy_level, cycle_run_id, status,
                        error_message, cycle_summary_json, publish_calls, started_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id, schedule_id, channel_id, int(autonomy_level), cycle_run_id, run_status,
                        error_message, cycle_summary_json, int(publish_calls), started_at, completed_at,
                    ),
                )
                upd = conn.execute(
                    """
                    UPDATE autonomy_schedules
                    SET next_run_at = ?, last_run_at = ?, last_run_id = ?, last_run_status = ?,
                        total_runs = total_runs + 1, consecutive_failures = ?,
                        leased_by = NULL, lease_until = NULL, updated_at = ?
                    WHERE schedule_id = ? AND leased_by = ?
                    """,
                    (
                        next_run_at, completed_at, run_id, run_status, int(consecutive_failures),
                        completed_at, schedule_id, worker_id,
                    ),
                )
                conn.commit()
                return upd.rowcount > 0
            except Exception:
                conn.rollback()
                raise

    def list_schedule_runs(self, schedule_id: str, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM autonomy_schedule_runs WHERE schedule_id = ? ORDER BY started_at DESC LIMIT ?",
                (schedule_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def record_trend_signal(self, signal, run_id: Optional[str] = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO trend_signals (
                    signal_id, run_id, topic, source, source_url, detected_at,
                    freshness_score, relevance_score, category, confidence,
                    evidence_text, provenance_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.signal_id,
                    run_id,
                    signal.topic,
                    signal.source,
                    signal.source_url,
                    signal.detected_at,
                    signal.freshness_score,
                    signal.relevance_score,
                    signal.category,
                    signal.confidence,
                    signal.evidence_text,
                    signal.provenance.model_dump_json() if signal.provenance else "{}",
                ),
            )
            conn.commit()

    def get_trend_signals_for_run(self, run_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM trend_signals WHERE run_id = ? ORDER BY freshness_score DESC", (run_id,)).fetchall()
            return [dict(r) for r in rows]

    def record_topic_candidate(self, candidate) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO topic_candidates (
                    candidate_id, run_id, proposed_topic, angle, hook_hypothesis,
                    content_format, rationale, supporting_signals_json,
                    confidence, estimated_effort, duplicate_risk, commercial_relevance
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.candidate_id,
                    candidate.run_id,
                    candidate.proposed_topic,
                    candidate.angle,
                    candidate.hook_hypothesis,
                    candidate.content_format,
                    candidate.rationale,
                    json.dumps(candidate.supporting_signal_ids),
                    candidate.confidence,
                    candidate.estimated_effort,
                    candidate.duplicate_risk,
                    candidate.commercial_relevance,
                ),
            )
            conn.commit()

    def get_topic_candidate(self, candidate_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM topic_candidates WHERE candidate_id = ?", (candidate_id,)).fetchone()
            return dict(row) if row else None

    def get_topic_candidates_for_run(self, run_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM topic_candidates WHERE run_id = ?", (run_id,)).fetchall()
            return [dict(r) for r in rows]

    def record_topic_score(self, score) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO topic_scores (
                    score_id, candidate_id, freshness, relevance,
                    historical_performance_factor, content_novelty,
                    production_effort_factor, duplicate_risk_penalty,
                    total_score, breakdown_json, explanation, calculated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    score.score_id,
                    score.candidate_id,
                    score.freshness,
                    score.relevance,
                    score.historical_performance_factor,
                    score.content_novelty,
                    score.production_effort_factor,
                    score.duplicate_risk_penalty,
                    score.total_score,
                    json.dumps(score.breakdown),
                    score.explanation,
                    score.calculated_at,
                ),
            )
            conn.commit()

    def get_topic_score(self, candidate_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM topic_scores WHERE candidate_id = ?", (candidate_id,)).fetchone()
            return dict(row) if row else None

    def record_idea_proposal(self, proposal) -> str:
        if proposal.candidate:
            self.record_topic_candidate(proposal.candidate)
        if proposal.score:
            self.record_topic_score(proposal.score)

        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO idea_proposals (
                    proposal_id, run_id, channel_id, candidate_id, status, decision_reason, created_at, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal.proposal_id,
                    proposal.run_id,
                    proposal.channel_id or "default",
                    proposal.candidate.candidate_id if proposal.candidate else None,
                    proposal.status.value if hasattr(proposal.status, "value") else str(proposal.status),
                    proposal.decision.reason if proposal.decision else None,
                    proposal.created_at,
                    proposal.decided_at,
                ),
            )
            conn.commit()

        if proposal.decision:
            proposal.decision.proposal_id = proposal.proposal_id
            self.record_autonomy_decision(proposal.decision)

        return proposal.proposal_id

    def update_proposal_status(self, proposal_id: str, status: str, reason: Optional[str] = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE idea_proposals
                SET status = ?, decision_reason = COALESCE(?, decision_reason), decided_at = CURRENT_TIMESTAMP
                WHERE proposal_id = ?
                """,
                (status, reason, proposal_id),
            )
            conn.commit()

    def get_idea_proposal(self, proposal_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT p.*, c.proposed_topic, c.angle, c.hook_hypothesis, c.content_format,
                       s.total_score, s.breakdown_json, s.explanation
                FROM idea_proposals p
                JOIN topic_candidates c ON p.candidate_id = c.candidate_id
                LEFT JOIN topic_scores s ON p.candidate_id = s.candidate_id
                WHERE p.proposal_id = ?
                """,
                (proposal_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_idea_proposals(self, status: Optional[str] = None, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            query = """
                SELECT p.*, c.proposed_topic, c.angle, c.hook_hypothesis, c.content_format,
                       s.total_score, s.breakdown_json, s.explanation
                FROM idea_proposals p
                JOIN topic_candidates c ON p.candidate_id = c.candidate_id
                LEFT JOIN topic_scores s ON p.candidate_id = s.candidate_id
            """
            params = []
            if status:
                query += " WHERE p.status = ?"
                params.append(status)
            query += " ORDER BY p.created_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, tuple(params)).fetchall()
            return [dict(r) for r in rows]

    def record_autonomy_decision(self, decision) -> None:
        with self._connect() as conn:
            checks_json = json.dumps([c.model_dump() if hasattr(c, "model_dump") else dict(c) for c in decision.checks])
            action_val = decision.action.value if hasattr(decision.action, "value") else str(decision.action)
            conn.execute(
                """
                INSERT OR REPLACE INTO autonomy_decisions (
                    decision_id, proposal_id, action, autonomy_level, reason, checks_json, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.decision_id,
                    decision.proposal_id,
                    action_val,
                    decision.autonomy_level,
                    decision.reason,
                    checks_json,
                    decision.decided_at,
                ),
            )
            conn.commit()

    def get_decision_for_proposal(self, proposal_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM autonomy_decisions WHERE proposal_id = ?", (proposal_id,)).fetchone()
            return dict(row) if row else None

    def record_strategy_version(self, strategy) -> None:
        with self._connect() as conn:
            status_val = strategy.status.value if hasattr(strategy.status, "value") else str(strategy.status)
            if status_val == "active":
                # Deprecate existing active strategies
                conn.execute("UPDATE strategy_versions SET status = 'deprecated' WHERE status = 'active'")

            conn.execute(
                """
                INSERT OR REPLACE INTO strategy_versions (
                    version_id, parent_version_id, status, created_at,
                    niche_weights_json, hook_patterns_json, topic_rules_json,
                    supporting_evidence_json, rationale
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    strategy.version_id,
                    strategy.parent_version_id,
                    status_val,
                    strategy.created_at,
                    json.dumps(strategy.niche_weights),
                    json.dumps(strategy.hook_patterns),
                    json.dumps(strategy.topic_rules),
                    json.dumps(strategy.supporting_evidence_ids),
                    strategy.rationale,
                ),
            )
            conn.commit()

    def get_active_strategy(self):
        from autopilot.core.contracts import StrategyVersion, StrategyStatus
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM strategy_versions WHERE status = 'active' ORDER BY created_at DESC LIMIT 1").fetchone()
            if not row:
                # Default baseline strat-v1
                strat = StrategyVersion(version_id="strat-v1")
                self.record_strategy_version(strat)
                return strat
            return StrategyVersion(
                version_id=row["version_id"],
                parent_version_id=row["parent_version_id"],
                created_at=row["created_at"],
                status=StrategyStatus.ACTIVE,
                niche_weights=json.loads(row["niche_weights_json"] or "{}"),
                hook_patterns=json.loads(row["hook_patterns_json"] or "[]"),
                topic_rules=json.loads(row["topic_rules_json"] or "{}"),
                supporting_evidence_ids=json.loads(row["supporting_evidence_json"] or "[]"),
                rationale=row["rationale"] or "",
            )

    def get_strategy_version(self, version_id: str):
        from autopilot.core.contracts import StrategyVersion, StrategyStatus
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM strategy_versions WHERE version_id = ?", (version_id,)).fetchone()
            if not row:
                strat = StrategyVersion(version_id=version_id)
                self.record_strategy_version(strat)
                return strat
            raw_status = row["status"]
            try:
                strat_status = StrategyStatus(raw_status)
            except Exception:
                strat_status = StrategyStatus.ACTIVE
            return StrategyVersion(
                version_id=row["version_id"],
                parent_version_id=row["parent_version_id"],
                created_at=row["created_at"],
                status=strat_status,
                niche_weights=json.loads(row["niche_weights_json"] or "{}"),
                hook_patterns=json.loads(row["hook_patterns_json"] or "[]"),
                topic_rules=json.loads(row["topic_rules_json"] or "{}"),
                supporting_evidence_ids=json.loads(row["supporting_evidence_json"] or "[]"),
                rationale=row["rationale"] or "",
            )

    def set_active_strategy(self, version_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT version_id FROM strategy_versions WHERE version_id = ?", (version_id,)).fetchone()
            if not row:
                return False
            conn.execute("UPDATE strategy_versions SET status = 'deprecated' WHERE status = 'active'")
            conn.execute("UPDATE strategy_versions SET status = 'active' WHERE version_id = ?", (version_id,))
            conn.commit()
            return True

    def get_strategy_ancestor_chain(self, version_id: str, max_depth: int = 64) -> list[str]:
        """Return the ancestor chain [version_id, parent, grandparent, ...].

        Used by learning idempotency: if the strategy produced by a past learning
        run already lies in the ancestry of the currently active strategy, that
        evidence has already been incorporated and re-learning is a no-op.
        Terminates on missing parent, self-reference, or ``max_depth``.
        """
        chain: list[str] = []
        current = version_id
        with self._connect() as conn:
            for _ in range(max(1, int(max_depth))):
                if not current or current in chain:
                    break
                chain.append(current)
                row = conn.execute(
                    "SELECT parent_version_id FROM strategy_versions WHERE version_id = ?",
                    (current,),
                ).fetchone()
                if row is None:
                    break
                current = row["parent_version_id"]
        return chain

    def list_strategy_versions(self, limit: int = 50):
        from autopilot.core.contracts import StrategyVersion, StrategyStatus
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM strategy_versions ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            result = []
            for r in rows:
                raw_status = r["status"]
                try:
                    strat_status = StrategyStatus(raw_status)
                except Exception:
                    strat_status = StrategyStatus.PROPOSED
                result.append(
                    StrategyVersion(
                        version_id=r["version_id"],
                        parent_version_id=r["parent_version_id"],
                        created_at=r["created_at"],
                        status=strat_status,
                        niche_weights=json.loads(r["niche_weights_json"] or "{}"),
                        hook_patterns=json.loads(r["hook_patterns_json"] or "[]"),
                        topic_rules=json.loads(r["topic_rules_json"] or "{}"),
                        supporting_evidence_ids=json.loads(r["supporting_evidence_json"] or "[]"),
                        rationale=r["rationale"] or "",
                    )
                )
            return result

    def record_feedback_observation(self, signal) -> None:
        with self._connect() as conn:
            tier_val = signal.performance_tier.value if hasattr(signal.performance_tier, "value") else str(signal.performance_tier)
            channel_id = getattr(signal, "channel_id", "default")
            conn.execute(
                """
                INSERT OR REPLACE INTO feedback_observations (
                    observation_id, source_job_id, snapshot_id, channel_id, topic,
                    observed_views, observed_engagement_rate, performance_tier,
                    association_note, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.signal_id,
                    signal.job_id,
                    signal.snapshot_id,
                    channel_id,
                    signal.topic,
                    signal.observed_views,
                    signal.observed_engagement_rate,
                    tier_val,
                    signal.association_note,
                    signal.recorded_at,
                ),
            )
            conn.commit()

    def list_feedback_observations(self, limit: int = 50, channel_id: str | None = None) -> list[dict]:
        with self._connect() as conn:
            if channel_id:
                rows = conn.execute(
                    "SELECT * FROM feedback_observations WHERE channel_id = ? ORDER BY recorded_at DESC LIMIT ?",
                    (channel_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM feedback_observations ORDER BY recorded_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    # =====================================================================
    # Milestone 11: Analytics-Driven Feedback & Strategy Learning persistence
    # =====================================================================

    def record_learning_run(self, summary) -> str:
        """Persist one learning run record. Accepts a LearningRunSummary or dict.

        Idempotent on run_id (INSERT OR REPLACE).  The input fingerprint is the
        key used by the idempotency check in ``find_applied_learning_run``.
        """
        data = summary.model_dump(mode="json") if hasattr(summary, "model_dump") else dict(summary)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO learning_runs (
                    run_id, channel_id, input_fingerprint, parent_strategy_version,
                    resulting_strategy_version, status, dry_run,
                    observations_considered, observations_used, observations_excluded,
                    is_synthetic_input, observation_ids_json, category_signals_json,
                    deltas_json, reason, error_message, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data.get("run_id"),
                    data.get("channel_id") or "default",
                    data.get("input_fingerprint") or "",
                    data.get("parent_strategy_version") or "strat-v1",
                    data.get("resulting_strategy_version"),
                    data.get("status") or "insufficient",
                    1 if data.get("dry_run") else 0,
                    int(data.get("observations_considered") or 0),
                    int(data.get("observations_used") or 0),
                    int(data.get("observations_excluded") or 0),
                    1 if data.get("is_synthetic_input") else 0,
                    json.dumps(data.get("observation_ids") or []),
                    json.dumps(data.get("category_signals") or {}),
                    json.dumps(data.get("deltas") or [], default=str),
                    data.get("reason") or "",
                    data.get("error_message"),
                    data.get("started_at"),
                    data.get("completed_at"),
                ),
            )
            conn.commit()
            return data.get("run_id")

    def get_learning_run(self, run_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM learning_runs WHERE run_id = ?", (run_id,)).fetchone()
            return dict(row) if row else None

    def list_learning_runs(self, channel_id: Optional[str] = None, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            if channel_id:
                rows = conn.execute(
                    "SELECT * FROM learning_runs WHERE channel_id = ? ORDER BY started_at DESC LIMIT ?",
                    (channel_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM learning_runs ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def find_applied_learning_run(self, channel_id: str, input_fingerprint: str) -> Optional[dict]:
        """Find the most recent *applied* (persisted, non-dry-run) learning run
        for this channel whose input fingerprint matches.

        Dry-run records are deliberately excluded: a dry run proposes but never
        applies, so it must not block a later real application of the same data.
        """
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM learning_runs
                WHERE channel_id = ? AND input_fingerprint = ? AND status = 'applied'
                ORDER BY started_at DESC LIMIT 1
                """,
                (channel_id, input_fingerprint),
            ).fetchone()
            return dict(row) if row else None

    def list_learning_runs_for_strategy(self, version_id: str, limit: int = 10) -> list[dict]:
        """Learning runs that produced (or proposed) a given strategy version."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM learning_runs WHERE resulting_strategy_version = ? ORDER BY started_at DESC LIMIT ?",
                (version_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_published_jobs(
        self,
        channel_id: Optional[str] = None,
        since_iso: Optional[str] = None,
        published_statuses: Optional[tuple] = None,
    ) -> list[dict]:
        """List jobs that carry an actual publication record.

        Audience-performance learning only ever consumes content that really
        reached the publish stage.  ``published_statuses`` defaults to the
        genuinely-published states (SUCCESS / PUBLISHED); DRY_RUN and FAILED
        records are excluded so unpublished content never masquerades as
        audience evidence.
        """
        statuses = published_statuses if published_statuses is not None else ("SUCCESS", "PUBLISHED")
        placeholders = ",".join("?" for _ in statuses)
        sql = (
            "SELECT pr.job_id, pr.platform, pr.remote_video_id, pr.status, pr.created_at AS published_at, "
            "j.topic, j.channel_id "
            "FROM publish_records pr JOIN jobs j ON j.job_id = pr.job_id "
            f"WHERE pr.status IN ({placeholders})"
        )
        params: list = list(statuses)
        if channel_id:
            sql += " AND j.channel_id = ?"
            params.append(channel_id)
        if since_iso:
            sql += " AND datetime(pr.created_at) >= datetime(?)"
            params.append(since_iso)
        sql += " ORDER BY datetime(pr.created_at) DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [dict(r) for r in rows]

    def resolve_job_category(self, job_id: str) -> tuple[Optional[str], str]:
        """Resolve the niche category for a published job.

        Lineage first (deterministic, exact): the job's queue payload carries
        ``parent_signal_ids`` from ideation, which map to ``trend_signals.category``.

        Fallback (deterministic keyword mapper) for manually-enqueued jobs with
        no trend lineage.  Returns ``(category, source)`` where source is one of
        ``lineage`` | ``keyword`` | ``unresolved``.  Unresolved jobs are never
        silently assigned to an arbitrary category; callers bucket them as
        ``general`` and flag them explicitly.
        """
        with self._connect() as conn:
            # 1. Lineage: job -> queue payload parent_signal_ids -> trend category
            try:
                qrow = conn.execute(
                    "SELECT payload_json FROM queue_items WHERE job_id = ? ORDER BY created_at DESC LIMIT 1",
                    (job_id,),
                ).fetchone()
            except Exception:
                qrow = None
            if qrow:
                try:
                    payload = json.loads(qrow["payload_json"] or "{}")
                except Exception:
                    payload = {}
                signal_ids = payload.get("parent_signal_ids") or payload.get("supporting_signal_ids") or []
                if signal_ids:
                    placeholders = ",".join("?" for _ in signal_ids)
                    srow = conn.execute(
                        f"SELECT category FROM trend_signals WHERE signal_id IN ({placeholders}) "
                        f"AND category IS NOT NULL ORDER BY detected_at DESC LIMIT 1",
                        tuple(signal_ids),
                    ).fetchone()
                    if srow and srow["category"]:
                        return str(srow["category"]).strip().lower(), "lineage"

            # 2. Keyword fallback over the job topic
            try:
                jrow = conn.execute("SELECT topic FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            except Exception:
                jrow = None
            topic = (jrow["topic"] if jrow else "") or ""
            cat = _classify_topic_by_keyword(topic)
            if cat:
                return cat, "keyword"

        return None, "unresolved"

    def count_autonomous_jobs_queued_today(self, channel_id: str | None = None) -> int:
        """Counts queue items enqueued today by autonomous or manual-approved origins.

        Only counts items with active status (queued, running, retry_wait) —
        cancelled/completed/dead-letter items are excluded to prevent
        stale records from inflating the daily budget counter (ISSUE 9 fix).
        The LIKE pattern matches both 'autonomous' (auto-queued) and
        'autonomous_manual_approved' (operator-approved from autonomy pipeline).
        """
        with self._connect() as conn:
            if channel_id:
                row = conn.execute(
                    """
                    SELECT count(*) as cnt FROM queue_items
                    WHERE (payload_json LIKE '%"origin":"autonomous%'
                           OR payload_json LIKE '%"origin": "autonomous%')
                      AND status NOT IN ('cancelled', 'dead_letter', 'succeeded')
                      AND channel_id = ?
                      AND date(created_at) = date('now')
                    """,
                    (channel_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT count(*) as cnt FROM queue_items
                    WHERE (payload_json LIKE '%"origin":"autonomous%'
                           OR payload_json LIKE '%"origin": "autonomous%')
                      AND status NOT IN ('cancelled', 'dead_letter', 'succeeded')
                      AND date(created_at) = date('now')
                    """
                ).fetchone()
            return row["cnt"] if row else 0

    def count_daily_produced_jobs(self, channel_id: str | None = None) -> int:
        """Counts distinct jobs whose queue item succeeded today.

        Used by the Level 4 guarded auto-produce cycle to enforce a strict daily
        production budget (max_jobs_per_day) that covers both in-flight and
        already-completed autonomous jobs.
        """
        with self._connect() as conn:
            if channel_id:
                row = conn.execute(
                    """
                    SELECT count(DISTINCT job_id) as cnt FROM queue_items
                    WHERE status = 'succeeded'
                      AND channel_id = ?
                      AND date(completed_at) = date('now')
                    """,
                    (channel_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT count(DISTINCT job_id) as cnt FROM queue_items
                    WHERE status = 'succeeded'
                      AND date(completed_at) = date('now')
                    """
                ).fetchone()
            return row["cnt"] if row else 0

    def get_recent_topics(self, days: int = 14, channel_id: str | None = None) -> list[str]:
        with self._connect() as conn:
            if channel_id:
                rows = conn.execute(
                    """
                    SELECT DISTINCT topic FROM jobs
                    WHERE topic IS NOT NULL AND topic != ''
                      AND channel_id = ?
                      AND datetime(created_at) >= datetime('now', '-' || ? || ' days')
                    UNION
                    SELECT DISTINCT c.proposed_topic as topic FROM idea_proposals p
                    JOIN topic_candidates c ON p.candidate_id = c.candidate_id
                    JOIN autonomy_runs r ON c.run_id = r.run_id
                    WHERE p.status IN ('approved', 'queued', 'proposed')
                      AND r.channel_id = ?
                      AND datetime(p.created_at) >= datetime('now', '-' || ? || ' days')
                    """,
                    (channel_id, days, channel_id, days),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT DISTINCT topic FROM jobs
                    WHERE topic IS NOT NULL AND topic != ''
                      AND datetime(created_at) >= datetime('now', '-' || ? || ' days')
                    UNION
                    SELECT DISTINCT c.proposed_topic as topic FROM idea_proposals p
                    JOIN topic_candidates c ON p.candidate_id = c.candidate_id
                    WHERE p.status IN ('approved', 'queued', 'proposed')
                      AND datetime(p.created_at) >= datetime('now', '-' || ? || ' days')
                    """,
                    (days, days),
                ).fetchall()
            return [r["topic"] for r in rows if r["topic"]]

    # ------------------------------------------------------------------
    # Milestone 10: Multi-Channel Scaling & Channel Profiles
    # ------------------------------------------------------------------
    def record_channel_profile(self, profile, *args) -> None:
        """Persists or updates active channel profile."""
        with self._connect() as conn:
            if args:
                channel_id = profile
                channel_name = args[0]
                status_val = args[1]
                profile_version = args[2]
                active_strategy_version_id = args[3]
                profile_json = args[4]
            else:
                channel_id = profile.channel_id
                channel_name = profile.channel_name
                status_val = profile.status.value if hasattr(profile.status, "value") else str(profile.status)
                profile_version = profile.profile_version
                active_strategy_version_id = profile.active_strategy_version_id
                profile_json = profile.model_dump_json() if hasattr(profile, "model_dump_json") else json.dumps(profile)
            conn.execute(
                """
                INSERT INTO channel_profiles (
                    channel_id, channel_name, status, profile_version,
                    active_strategy_version_id, profile_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(channel_id) DO UPDATE SET
                    channel_name = excluded.channel_name,
                    status = excluded.status,
                    profile_version = excluded.profile_version,
                    active_strategy_version_id = excluded.active_strategy_version_id,
                    profile_json = excluded.profile_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    channel_id,
                    channel_name,
                    status_val,
                    profile_version,
                    active_strategy_version_id,
                    profile_json,
                ),
            )
            conn.commit()

    def get_channel_profile(self, channel_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM channel_profiles WHERE channel_id = ?", (channel_id,)).fetchone()
            if not row:
                return None
            res = dict(row)
            try:
                res["profile"] = json.loads(res["profile_json"])
            except Exception:
                res["profile"] = {}
            return res

    def list_channel_profiles(self, status: str | None = None) -> list[dict]:
        with self._connect() as conn:
            if status:
                rows = conn.execute("SELECT * FROM channel_profiles WHERE status = ? ORDER BY channel_id ASC", (status,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM channel_profiles ORDER BY channel_id ASC").fetchall()
            result = []
            for r in rows:
                d = dict(r)
                try:
                    d["profile"] = json.loads(d["profile_json"])
                except Exception:
                    d["profile"] = {}
                result.append(d)
            return result

    def update_channel_status(self, channel_id: str, new_status: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE channel_profiles SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE channel_id = ?",
                (new_status, channel_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def record_channel_version(self, version) -> None:
        """Stores immutable snapshot of a channel profile version."""
        with self._connect() as conn:
            snapshot_json = (
                json.dumps(version.profile_snapshot)
                if isinstance(version.profile_snapshot, dict)
                else (version.profile_snapshot if isinstance(version.profile_snapshot, str) else "{}")
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO channel_profile_versions (
                    version_id, channel_id, profile_version, strategy_version_id,
                    profile_snapshot_json, change_summary, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version.version_id,
                    version.channel_id,
                    version.profile_version,
                    version.strategy_version_id,
                    snapshot_json,
                    version.change_summary,
                    version.created_at,
                ),
            )
            conn.commit()

    def get_channel_version(self, channel_id: str, profile_version: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM channel_profile_versions WHERE channel_id = ? AND profile_version = ?",
                (channel_id, profile_version),
            ).fetchone()
            if not row:
                return None
            res = dict(row)
            try:
                res["profile_snapshot"] = json.loads(res["profile_snapshot_json"])
            except Exception:
                res["profile_snapshot"] = {}
            return res

    def list_channel_versions(self, channel_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM channel_profile_versions WHERE channel_id = ? ORDER BY created_at DESC",
                (channel_id,),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                try:
                    d["profile_snapshot"] = json.loads(d["profile_snapshot_json"])
                except Exception:
                    d["profile_snapshot"] = {}
                result.append(d)
            return result

    def record_channel_quota_usage(
        self,
        channel_id: str,
        queued_increment: int = 0,
        published_increment: int = 0,
        date_str: str | None = None,
    ) -> dict:
        if not date_str:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO channel_daily_quotas (channel_id, date_str, queued_count, published_count, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(channel_id, date_str) DO UPDATE SET
                    queued_count = queued_count + excluded.queued_count,
                    published_count = published_count + excluded.published_count,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (channel_id, date_str, queued_increment, published_increment),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM channel_daily_quotas WHERE channel_id = ? AND date_str = ?",
                (channel_id, date_str),
            ).fetchone()
            res = dict(row) if row else {"queued_count": 0, "published_count": 0}
            res["jobs_queued"] = res.get("queued_count", 0)
            res["jobs_published"] = res.get("published_count", 0)
            return res

    def get_channel_quota_usage(self, channel_id: str, date_str: str | None = None) -> dict:
        if not date_str:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM channel_daily_quotas WHERE channel_id = ? AND date_str = ?",
                (channel_id, date_str),
            ).fetchone()
            if row:
                res = dict(row)
            else:
                res = {"channel_id": channel_id, "date_str": date_str, "queued_count": 0, "published_count": 0}
            res["jobs_queued"] = res.get("queued_count", 0)
            res["jobs_published"] = res.get("published_count", 0)
            return res

    def record_video_performance(self, perf: Any) -> None:
        """Persist a VideoPerformance snapshot record (historical snapshots)."""
        data = perf.model_dump() if hasattr(perf, "model_dump") else dict(perf)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO video_performance_snapshots (
                    performance_id, job_id, platform, remote_id, collected_at,
                    views, likes, comments, shares, watch_time_seconds,
                    avg_view_duration_seconds, retention_rate, ctr, impressions,
                    subscriber_change, raw_metrics_json, topic, channel_id,
                    hook, duration_sec, production_engine, voice_id, visual_motif
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data.get("performance_id"),
                    data.get("job_id"),
                    data.get("platform", "youtube"),
                    data.get("remote_id"),
                    data.get("collected_at") or datetime.now(timezone.utc).isoformat(),
                    data.get("views", 0),
                    data.get("likes", 0),
                    data.get("comments", 0),
                    data.get("shares", 0),
                    data.get("watch_time_seconds", 0.0),
                    data.get("avg_view_duration_seconds", 0.0),
                    data.get("retention_rate"),
                    data.get("ctr"),
                    data.get("impressions"),
                    data.get("subscriber_change"),
                    json.dumps(data.get("raw_metrics", {})),
                    data.get("topic"),
                    data.get("channel_id"),
                    data.get("hook"),
                    data.get("duration_sec"),
                    data.get("production_engine"),
                    data.get("voice_id"),
                    data.get("visual_motif"),
                ),
            )
            conn.commit()

    def get_video_performance_history(self, job_id: str) -> list[dict]:
        """Return historical performance snapshots for a job ordered chronologically."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM video_performance_snapshots WHERE job_id = ? ORDER BY collected_at ASC",
                (job_id,),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                if d.get("raw_metrics_json"):
                    try:
                        d["raw_metrics"] = json.loads(d["raw_metrics_json"])
                    except Exception:
                        d["raw_metrics"] = {}
                result.append(d)
            return result

    def get_channel_performance_history(self, channel_id: str) -> list[dict]:
        """Return all performance snapshots for a given channel."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM video_performance_snapshots WHERE channel_id = ? ORDER BY collected_at DESC",
                (channel_id,),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                if d.get("raw_metrics_json"):
                    try:
                        d["raw_metrics"] = json.loads(d["raw_metrics_json"])
                    except Exception:
                        d["raw_metrics"] = {}
                result.append(d)
            return result

    def create_publish_approval(
        self,
        job_id: str,
        channel_id: str = "default",
        notes: str = "",
        media_checksum_sha256: str | None = None,
        platform: str | None = None,
    ) -> str:
        """Create a pending publication approval request for a job.

        Each call inserts a distinct row so a full decision history is kept (the
        previous ``INSERT OR REPLACE`` behaviour overwrote prior approvals).
        """
        approval_id = f"appr-{job_id}-{uuid.uuid4().hex[:8]}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO publish_approvals (
                    approval_id, job_id, channel_id, status, requested_at, notes,
                    media_checksum_sha256, platform
                ) VALUES (?, ?, ?, 'pending', CURRENT_TIMESTAMP, ?, ?, ?)
                """,
                (approval_id, job_id, channel_id, notes, media_checksum_sha256, platform),
            )
            conn.commit()
            return approval_id

    def get_publish_approval(self, job_id: str) -> dict | None:
        """Get the most recent publication approval for a given job."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM publish_approvals WHERE job_id = ? ORDER BY rowid DESC LIMIT 1",
                (job_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_publish_approvals(self, job_id: str | None = None) -> list[dict]:
        """List publication approval history.

        With ``job_id`` returns every approval for that job in insertion order
        (``rowid`` is the SQLite insertion sequence and therefore deterministic
        even when several requests land in the same second); otherwise returns
        the full audit trail newest-first.
        """
        with self._connect() as conn:
            if job_id:
                rows = conn.execute(
                    "SELECT * FROM publish_approvals WHERE job_id = ? ORDER BY rowid ASC",
                    (job_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM publish_approvals ORDER BY rowid DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    def decide_publish_approval(
        self,
        job_id: str,
        approved: bool,
        decided_by: str = "operator",
        notes: str = "",
        artifact_checksum: str | None = None,
        platform: str | None = None,
    ) -> bool:
        """Approve or reject the most recent publication approval for a job.

        Only the latest pending/decided record is touched so prior decisions are
        preserved in the history. When ``artifact_checksum``/``platform`` are
        supplied they are bound to the decision at the same time.
        """
        status = "approved" if approved else "rejected"
        bind_sets = ""
        bind_values: list = []
        if artifact_checksum:
            bind_sets += ", media_checksum_sha256 = ?"
            bind_values.append(artifact_checksum)
        if platform:
            bind_sets += ", platform = ?"
            bind_values.append(platform)
        with self._connect() as conn:
            cur = conn.execute(
                f"""
                UPDATE publish_approvals
                SET status = ?, decided_at = CURRENT_TIMESTAMP, decided_by = ?,
                    notes = COALESCE(notes || '; ', '') || ?
                    {bind_sets}
                WHERE approval_id = (
                    SELECT approval_id FROM publish_approvals
                    WHERE job_id = ?
                    ORDER BY rowid DESC LIMIT 1
                )
                """,
                (status, decided_by, notes, *bind_values, job_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def bind_publish_approval(
        self,
        job_id: str,
        media_checksum_sha256: str,
        platform: str,
    ) -> bool:
        """Lazily bind the published artifact checksum/platform to the latest approval."""
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE publish_approvals
                SET media_checksum_sha256 = COALESCE(media_checksum_sha256, ?),
                    platform = COALESCE(platform, ?)
                WHERE approval_id = (
                    SELECT approval_id FROM publish_approvals
                    WHERE job_id = ?
                    ORDER BY rowid DESC LIMIT 1
                )
                """,
                (media_checksum_sha256, platform, job_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def list_pending_approvals(self, channel_id: str | None = None) -> list[dict]:
        """List all pending publication approvals."""
        with self._connect() as conn:
            if channel_id:
                rows = conn.execute(
                    "SELECT * FROM publish_approvals WHERE status = 'pending' AND channel_id = ? ORDER BY requested_at DESC",
                    (channel_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM publish_approvals WHERE status = 'pending' ORDER BY requested_at DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    def get_publish_health_counts(self) -> dict:
        """Aggregate publication-loop counts for the health report."""
        with self._connect() as conn:
            approvals = conn.execute(
                "SELECT status, count(*) AS n FROM publish_approvals GROUP BY status"
            ).fetchall()
            jobs = conn.execute(
                "SELECT status, count(*) AS n FROM jobs WHERE status IN ('PUBLISHED', 'FAILED_PUBLISH') GROUP BY status"
            ).fetchall()
        counts = {r["status"]: r["n"] for r in approvals}
        job_counts = {r["status"]: r["n"] for r in jobs}
        return {
            "ready": 0,
            "awaiting_approval": counts.get("pending", 0),
            "approved": counts.get("approved", 0),
            "rejected": counts.get("rejected", 0),
            "published": job_counts.get("PUBLISHED", 0),
            "publish_failures": job_counts.get("FAILED_PUBLISH", 0),
        }

    def get_config_value(self, key: str) -> Optional[str]:
        """Read a persisted runtime configuration value (config table).

        Returns None when the key is absent (no row yet), so callers can fall
        back to the env/default configuration. Never stores or returns secrets.
        """
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
                return str(row["value"]) if row else None
        except Exception:  # noqa: BLE001 — missing/migrating schema must not crash readers
            return None

    def set_config_value(self, key: str, value: str) -> None:
        """Persist a runtime configuration override in the config table.

        Idempotent upsert; the row's ``updated_at`` is refreshed so callers can
        report when the value was last changed by the backend.
        """
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO config (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP",
                (key, value),
            )


# Alias for backward compatibility
DatabaseManager = DBManager


# Keyword -> niche category map for the deterministic fallback resolver.
# Keys are lowercase substrings matched against the job topic.  This is the only
# category heuristic in the system; it never invents categories outside this set
# (which mirrors the StrategyVersion.niche_weights keys + channel niche names).
KEYWORD_CATEGORY_MAP: dict[str, str] = {
    "technology": "technology",
    "tech": "technology",
    "software": "technology",
    "ai": "technology",
    "artificial intelligence": "technology",
    "quantum": "technology",
    "cryptograph": "technology",
    "neural": "technology",
    "cyber": "technology",
    "robot": "technology",
    "computer": "technology",
    "engineering": "technology",
    "science": "science",
    "physics": "science",
    "fusion": "science",
    "space": "science",
    "ocean": "science",
    "biology": "science",
    "chemistry": "science",
    "astronom": "science",
    "microbial": "science",
    "climate": "science",
    "history": "history",
    "ancient": "history",
    "archaeolog": "history",
    "bronze age": "history",
    "medieval": "history",
    "empire": "history",
    "finance": "finance",
    "economic": "finance",
    "market": "finance",
    "invest": "finance",
    "money": "finance",
    "business": "finance",
    "stock": "finance",
}


def _classify_topic_by_keyword(topic: str) -> Optional[str]:
    """Deterministic substring matcher; returns a known category or None."""
    if not topic:
        return None
    lowered = topic.lower()
    for keyword, category in KEYWORD_CATEGORY_MAP.items():
        if keyword in lowered:
            return category
    return None


