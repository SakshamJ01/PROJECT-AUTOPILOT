import { invoke } from "@tauri-apps/api/core";

export type JsonValue =
  | null
  | boolean
  | number
  | string
  | JsonValue[]
  | { [key: string]: JsonValue };

export function engineCall<T = JsonValue>(
  method: string,
  params?: Record<string, unknown>,
): Promise<T> {
  return invoke<T>("engine_call", {
    method,
    params: params ?? null,
  });
}

export function engineStatus(): Promise<{ running: boolean; pid: number | null }> {
  return invoke<{ running: boolean; pid: number | null }>("engine_status");
}

export const POLL = {
  healthMs: 6000,
  queueMs: 2000,
  activityMs: 2000,
  logsMs: 2000,
  systemMs: 8000,
} as const;