import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import QueueScreen from "../components/QueueScreen";
import { useUiStore } from "../state/ui";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
}));

import { invoke } from "@tauri-apps/api/core";
import type { Mock } from "vitest";

function makeItem(overrides: Record<string, unknown> = {}) {
  return {
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
    last_error: null,
    worker_id: null,
    profile: "short_vertical",
    policy: "local_only",
    auto_publish: false,
    publish_visibility: null,
    ...overrides,
  };
}

const summary = {
  queued: 1,
  running: 1,
  retry_wait: 0,
  succeeded: 2,
  failed: 1,
  blocked: 0,
  cancelled: 0,
  dead_letter: 0,
  total: 5,
  active_workers: [],
};

function renderScreen() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <QueueScreen />
    </QueryClientProvider>,
  );
}

const items = [
  makeItem(),
  makeItem({
    queue_id: "q-2",
    job_id: "job-2",
    topic: "Rust performance",
    status: "running",
    stage: "RENDER",
  }),
  makeItem({
    queue_id: "q-3",
    job_id: "job-3",
    topic: "Kubernetes networking",
    status: "failed",
    stage: "QA",
    last_error: "loudness gate failed",
    attempt_count: 3,
  }),
];

beforeEach(() => {
  useUiStore.getState().setSelectedJobId(null);
});

afterEach(() => {
  vi.clearAllMocks();
});

function mockQueue() {
  (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
    if (args.method === "queue.list") return Promise.resolve({ items, summary });
    return Promise.reject(new Error(`unexpected ${args.method}`));
  });
}

describe("Queue screen", () => {
  it("renders summary stats and queue rows", async () => {
    mockQueue();
    renderScreen();

    await waitFor(() => {
      expect(screen.getByText("Quantum Computing Basics")).toBeInTheDocument();
    });
    expect(screen.getByText("Rust performance")).toBeInTheDocument();
    // Summary stats render counts from the backend summary.
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getAllByText("1")[0]).toBeInTheDocument();
  });

  it("sends a search filter to the bridge", async () => {
    const tail = vi.fn((_cmd: string, _args: { method: string }) =>
      Promise.resolve({ items: [items[0]], summary }),
    );
    (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string; params?: { search?: string } }) => {
      if (args.method !== "queue.list") return Promise.reject(new Error("unexpected"));
      return tail(_cmd, args);
    });

    renderScreen();
    await waitFor(() => expect(screen.getByText("Quantum Computing Basics")).toBeInTheDocument());

    const input = screen.getByLabelText("Search queue");
    fireEvent.change(input, { target: { value: "rust" } });

    await waitFor(() => {
      const call = tail.mock.calls.find(
        (c) => (c[1] as { params?: { search?: string } })?.params?.search === "rust",
      );
      expect(call).toBeTruthy();
    });
  });

  it("sends a status filter to the bridge", async () => {
    const tail = vi.fn((_cmd: string, _args: { method: string }) =>
      Promise.resolve({ items: [items[1]], summary }),
    );
    (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string; params?: { status?: string } }) => {
      if (args.method !== "queue.list") return Promise.reject(new Error("unexpected"));
      return tail(_cmd, args);
    });

    renderScreen();
    await waitFor(() => expect(screen.getByText("Rust performance")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Filter by status"), { target: { value: "running" } });

    await waitFor(() => {
      const call = tail.mock.calls.find(
        (c) => (c[1] as { params?: { status?: string } })?.params?.status === "running",
      );
      expect(call).toBeTruthy();
    });
  });

  it("opens the job drawer when a row is clicked", async () => {
    mockQueue();
    renderScreen();

    const row = await screen.findByText("Rust performance");
    fireEvent.click(row);

    expect(useUiStore.getState().selectedJobId).toBe("job-2");
  });

  it("shows an empty state when no items match", async () => {
    (invoke as unknown as Mock).mockImplementation(() =>
      Promise.resolve({ items: [], summary }),
    );
    renderScreen();
    await waitFor(() =>
      expect(screen.getByText(/No queue items match/)).toBeInTheDocument(),
    );
  });

  it("renders an unavailable state on bridge error", async () => {
    (invoke as unknown as Mock).mockRejectedValue(new Error("bridge down"));
    renderScreen();
    await waitFor(() => expect(screen.getByText("Queue unavailable")).toBeInTheDocument());
  });
});