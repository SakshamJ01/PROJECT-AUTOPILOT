export interface HealthGet {
  status: string;
  python_version: string;
  platform: string;
  ffmpeg: { available: boolean; message?: string; version?: string };
  sqlite: { available: boolean; message?: string; version?: string };
  artifacts_dir: string;
  asset_cache_dir: string;
  db: { path: string; exists: boolean; schema_version: number | null };
  qa_engine: { available: boolean; status: string };
  queue_engine: {
    status: string;
    summary: QueueSummary;
  };
  worker: { status: string };
  scheduler: {
    status: string;
    next_schedule?: string | null;
    queue_times?: Record<string, unknown>;
    channel_schedules?: Record<string, unknown>;
  };
  analytics_engine: { status: string; default_provider: string };
  learning_engine: { status: string };
  autonomy_engine: {
    status: string;
    autonomy_level: number;
    max_ideas_per_cycle: number;
    max_daily_jobs: number;
  };
  autonomy_auto_publish_enabled: boolean;
  publishing: { status: string; counts: Record<string, number> };
  channels: { status: string; total: number; enabled: number };
  providers: { configured: Record<string, string>; youtube: { status: string } };
}

export interface QueueSummary {
  queued: number;
  running: number;
  retry_wait: number;
  succeeded: number;
  failed: number;
  blocked: number;
  cancelled: number;
  dead_letter: number;
  total: number;
  active_workers: Array<{ worker_id: string }>;
}

export interface QueueItem {
  queue_id: string;
  job_id: string;
  channel_id: string | null;
  content_id: string | null;
  topic: string | null;
  stage: string;
  status: string;
  priority: number;
  attempt_count: number;
  scheduled_at: string | null;
  started_at: string | null;
  lease_expires_at: string | null;
  completed_at: string | null;
  payload_json: string | null;
  manifest_id: string | null;
  last_error: string | null;
  worker_id: string | null;
  profile: string | null;
  policy: string | null;
  auto_publish: boolean;
  publish_visibility: string | null;
  providers?: Record<string, string>;
}

export interface QueueList {
  items: QueueItem[];
  summary: QueueSummary;
}

export interface QaCheck {
  check_id: string;
  status: string;
  check_name?: string;
  message?: string;
}

export interface QaFinding {
  finding_id: string;
  severity: string;
  message?: string;
}

export interface QaReport {
  report_id: string;
  job_id: string | null;
  status?: string;
  overall_score?: number | null;
  publish_allowed?: boolean | null;
  created_at?: string | null;
  checks?: QaCheck[];
  findings?: QaFinding[];
}

export interface WorkflowEvent {
  event_id: number;
  job_id: string | null;
  from_state: string | null;
  to_state: string | null;
  reason: string | null;
  occurred_at: string | null;
  channel_id: string | null;
  topic: string | null;
}

export interface ErrorEntry {
  error_id: number;
  job_id: string | null;
  stage: string | null;
  error_type: string | null;
  message: string | null;
  occurred_at: string | null;
  channel_id: string | null;
  topic: string | null;
}

export interface LogEntry {
  timestamp: string | null;
  severity: "info" | "error";
  source: string;
  stage: string | null;
  job_id: string | null;
  channel_id: string | null;
  topic: string | null;
  message: string;
}

export interface JobInspect {
  found: boolean;
  job_id: string;
  job: Record<string, unknown> | null;
  events: WorkflowEvent[];
  artifacts: Record<string, unknown>[];
  errors: ErrorEntry[];
  queue_item: QueueItem | null;
  publications: Record<string, unknown>[];
  qa_reports?: QaReport[];
  stage_order?: string[];
}

export interface ProductionStartRequest {
  topic: string;
  channel?: string;
  policy?: string;
  profile?: string;
  llm_provider?: string;
  research_provider?: string;
  tts_provider?: string;
  asset_provider?: string;
  production_engine?: string;
}

export interface ProductionStartResult {
  queue_id: string;
  job_id: string;
  status: string;
  media_path: string | null;
  qa_status: string | null;
  error: string | null;
}

export interface ProductionActionResult {
  cancelled?: boolean;
  retried?: boolean;
  queue_id: string;
}

// ---------------------------------------------------------------------------
// M3 — autonomy + scheduler control surface
// ---------------------------------------------------------------------------

export type AutonomyMode = "manual" | "assisted" | "autonomous";
export type AutonomyOperationalStatus = "ready" | "running" | "degraded";
export type AutonomyRunMode = "level3" | "level4" | "level3_then_level4";

export interface AutonomyPolicy {
  autonomy_level: number;
  max_ideas_per_cycle: number;
  max_auto_queue_per_cycle: number;
  max_jobs_per_day: number;
  max_concurrent_jobs: number;
  max_queued_jobs: number;
  topic_cooldown_days: number;
  similarity_threshold: number;
  min_score_threshold: number;
  trend_provider: string;
  strategy_influence_scale: number;
}

export interface AutonomyRunRecord {
  run_id: string;
  channel_id: string | null;
  autonomy_level: number;
  strategy_version: string | null;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  signals_discovered: number;
  candidates_generated: number;
  proposals_created: number;
  jobs_queued: number;
  error_message: string | null;
  config_json: string | null;
}

export interface AutonomyActivity {
  recent_runs: AutonomyRunRecord[];
  proposal_counts: {
    proposed: number;
    approved: number;
    rejected: number;
    queued: number;
  };
  ready_to_publish: number;
  published: number;
  produced_today: number;
  queued_today: number;
}

export interface AutonomyStatus {
  level: number;
  level_name: string;
  mode: AutonomyMode;
  operational_status: AutonomyOperationalStatus;
  policy: AutonomyPolicy;
  active_strategy_version: string | null;
  learning: Record<string, unknown>;
  activity: AutonomyActivity;
  scheduler: Record<string, unknown>;
  auto_publish_enabled: boolean;
  publish_boundary: string;
}

export interface AutonomyRunRequest {
  mode: AutonomyRunMode;
  channel_id?: string;
  dry_run?: boolean;
  limit?: number;
  category?: string;
  policy?: string;
}

export interface AutonomyRunResult {
  operation_mode: string;
  level3: Record<string, unknown> | null;
  level4: Record<string, unknown> | null;
  level4_skipped_reason?: string | null;
  status: string;
  run_id: string;
}

export interface AutonomyProposal {
  proposal_id: string;
  run_id: string | null;
  channel_id: string | null;
  candidate_id: string | null;
  status: string;
  decision_reason: string | null;
  created_at: string | null;
  decided_at: string | null;
  proposed_topic: string | null;
  angle: string | null;
  hook_hypothesis: string | null;
  content_format: string | null;
  total_score: number | null;
  breakdown_json: string | null;
  explanation: string | null;
}

export interface AutonomyInspectRun {
  found: boolean;
  run_id?: string;
  run?: AutonomyRunRecord;
  signals?: Record<string, unknown>[];
  candidates?: Record<string, unknown>[];
}

export interface AutonomyInspectProposal {
  found: boolean;
  proposal_id?: string;
  proposal?: AutonomyProposal;
  decision?: Record<string, unknown> | null;
}

export interface ProposalActionResponse {
  status: string;
  proposal_id?: string;
  reason?: string;
  job_id?: string;
  queue_id?: string;
  queue_item_cancelled?: boolean | null;
}

export type ScheduleCadence = "hourly" | "daily" | "weekly" | "weekdays";
export type OperationMode = "level3" | "level4" | "level3_then_level4";

export interface Schedule {
  schedule_id: string;
  channel_id: string;
  autonomy_level: number;
  operation_mode: string | null;
  enabled: boolean;
  cadence: string;
  days_of_week: string[];
  timezone: string;
  max_items_per_run: number;
  dry_run: boolean;
  policy: string;
  include_learning: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  last_run_id: string | null;
  last_run_status: string | null;
  total_runs: number;
  consecutive_failures: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface ScheduleRun {
  run_id: string;
  schedule_id: string;
  channel_id: string;
  autonomy_level: number;
  cycle_run_id: string | null;
  status: string;
  error_message: string | null;
  cycle_summary_json: string | null;
  publish_calls: number;
  started_at: string | null;
  completed_at: string | null;
}

export interface ScheduleInspect {
  schedule: Schedule;
  runs: ScheduleRun[];
}

export interface ScheduleCreateRequest {
  channel_id?: string;
  autonomy_level?: number;
  operation_mode?: OperationMode;
  cadence?: ScheduleCadence;
  timezone?: string;
  days_of_week?: string[];
  max_items_per_run?: number;
  dry_run?: boolean;
  policy?: string;
  include_learning?: boolean;
}

export interface ScheduleUpdateRequest {
  schedule_id: string;
  channel_id?: string;
  autonomy_level?: number;
  operation_mode?: OperationMode;
  cadence?: ScheduleCadence;
  timezone?: string;
  days_of_week?: string[];
  max_items_per_run?: number;
  dry_run?: boolean;
  policy?: string;
  include_learning?: boolean;
  enabled?: boolean;
}

export interface SchedulerStatus {
  state: "running" | "stopped" | "degraded";
  summary: Record<string, unknown>;
  active_executions: number;
}

export interface ScheduleRunSummary {
  run_id: string;
  schedule_id: string;
  channel_id: string;
  autonomy_level: number;
  operation_mode: string;
  status: string;
  cycle_run_id: string | null;
  cycle_status: string | null;
  level3_run_id: string | null;
  level3_status: string | null;
  level3_jobs_queued: number;
  level4_run_id: string | null;
  level4_status: string | null;
  level4_jobs_ready_to_publish: number;
  include_learning: boolean;
  learning_run_id: string | null;
  learning_status: string | null;
  next_run_at: string | null;
  publish_calls: number;
  error_message: string | null;
}

// =====================================================================
// M4 — Publishing
// =====================================================================

export type PublishVisibility = "private" | "unlisted" | "public";

export interface YouTubeAuthStatus {
  status: "authenticated" | "needs_auth" | "unconfigured" | "error";
  authenticated: boolean;
  secrets_present: boolean;
  guidance: string;
}

export interface LearningHealth {
  status: string;
  current_strategy_version: string | null;
  last_learning_run: {
    run_id: string | null;
    status: string | null;
    observations_used: number | null;
    dry_run: boolean | null;
    resulting_strategy_version: string | null;
  } | null;
  error?: string;
}

export interface PublishingHealth {
  counts: Record<string, number>;
  ready_to_publish: number;
  published: number;
  publish_failures: number;
  awaiting_approval: number;
  approved: number;
  rejected: number;
  learning: LearningHealth;
  youtube: YouTubeAuthStatus;
  default_visibility: PublishVisibility;
  autonomy_auto_publish_enabled: boolean;
}

export interface PublishingStatus extends PublishingHealth {
  status: string;
  publish_boundary: string;
}

export interface ReadyPublishItem {
  job_id: string;
  topic: string | null;
  channel_id: string;
  status: string | null;
  approval_status: string | null;
  approval_id: string | null;
  qa_status: string | null;
  qa_publish_allowed: boolean;
  media_checksum_sha256: string | null;
  approved_checksum: string | null;
  checksum_matches: boolean;
  published: boolean;
  remote_video_id: string | null;
  visibility: PublishVisibility | null;
  published_at: string | null;
  idempotency_key: string | null;
  publication_count: number;
}

export interface ReadyList {
  items: ReadyPublishItem[];
  summary: PublishingHealth;
}

export interface PublicationApproval {
  approval_id: string;
  job_id: string;
  channel_id: string;
  status: string;
  requested_at: string;
  decided_at: string | null;
  decided_by: string | null;
  notes: string | null;
  media_checksum_sha256: string | null;
  platform: string | null;
}

export interface PublicationRecord {
  publish_id: string;
  job_id: string;
  content_id: string;
  platform: string;
  provider: string;
  status: string;
  visibility: PublishVisibility;
  remote_video_id: string | null;
  remote_url: string | null;
  idempotency_key: string | null;
  media_checksum_sha256: string | null;
  created_at: string;
}

export interface PublishingInspect {
  found: boolean;
  job_id: string;
  state: string;
  approval_status: string;
  qa_status: string;
  qa_publish_allowed: boolean;
  media_checksum_sha256: string | null;
  approval: PublicationApproval | null;
  approval_history: PublicationApproval[];
  publications: PublicationRecord[];
  publish_attempts: Record<string, unknown>[];
  publishable: boolean;
}

export interface PublishRequestParams {
  job_id: string;
  visibility?: PublishVisibility;
  platform?: string;
  scheduled_time?: string;
  dry_run?: boolean;
  force_retry?: boolean;
  media_path?: string;
}

export interface PublishActionResult {
  success: boolean;
  status: string;
  job_id: string;
  platform: string;
  visibility: PublishVisibility;
  dry_run: boolean;
  remote_video_id: string | null;
  remote_url: string | null;
  idempotency_key: string | null;
  published_at: string | null;
  error_code: string | null;
  error_message: string | null;
  result: Record<string, unknown>;
}

export interface ApprovalActionResult {
  job_id: string;
  status: string;
  platform?: string;
  decided_by: string | null;
  decided_at: string | null;
  media_checksum_sha256?: string | null;
}

// =====================================================================
// M4 — Analytics
// =====================================================================

export interface AnalyticsStatus {
  status: string;
  default_provider: string;
  published_job_count: number;
  jobs_with_snapshots: number;
  snapshot_count: number;
  last_observed_at: string | null;
  has_published_jobs: boolean;
  learning: LearningHealth;
  youtube: YouTubeAuthStatus;
}

export interface MetricObservation {
  observation_id: string | null;
  snapshot_id: string | null;
  metric_name: string;
  raw_name: string;
  raw_value: number;
  normalized_value: number;
  unit: string;
  metric_type: string;
  observed_at: string;
  window: string;
}

export interface DerivedMetric {
  metric_name: string;
  value: number;
  formula: string;
  input_metrics: Record<string, number>;
  calculated_at: string;
  confidence: number;
}

export interface AnalyticsSnapshot {
  snapshot_id: string;
  job_id: string;
  content_id: string | null;
  platform: string;
  remote_id: string;
  window: string;
  observed_at: string;
  retrieved_at: string;
  provider: string;
  metrics: Record<string, MetricObservation>;
  derived_metrics: Record<string, DerivedMetric>;
  is_synthetic: boolean;
  metadata: Record<string, unknown>;
}

export interface AnalyticsSnapshots {
  found: boolean;
  job_id: string;
  topic: string | null;
  platform: string | null;
  remote_id: string | null;
  published_at: string | null;
  publication_receipt_id: string | null;
  snapshots: AnalyticsSnapshot[];
  latest_snapshot: AnalyticsSnapshot | null;
}

export interface AnalyticsReportRow {
  job_id: string;
  topic: string;
  platform: string;
  remote_id: string;
  views: number;
  likes: number;
  comments: number;
  engagement_rate: number;
  snapshots_recorded: number;
  is_synthetic: boolean;
  latest_observed_at: string | null;
}

export interface ChannelAttribution {
  channel_id: string;
  total_videos: number;
  average_views: number;
  top_durations: Array<{ category: string; sample_size: number; avg_views: number; max_views: number }>;
  top_hooks: Array<{ category: string; sample_size: number; avg_views: number; max_views: number }>;
  top_engines: Array<{ category: string; sample_size: number; avg_views: number; max_views: number }>;
  recommendation: string;
  status: string;
  message?: string;
}

export interface AnalyticsReport {
  report: AnalyticsReportRow[];
  channel_attribution: ChannelAttribution | null;
  rows: number;
  note: string | null;
}

export interface AnalyticsSyncParams {
  job_id?: string;
  platform?: string;
  provider?: string;
  window?: string;
  sync_all?: boolean;
  limit?: number;
  dry_run?: boolean;
}

export interface AnalyticsSyncAllResult {
  total_targeted: number;
  synced_count: number;
  dry_run: boolean;
  results: Array<Record<string, unknown>>;
  note?: string;
  synced?: number;
  errors?: number;
  jobs?: Array<Record<string, unknown>>;
}

// =====================================================================
// M4 — Strategy
// =====================================================================

export interface StrategyVersion {
  version_id: string;
  parent_version_id: string | null;
  created_at: string;
  status: string;
  niche_weights: Record<string, number>;
  hook_patterns: string[];
  topic_rules: Record<string, unknown>;
  supporting_evidence_ids: string[];
  rationale: string;
}

export interface StrategyDelta {
  parameter: string;
  old_value: number;
  new_value: number;
  raw_delta: number;
  applied_delta: number;
  sample_size: number;
  confidence: number;
  signal: number;
  source_job_ids: string[];
  reason: string;
}

export interface StrategyBounds {
  min_samples: number;
  min_category_observations: number;
  window_days: number;
  max_weight_delta: number;
  max_params_per_update: number;
  weight_floor: number;
  weight_ceiling: number;
  min_age_days: number;
}

export interface StrategyStatus {
  status: string;
  channel_id: string;
  active_strategy: StrategyVersion | null;
  active_strategy_version: string | null;
  learning: LearningHealth;
  last_learning_run: {
    run_id: string | null;
    status: string | null;
    observations_used: number | null;
    resulting_strategy_version: string | null;
    input_fingerprint: string | null;
    completed_at: string | null;
  } | null;
  bounds: StrategyBounds;
  learning_boundary: string;
}

export interface StrategyShow {
  found: boolean;
  version_id: string | null;
  strategy: StrategyVersion | null;
  ancestry: string[];
  learning_runs: Array<Record<string, unknown>>;
  is_active: boolean;
}

export interface StrategyLearnParams {
  channel_id?: string;
  dry_run?: boolean;
  min_samples?: number;
  window_days?: number;
}

export interface StrategyLearnResult {
  run_id: string;
  channel_id: string;
  status: string;
  dry_run: boolean;
  window_days: number;
  observations_considered: number;
  observations_used: number;
  observations_excluded: number;
  excluded_reasons: Record<string, number>;
  input_fingerprint: string;
  is_synthetic_input: boolean;
  parent_strategy_version: string;
  resulting_strategy_version: string | null;
  reason: string;
  error_message: string | null;
  started_at: string;
  completed_at: string | null;
}

// =====================================================================
// M4 — Autonomous public publishing switch (M6: backend-controlled)
// =====================================================================

export interface AutonomyPublishPrerequisite {
  check: string;
  ok: boolean;
  message: string;
}

export interface AutonomyPublishPrereqs {
  ok: boolean;
  passed: string[];
  failed: string[];
  checks: AutonomyPublishPrerequisite[];
}

export interface AutonomyPublishStatus {
  enabled: boolean;
  state: "ENABLED" | "DISABLED";
  label: string;
  default: boolean;
  controlled_by: string;
  guardrails: string[];
  boundary: string;
  controlled_at: string | null;
  timestamp: string;
}

export interface AutonomyPublishSwitchResult {
  ok: boolean;
  changed: boolean;
  enabled: boolean;
  state: "ENABLED" | "DISABLED";
  label: string;
  default: boolean;
  controlled_by: string;
  guardrails: string[];
  boundary: string;
  reason?: string;
  prerequisites?: AutonomyPublishPrereqs;
  controlled_at: string | null;
  timestamp: string;
}