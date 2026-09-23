import { invoke } from "@tauri-apps/api/core";

export type JsonValue =
  | null
  | boolean
  | number
  | string
  | JsonValue[]
  | { [key: string]: JsonValue };

// Structured params are accepted directly; they are serialised by the invoke
// boundary. Untyped ad-hoc objects still work via Record<string, unknown>.
type EngineParams = Record<string, unknown> | object;

export function engineCall<T = JsonValue>(
  method: string,
  params?: EngineParams,
): Promise<T> {
  return invoke<T>("engine_call", {
    method,
    params: (params as Record<string, unknown> | null) ?? null,
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
  // MoneyPrinterTurbo readiness probe — quicker while the service is down.
  engineMs: 5000,
} as const;

export interface StructuredError {
  code?: number;
  message: string;
  category: "engine" | "network" | "timeout" | "validation" | "error";
  actionHint?: string;
  raw: string;
}

export function parseEngineError(err: unknown): StructuredError {
  const raw = err instanceof Error ? err.message : String(err ?? "Unknown error");
  const match = raw.match(/engine error (-?\d+):\s*(.*)/i);
  let code: number | undefined;
  let message = raw;
  if (match) {
    code = parseInt(match[1], 10);
    message = match[2];
  }

  let category: StructuredError["category"] = "error";
  let actionHint: string | undefined;

  const lower = message.toLowerCase();
  if (code === -32601 || code === -32602 || lower.includes("invalid param") || lower.includes("validation") || lower.includes("not found")) {
    category = "validation";
    actionHint = "Verify request parameters and format.";
  } else if (lower.includes("timeout") || lower.includes("timed out") || lower.includes("timedout")) {
    category = "timeout";
    actionHint = "Operation timed out. The local engine or provider may be busy or still processing.";
  } else if (lower.includes("moneyprinter") || /\bmpt\b/.test(lower) || lower.includes("renderer")) {
    category = "engine";
    actionHint = "Ensure MoneyPrinterTurbo service is running or click 'Start Engine' on the Production screen.";
  } else if (lower.includes("ollama") || lower.includes("llm") || /\bmodel\b/.test(lower)) {
    category = "engine";
    actionHint = "Ensure local Ollama service is active (`ollama serve`) and model `qwen3:4b` is installed.";
  } else if (lower.includes("connection") || lower.includes("refused") || lower.includes("failed to fetch")) {
    category = "network";
    actionHint = "Connection failed. Check network or local service availability.";
  }

  return { code, message, category, actionHint, raw };
}