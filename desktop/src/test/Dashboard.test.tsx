import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Dashboard from "../components/Dashboard";
import { useUiStore } from "../state/ui";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
}));

import { invoke } from "@tauri-apps/api/core";
import type { Mock } from "vitest";

const health = {
  status: "healthy",
  python_version: "3.14.4",
  platform: "win32",
  ffmpeg: { available: true },
  sqlite: { available: true, version: "3.45" },
  artifacts_dir: "C:\\artifacts",
  asset_cache_dir: "C:\\cached",
  db: { path: "C:\\db", exists: true, schema_version: 26 },
  qa_engine: { available: true, status: "AVAILABLE" },
  queue_engine: { status: "AVAILABLE", summary: { queued: 1, running: 0, retry_wait: 0, succeeded: 0, failed: 0, blocked: 0, cancelled: 0, dead_letter: 0, total: 1, active_workers: [] } },
  worker: { status: "AVAILABLE" },
  scheduler: { status: "ACTIVE", next_schedule: "2026-09-18T09:00:00" },
  analytics_engine: { status: "AVAILABLE", default_provider: "local_stub" },
  learning_engine: { status: "ACTIVE" },
  autonomy_engine: { status: "AVAILABLE", autonomy_level: 1, max_ideas_per_cycle: 10, max_daily_jobs: 4 },
  autonomy_auto_publish_enabled: false,
  publishing: { status: "AVAILABLE", counts: { ready: 2, published: 5 } },
  channels: { status: "AVAILABLE", total: 1, enabled: 1 },
  providers: { configured: {}, youtube: { status: "unconfigured" } },
};

const queue = {
  items: [
    {
      queue_id: "q-1",
      job_id: "job-1",
      channel_id: "chan1",
      content_id: null,
      topic: "Quantum Computing Basics",
      stage: "RESEARCH",
      status: "queued",
      priority: 3,
      attempt_count: 0,
      scheduled_at: null,
      started_at: null,
      lease_expires_at: null,
      completed_at: null,
      payload_json: null,
      manifest_id: null,
    },
  ],
  summary: { queued: 1, running: 0, retry_wait: 0, succeeded: 0, failed: 0, blocked: 0, cancelled: 0, dead_letter: 0, total: 1, active_workers: [] },
};

const jobInspect = {
  found: true,
  job_id: "job-1",
  job: { topic: "Quantum Computing Basics", channel_id: "chan1" },
  events: [{ event_id: 1, job_id: "job-1", from_state: "IDEA", to_state: "RESEARCH", reason: "fixture", occurred_at: "2026-09-17T10:00:00", channel_id: "chan1", topic: null }],
  artifacts: [],
  errors: [{ error_id: 1, job_id: "job-1", stage: "RENDER", error_type: "FFMPEG_TIMEOUT", message: "render exceeded budget", occurred_at: "2026-09-17T10:01:00", channel_id: "chan1", topic: null }],
  queue_item: null,
  publications: [],
};

function renderDashboard() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <Dashboard />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useUiStore.getState().setSelectedJobId(null);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Dashboard polls the engine bridge", () => {
  it("renders health sectors and queue rows from mocked engine call", async () => {
    (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
      if (args.method === "health.get") return Promise.resolve(health);
      if (args.method === "queue.list") return Promise.resolve(queue);
      return Promise.reject(new Error(`unexpected call ${args.method}`));
    });

    renderDashboard();

    expect(screen.getByText("Loading health…")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("Quantum Computing Basics")).toBeInTheDocument();
    });
    expect(screen.getByText("queued")).toBeInTheDocument();
    expect(screen.getByText(/2 ready · 5 published/)).toBeInTheDocument();
    expect(invoke).toHaveBeenCalledWith("engine_call", { method: "health.get", params: null });
    expect(invoke).toHaveBeenCalledWith("engine_call", { method: "queue.list", params: null });
  });

  it("opens the job drawer when a queue row is clicked", async () => {
    (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
      if (args.method === "engine_status" || _cmd === "engine_status") {
        return Promise.resolve({ running: true, pid: 42 });
      }
      if (args.method === "health.get") return Promise.resolve(health);
      if (args.method === "queue.list") return Promise.resolve(queue);
      if (args.method === "job.inspect") return Promise.resolve(jobInspect);
      return Promise.reject(new Error(`unexpected call ${args.method}`));
    });

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0 } },
    });
    const App = (await import("../App")).default;
    render(
      <QueryClientProvider client={qc}>
        <App />
      </QueryClientProvider>,
    );

    const row = await screen.findByText("Quantum Computing Basics");
    fireEvent.click(row);

    await waitFor(() => {
      expect(screen.getByText("FFMPEG_TIMEOUT")).toBeInTheDocument();
    });
    expect(invoke).toHaveBeenCalledWith("engine_call", {
      method: "job.inspect",
      params: { job_id: "job-1" },
    });
  });
});