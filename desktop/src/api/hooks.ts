import { useQuery } from "@tanstack/react-query";
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

export function useQueueQuery() {
  return useQuery({
    queryKey: ["engine", "queue.list"],
    queryFn: () => engineCall<QueueList>("queue.list"),
    refetchInterval: POLL.queueMs,
    retry: false,
  });
}

export function useJobInspectQuery(jobId: string | null) {
  return useQuery({
    queryKey: ["engine", "job.inspect", jobId],
    queryFn: () => engineCall<JobInspect>("job.inspect", { job_id: jobId }),
    refetchInterval: POLL.activityMs,
    retry: false,
    enabled: jobId !== null,
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