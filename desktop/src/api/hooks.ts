import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  engineCall,
  engineStatus,
  POLL,
  type JsonValue,
} from "./engine";
import type {
  HealthGet,
  JobInspect,
  LogEntry,
  ProductionActionResult,
  ProductionStartResult,
  QueueList,
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