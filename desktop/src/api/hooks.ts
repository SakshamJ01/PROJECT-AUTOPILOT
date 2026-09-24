import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  engineCall,
  engineStatus,
  POLL,
  type JsonValue,
} from "./engine";
import type {
  AnalyticsReport,
  AnalyticsSnapshots,
  AnalyticsStatus,
  AnalyticsSyncAllResult,
  AnalyticsSyncParams,
  ApprovalActionResult,
  AutonomyPublishStatus,
  AutonomyPublishSwitchResult,
  AutonomyInspectProposal,
  AutonomyInspectRun,
  AutonomyProposal,
  AutonomyRunRequest,
  AutonomyRunResult,
  AutonomyStatus,
  ErrorsListResult,
  HealthGet,
  JobInspect,
  LogEntry,
  ProductionActionResult,
  ProductionEngineStatus,
  ProductionStartResult,
  ProposalActionResponse,
  PublishActionResult,
  PublishRequestParams,
  PublishingInspect,
  PublishingStatus,
  QueueList,
  ReadyList,
  Schedule,
  ScheduleCreateRequest,
  ScheduleInspect,
  ScheduleRunSummary,
  ScheduleUpdateRequest,
  SchedulerStatus,
  StrategyLearnParams,
  StrategyLearnResult,
  StrategyShow,
  StrategyStatus,
  YouTubeAuthStatus,
  WorkflowEvent,
} from "./types";

export function useEngineStatusQuery() {
  return useQuery({
    queryKey: ["engine", "status"],
    queryFn: () => engineStatus(),
    refetchInterval: POLL.systemMs,
  });
}

export function useHealthQuery() {
  return useQuery({
    queryKey: ["engine", "health"],
    queryFn: () => engineCall<HealthGet>("health.get"),
    refetchInterval: POLL.healthMs,
    retry: false,
  });
}

export function useSystemStatusQuery() {
  return useQuery({
    queryKey: ["engine", "system.status"],
    queryFn: () => engineCall<JsonValue>("system.status"),
    refetchInterval: POLL.systemMs,
    retry: false,
  });
}

const TERMINAL_QUEUE_STATUSES = new Set([
  "succeeded",
  "failed",
  "cancelled",
  "blocked",
  "dead_letter",
]);

/** Polls fast while active queue items exist; slows once all are terminal. */
export function useQueueQuery(filters?: { status?: string; search?: string; channel_id?: string }) {
  const status = filters?.status === "all" ? undefined : filters?.status;
  return useQuery({
    queryKey: [
      "engine",
      "queue.list",
      status ?? "all",
      filters?.search ?? "",
      filters?.channel_id ?? "all",
    ],
    queryFn: () =>
      engineCall<QueueList>("queue.list", {
        status,
        search: filters?.search,
        channel_id: filters?.channel_id,
        limit: 200,
      }),
    refetchInterval: (query) => {
      const snapshot = query.state.data as QueueList | undefined;
      if (!snapshot) return POLL.queueMs;
      const hasActive = snapshot.items.some(
        (it) => !TERMINAL_QUEUE_STATUSES.has(it.status),
      );
      return hasActive ? POLL.queueMs : POLL.systemMs;
    },
    retry: false,
  });
}

export function useJobInspectQuery(jobId: string | null) {
  return useQuery({
    queryKey: ["engine", "job.inspect", jobId],
    queryFn: () => engineCall<JobInspect>("job.inspect", { job_id: jobId }),
    refetchInterval: (query) => {
      const inspect = query.state.data as JobInspect | undefined;
      const status = inspect?.queue_item?.status;
      if (!status) return POLL.activityMs;
      return TERMINAL_QUEUE_STATUSES.has(status) ? false : 1500;
    },
    retry: false,
    enabled: jobId !== null,
  });
}

export function useProductionStartMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: {
      topic: string;
      channel?: string;
      policy?: string;
      profile?: string;
      llm_provider?: string;
      research_provider?: string;
      tts_provider?: string;
      asset_provider?: string;
      production_engine?: string;
    }) => engineCall<ProductionStartResult>("production.start", params),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ["engine", "queue.list"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "health"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "job.inspect", data.job_id] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "errors.list"] });
    },
  });
}

export function useProductionCancelMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: { job_id?: string; queue_id?: string }) =>
      engineCall<ProductionActionResult>("production.cancel", params),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["engine", "queue.list"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "health"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "errors.list"] });
    },
  });
}

export function useProductionRetryMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: { job_id?: string; queue_id?: string }) =>
      engineCall<ProductionActionResult>("production.retry", params),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["engine", "queue.list"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "health"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "errors.list"] });
    },
  });
}

export function useProductionEngineStatusQuery(enabled = true) {
  return useQuery({
    queryKey: ["engine", "production.engine.status"],
    queryFn: () => engineCall<ProductionEngineStatus>("production.engine.status"),
    // Probe the API more often while the service is down so readiness is
    // picked up promptly without hammering it while it is healthy.
    refetchInterval: (query) => {
      const status = query.state.data as ProductionEngineStatus | undefined;
      return status?.running ? POLL.systemMs : POLL.engineMs;
    },
    retry: false,
    enabled,
  });
}

export function useProductionEngineEnsureMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => engineCall<ProductionEngineStatus>("production.engine.ensure"),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["engine", "production.engine.status"] });
    },
  });
}

export function useLogsQuery(logParams?: { severity?: string; search?: string }, enabled = true) {
  return useQuery({
    queryKey: [
      "engine",
      "logs.tail",
      logParams?.severity ?? "all",
      logParams?.search ?? "",
    ],
    queryFn: () =>
      engineCall<{ entries: LogEntry[] }>("logs.tail", {
        limit: 100,
        severity: logParams?.severity,
        search: logParams?.search,
      }),
    refetchInterval: POLL.logsMs,
    retry: false,
    enabled,
  });
}

export function useActivityQuery(enabled = true) {
  return useQuery({
    queryKey: ["engine", "events.tail"],
    queryFn: () => engineCall<{ events: WorkflowEvent[] }>("events.tail", { limit: 25 }),
    refetchInterval: POLL.activityMs,
    retry: false,
    enabled,
  });
}

export function useErrorsQuery(filters?: { limit?: number; job_id?: string; channel_id?: string }, enabled = true) {
  return useQuery({
    queryKey: [
      "engine",
      "errors.list",
      filters?.limit ?? 100,
      filters?.job_id ?? "all",
      filters?.channel_id ?? "all",
    ],
    queryFn: () =>
      engineCall<ErrorsListResult>("errors.list", {
        limit: filters?.limit ?? 100,
        job_id: filters?.job_id,
        channel_id: filters?.channel_id,
      }),
    refetchInterval: POLL.logsMs,
    retry: false,
    enabled,
  });
}

// ---------------------------------------------------------------------------
// M3 — autonomy + scheduler
// ---------------------------------------------------------------------------

const ACTIVE_RUN_STATUSES = new Set(["running", "retry_wait"]);

export function useAutonomyStatusQuery() {
  return useQuery({
    queryKey: ["engine", "autonomy.status"],
    queryFn: () => engineCall<AutonomyStatus>("autonomy.status"),
    // Fast while a cycle is executing, slower when idle.
    refetchInterval: (query) => {
      const status = query.state.data as AutonomyStatus | undefined;
      if (!status) return POLL.systemMs;
      const activeRuns = status.activity.recent_runs.some((r) =>
        ACTIVE_RUN_STATUSES.has(r.status),
      );
      if (status.operational_status === "running" || activeRuns) return 2500;
      return POLL.systemMs;
    },
    retry: false,
  });
}

export function useAutonomyRunMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: AutonomyRunRequest) =>
      engineCall<AutonomyRunResult>("autonomy.run", params),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["engine", "autonomy.status"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "autonomy.proposals"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "queue.list"] });
    },
  });
}

export function useAutonomyProposalsQuery(status?: string) {
  return useQuery({
    queryKey: ["engine", "autonomy.proposals", status ?? "all"],
    queryFn: () =>
      engineCall<{ items: AutonomyProposal[] }>("autonomy.proposals", {
        status,
        limit: 100,
      }),
    retry: false,
  });
}

export function useAutonomyInspectQuery(target: { run_id?: string; proposal_id?: string } | null) {
  return useQuery({
    queryKey: ["engine", "autonomy.inspect", target?.run_id ?? target?.proposal_id],
    queryFn: () =>
      engineCall<AutonomyInspectRun | AutonomyInspectProposal>(
        "autonomy.inspect",
        target ?? {},
      ),
    retry: false,
    enabled: target !== null,
  });
}

export function useProposalApproveMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: { proposal_id: string }) =>
      engineCall<ProposalActionResponse>("autonomy.proposal.approve", params),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["engine", "autonomy.proposals"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "autonomy.status"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "queue.list"] });
    },
  });
}

export function useProposalRejectMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: { proposal_id: string; reason?: string }) =>
      engineCall<ProposalActionResponse>("autonomy.proposal.reject", params),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["engine", "autonomy.proposals"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "autonomy.status"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "queue.list"] });
    },
  });
}

export function useSchedulerStatusQuery() {
  return useQuery({
    queryKey: ["engine", "scheduler.status"],
    queryFn: () => engineCall<SchedulerStatus>("scheduler.status"),
    refetchInterval: (query) => {
      const status = query.state.data as SchedulerStatus | undefined;
      if (!status) return POLL.systemMs;
      // Poll faster while executions are active or a schedule is due.
      if (status.active_executions > 0) return 3000;
      const dueNow = Number(status.summary?.due_now ?? 0);
      if (dueNow > 0) return 3000;
      return POLL.systemMs;
    },
    retry: false,
  });
}

export function useSchedulesQuery() {
  return useQuery({
    queryKey: ["engine", "scheduler.list"],
    queryFn: () => engineCall<{ items: Schedule[] }>("scheduler.list"),
    // Refresh schedules at a moderate cadence; run state changes come through
    // the status query and post-mutation invalidation.
    refetchInterval: (query) => {
      const data = query.state.data as { items: Schedule[] } | undefined;
      const anyLeased = data?.items?.some(
        (s) => Boolean((s as Schedule & { leased_by?: string | null }).leased_by),
      );
      return anyLeased ? 3000 : POLL.systemMs;
    },
    retry: false,
  });
}

export function useScheduleInspectQuery(scheduleId: string | null) {
  return useQuery({
    queryKey: ["engine", "scheduler.inspect", scheduleId],
    queryFn: () =>
      engineCall<ScheduleInspect>("scheduler.inspect", {
        schedule_id: scheduleId,
        limit: 25,
      }),
    refetchInterval: 4000,
    retry: false,
    enabled: scheduleId !== null,
  });
}

export function useScheduleCreateMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: ScheduleCreateRequest) =>
      engineCall<Schedule>("scheduler.create", params),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["engine", "scheduler.list"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "scheduler.status"] });
    },
  });
}

export function useScheduleUpdateMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: ScheduleUpdateRequest) =>
      engineCall<Schedule>("scheduler.update", params),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["engine", "scheduler.list"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "scheduler.status"] });
    },
  });
}

export function useScheduleToggleMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: { schedule_id: string; enabled: boolean }) =>
      engineCall<Schedule>(
        params.enabled ? "scheduler.enable" : "scheduler.disable",
        { schedule_id: params.schedule_id },
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["engine", "scheduler.list"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "scheduler.status"] });
    },
  });
}

export function useScheduleDeleteMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: { schedule_id: string }) =>
      engineCall<{ deleted: boolean; schedule_id: string }>("scheduler.delete", params),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["engine", "scheduler.list"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "scheduler.status"] });
    },
  });
}

export function useScheduleRunNowMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: { schedule_id: string }) =>
      engineCall<ScheduleRunSummary>("scheduler.run_now", params),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ["engine", "scheduler.list"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "scheduler.status"] });
      void queryClient.invalidateQueries({
        queryKey: ["engine", "scheduler.inspect", data.schedule_id],
      });
      void queryClient.invalidateQueries({ queryKey: ["engine", "queue.list"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "autonomy.status"] });
    },
  });
}

// =====================================================================
// M4 — Publishing
// =====================================================================

export function usePublishingStatusQuery() {
  return useQuery({
    queryKey: ["engine", "publishing.status"],
    queryFn: () => engineCall<PublishingStatus>("publishing.status"),
    refetchInterval: (query) => {
      const data = query.state.data;
      // Poll faster while jobs are awaiting approval or ready to publish.
      const active = data && (data.awaiting_approval > 0 || data.ready_to_publish > 0);
      return active ? POLL.queueMs : POLL.systemMs;
    },
    retry: false,
  });
}

export function useReadyListQuery() {
  return useQuery({
    queryKey: ["engine", "publishing.list_ready"],
    queryFn: () => engineCall<ReadyList>("publishing.list_ready"),
    refetchInterval: (query) => {
      const data = query.state.data;
      const anyReady = data && data.items.length > 0;
      return anyReady ? POLL.queueMs : POLL.systemMs;
    },
    retry: false,
  });
}

export function usePublishingInspectQuery(jobId: string | null) {
  return useQuery({
    queryKey: ["engine", "publishing.inspect", jobId],
    queryFn: () =>
      engineCall<PublishingInspect>("publishing.inspect", { job_id: jobId }),
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const data = query.state.data;
      // Stop aggressive polling once the job reached a terminal publish state.
      const terminal = data && (data.state === "PUBLISHED" || data.state === "FAILED_PUBLISH");
      return terminal ? POLL.systemMs : POLL.queueMs;
    },
    retry: false,
  });
}

export function useYouTubeAuthQuery() {
  return useQuery({
    queryKey: ["engine", "youtube.auth_status"],
    queryFn: () => engineCall<YouTubeAuthStatus>("youtube.auth_status"),
    refetchInterval: POLL.systemMs,
    retry: false,
  });
}

export function usePublishApproveMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: {
      job_id: string;
      platform?: string;
      decided_by?: string;
      notes?: string;
    }) => engineCall<ApprovalActionResult>("publishing.approve", params),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ["engine", "publishing.list_ready"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "publishing.status"] });
      void queryClient.invalidateQueries({
        queryKey: ["engine", "publishing.inspect", variables.job_id],
      });
    },
  });
}

export function usePublishRejectMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: {
      job_id: string;
      decided_by?: string;
      notes?: string;
    }) => engineCall<ApprovalActionResult>("publishing.reject", params),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ["engine", "publishing.list_ready"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "publishing.status"] });
      void queryClient.invalidateQueries({
        queryKey: ["engine", "publishing.inspect", variables.job_id],
      });
    },
  });
}

export function usePublishMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: PublishRequestParams) =>
      engineCall<PublishActionResult>("publishing.publish", params),
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ["engine", "publishing.list_ready"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "publishing.status"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "queue.list"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "health"] });
      void queryClient.invalidateQueries({
        queryKey: ["engine", "publishing.inspect", variables.job_id],
      });
      void queryClient.invalidateQueries({ queryKey: ["engine", "analytics.status"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "analytics.report"] });
    },
  });
}

// =====================================================================
// M4 — Analytics
// =====================================================================

export function useAnalyticsStatusQuery() {
  return useQuery({
    queryKey: ["engine", "analytics.status"],
    queryFn: () => engineCall<AnalyticsStatus>("analytics.status"),
    // Slower polling while idle; refresh after sync invalidates.
    refetchInterval: POLL.healthMs,
    retry: false,
  });
}

export function useAnalyticsSyncMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: AnalyticsSyncParams) =>
      engineCall<AnalyticsSyncAllResult>("analytics.sync", params),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["engine", "analytics.status"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "analytics.report"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "analytics.snapshots"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "strategy.status"] });
    },
  });
}

export function useAnalyticsSnapshotsQuery(jobId: string | null) {
  return useQuery({
    queryKey: ["engine", "analytics.snapshots", jobId],
    queryFn: () =>
      engineCall<AnalyticsSnapshots>("analytics.snapshots", { job_id: jobId }),
    enabled: Boolean(jobId),
    refetchInterval: POLL.healthMs,
    retry: false,
  });
}

export function useAnalyticsReportQuery(channelId?: string) {
  return useQuery({
    queryKey: ["engine", "analytics.report", channelId ?? null],
    queryFn: () =>
      engineCall<AnalyticsReport>("analytics.report", { channel_id: channelId }),
    refetchInterval: POLL.healthMs,
    retry: false,
  });
}

// =====================================================================
// M4 — Strategy
// =====================================================================

export function useStrategyStatusQuery(channelId?: string) {
  return useQuery({
    queryKey: ["engine", "strategy.status", channelId ?? "default"],
    queryFn: () =>
      engineCall<StrategyStatus>("strategy.status", { channel_id: channelId }),
    refetchInterval: POLL.systemMs,
    retry: false,
  });
}

export function useStrategyShowQuery(versionId: string | null) {
  return useQuery({
    queryKey: ["engine", "strategy.show", versionId ?? null],
    queryFn: () => engineCall<StrategyShow>("strategy.show", { version_id: versionId }),
    enabled: true,
    refetchInterval: POLL.systemMs,
    retry: false,
  });
}

export function useStrategyLearnMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: StrategyLearnParams) =>
      engineCall<StrategyLearnResult>("strategy.learn", params),
    onSuccess: () => {
      // Refresh strategy state after a learning run.
      void queryClient.invalidateQueries({ queryKey: ["engine", "strategy.status"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "strategy.show"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "analytics.status"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "autonomy.status"] });
    },
  });
}

// =====================================================================
// M4 — Autonomous public publishing switch (M6: backend-controlled)
// =====================================================================

export function useAutonomyPublishStatusQuery() {
  return useQuery({
    queryKey: ["engine", "autonomy.publish_status"],
    queryFn: () => engineCall<AutonomyPublishStatus>("autonomy.publish_status"),
    refetchInterval: POLL.systemMs,
    retry: false,
  });
}

const AUTONOMY_SWITCH_INVALIDATE = [
  ["engine", "autonomy.publish_status"],
  ["engine", "publishing.status"],
  ["engine", "health"],
] as const;

export function useAutonomyPublishEnableMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => engineCall<AutonomyPublishSwitchResult>("autonomy.publish_enable"),
    onSuccess: (res) => {
      if (res.ok) {
        queryClient.invalidateQueries({ queryKey: ["engine", "autonomy.publish_status"] });
        queryClient.invalidateQueries({ queryKey: ["engine", "publishing.status"] });
        queryClient.invalidateQueries({ queryKey: ["engine", "health"] });
      }
    },
  });
}

export function useAutonomyPublishDisableMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => engineCall<AutonomyPublishSwitchResult>("autonomy.publish_disable"),
    onSuccess: () => {
      for (const key of AUTONOMY_SWITCH_INVALIDATE) {
        queryClient.invalidateQueries({ queryKey: [...key] });
      }
    },
  });
}