import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  engineCall,
  engineStatus,
  POLL,
  type JsonValue,
} from "./engine";
import type {
  AutonomyInspectProposal,
  AutonomyInspectRun,
  AutonomyProposal,
  AutonomyRunRequest,
  AutonomyRunResult,
  AutonomyStatus,
  HealthGet,
  JobInspect,
  LogEntry,
  ProductionActionResult,
  ProductionStartResult,
  ProposalActionResponse,
  QueueList,
  Schedule,
  ScheduleCreateRequest,
  ScheduleInspect,
  ScheduleRunSummary,
  ScheduleUpdateRequest,
  SchedulerStatus,
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
      void queryClient.invalidateQueries({ queryKey: ["engine", "health.get"] });
      void queryClient.invalidateQueries({ queryKey: ["engine", "job.inspect", data.job_id] });
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