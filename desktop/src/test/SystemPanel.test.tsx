import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import SystemPanel from "../components/SystemPanel";
import { useUiStore } from "../state/ui";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
}));

import { invoke } from "@tauri-apps/api/core";
import type { Mock } from "vitest";

const systemStatus = {
  state: "ready",
  pid: 4242,
  bridge_version: "0.1.0",
  python: "3.14.4",
  platform: "win32",
  uptime_seconds: 12.3,
  db: { path: "C:\\db", exists: true, schema_version: 26 },
  artifacts_dir: "C:\\artifacts",
  config_valid: true,
};

const healthStatus = {
  status: "healthy",
  db: { path: "C:\\db", exists: true, schema_version: 26 },
  scheduler: { status: "AVAILABLE", next_schedule: null },
  queue_engine: { status: "AVAILABLE", summary: {} },
  worker: { status: "AVAILABLE" },
  qa_engine: { available: true, status: "AVAILABLE" },
  analytics_engine: { status: "AVAILABLE", default_provider: "youtube_analytics" },
  learning_engine: { status: "AVAILABLE" },
  autonomy_engine: { status: "AVAILABLE", autonomy_level: 3 },
  autonomy_auto_publish_enabled: false,
  publishing: { status: "AVAILABLE", counts: {} },
  providers: {
    configured: {
      llm: "ollama",
      tts: "windows_sapi",
      asset: "openverse",
    },
    youtube: { status: "configured", token_present: true },
  },
  ffmpeg: { available: true, version: "6.0" },
};

const mptStatus = {
  engine: "moneyprinter",
  version: "1.0",
  installed: true,
  running: true,
  pid: 1234,
};

const entries = [
  { timestamp: "2026-09-17T10:02:00", severity: "error", source: "error", stage: "RENDER", job_id: "job-1", channel_id: "chan1", topic: "Quantum Computing Basics", message: "FFMPEG_TIMEOUT: render exceeded budget" },
  { timestamp: "2026-09-17T10:00:00", severity: "info", source: "event", stage: "RESEARCH", job_id: "job-1", channel_id: "chan1", topic: "Quantum Computing Basics", message: "transition IDEA -> RESEARCH" },
];

function renderPanel() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <SystemPanel />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useUiStore.getState().setSeverityFilter("all");
  useUiStore.getState().setLogSearch("");
  useUiStore.getState().setLogAutoRefresh(true);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("System & Logs panel", () => {
  it("renders system status, health matrix, and log entries", async () => {
    (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
      if (args.method === "system.status") return Promise.resolve(systemStatus);
      if (args.method === "health.get") return Promise.resolve(healthStatus);
      if (args.method === "production.engine_status") return Promise.resolve(mptStatus);
      if (args.method === "logs.tail") return Promise.resolve({ entries });
      return Promise.reject(new Error(`unexpected ${args.method}`));
    });

    renderPanel();

    await screen.findByText("System Health & Subsystem Matrix");
    await screen.findByText("Host Environment & Storage");
    await screen.findByText("FFMPEG_TIMEOUT: render exceeded budget");
    expect(screen.getByText("transition IDEA -> RESEARCH")).toBeInTheDocument();
    expect(screen.getByText("Ollama / LLM Provider")).toBeInTheDocument();
    expect(screen.getByText("MoneyPrinterTurbo")).toBeInTheDocument();
  });

  it("re-queries logs.tail with an error filter when severity changes", async () => {
    const tailMock = vi.fn((_cmd: string, _args: { method: string }) => {
      return Promise.resolve({ entries });
    });
    (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
      if (args.method === "system.status") return Promise.resolve(systemStatus);
      if (args.method === "health.get") return Promise.resolve(healthStatus);
      if (args.method === "production.engine_status") return Promise.resolve(mptStatus);
      if (args.method === "logs.tail") return tailMock(_cmd, args);
      return Promise.reject(new Error(`unexpected ${args.method}`));
    });

    renderPanel();
    await screen.findByText("transition IDEA -> RESEARCH");

    const select = screen.getByLabelText("Severity filter");
    fireEvent.change(select, { target: { value: "error" } });

    await waitFor(() => {
      const call = tailMock.mock.calls.find(
        (c) => (c[1] as { params?: { severity?: string } })?.params?.severity === "error",
      );
      expect(call).toBeTruthy();
    });
  });

  it("shows unavailable UI on bridge rejection", async () => {
    (invoke as unknown as Mock).mockRejectedValue(new Error("bridge down"));
    renderPanel();
    await screen.findByText("System status unavailable");
  });
});