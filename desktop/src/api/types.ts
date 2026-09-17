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