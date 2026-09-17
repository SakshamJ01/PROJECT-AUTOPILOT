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
}

export interface QueueList {
  items: QueueItem[];
  summary: QueueSummary;
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
}