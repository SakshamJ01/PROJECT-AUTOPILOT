import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ProductionScreen from "../components/ProductionScreen";
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
    channel_id: "default",
    content_id: null,
    topic: "Quantum Computing Basics",
    stage: "RENDER",
    status: "running",
    priority: 3,
    attempt_count: 1,
    scheduled_at: null,
    started_at: "2026-09-17T10:00:00",
    lease_expires_at: null,
    completed_at: null,
    payload_json: null,
    manifest_id: null,
    last_error: null,
    worker_id: "desktop-1",
    profile: "short_vertical",
    policy: "local_only",
    auto_publish: false,
    publish_visibility: null,
    providers: { llm: "ollama", tts: "kokoro" },
    ...overrides,
  };
}

const summary = {
  queued: 0,
  running: 1,
  retry_wait: 0,
  succeeded: 0,
  failed: 0,
  blocked: 0,
  cancelled: 0,
  dead_letter: 0,
  total: 1,
  active_workers: [],
};

const jobInspect = {
  found: true,
  job_id: "job-1",
  job: { topic: "Quantum Computing Basics", channel_id: "default" },
  events: [
    { event_id: 1, job_id: "job-1", from_state: "IDEA", to_state: "RESEARCH", reason: "start", occurred_at: "2026-09-17T10:00:00", channel_id: "default", topic: null },
  ],
  artifacts: [
    { artifact_id: 1, job_id: "job-1", artifact_path: "C:/artifacts/job-1/render/final.mp4", artifact_type: "media", checksum_sha256: "abc", created_at: "2026-09-17T10:05:00" },
    { artifact_id: 2, job_id: "job-1", artifact_path: "C:/artifacts/job-1/script.md", artifact_type: "script", checksum_sha256: "def", created_at: "2026-09-17T10:02:00" },
  ],
  errors: [],
  queue_item: makeItem(),
  publications: [],
  qa_reports: [],
  stage_order: ["RESEARCH", "SCRIPT", "VOICE", "ASSETS", "RENDER", "QA", "PUBLISH", "COMPLETE"],
};

function renderScreen() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <ProductionScreen />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useUiStore.getState().setSelectedJobId(null);
  useUiStore.getState().setJobDrawerTab("overview");
});

afterEach(() => {
  vi.clearAllMocks();
});

function mockQueueAndInspect(itemOverrides: Record<string, unknown> = {}) {
  const item = makeItem(itemOverrides);
  (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
    if (args.method === "queue.list") {
      return Promise.resolve({ items: [item], summary });
    }
    if (args.method === "job.inspect") {
      return Promise.resolve({ ...jobInspect, queue_item: item });
    }
    return Promise.reject(new Error(`unexpected ${args.method}`));
  });
  return item;
}

describe("Production screen", () => {
  it("renders the focused running job with its stage timeline", async () => {
    mockQueueAndInspect();
    renderScreen();

    await waitFor(() =>
      expect(screen.getAllByText("Quantum Computing Basics").length).toBeGreaterThan(0),
    );
    // Stage timeline shows stage names.
    expect(screen.getByText("RENDER")).toBeInTheDocument();
    expect(screen.getByText("QA")).toBeInTheDocument();
    // Providers surfaced from payload (names only, no secrets).
    expect(screen.getAllByText("ollama").length).toBeGreaterThan(0);
    expect(screen.getAllByText("kokoro").length).toBeGreaterThan(0);
    // Artifacts from job.inspect.
    await waitFor(() => expect(screen.getByText("final.mp4")).toBeInTheDocument());
    expect(screen.getByText("script.md")).toBeInTheDocument();
  });

  it("shows Cancel for a running job and requires confirmation", async () => {
    const item = mockQueueAndInspect();
    let cancelled = false;
    (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
      if (args.method === "queue.list") return Promise.resolve({ items: [item], summary });
      if (args.method === "job.inspect") return Promise.resolve({ ...jobInspect, queue_item: item });
      if (args.method === "production.cancel") {
        cancelled = true;
        return Promise.resolve({ cancelled: true, queue_id: item.queue_id });
      }
      return Promise.reject(new Error(`unexpected ${args.method}`));
    });

    renderScreen();
    const cancelBtn = await screen.findByText("Cancel");
    fireEvent.click(cancelBtn);

    // First click arms confirmation; the bridge is not called yet.
    expect(cancelled).toBe(false);
    expect(await screen.findByText("Confirm cancel?")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Confirm cancel?"));
    await waitFor(() => expect(cancelled).toBe(true));
  });

  it("shows Retry for a failed job and requires confirmation", async () => {
    const item = mockQueueAndInspect({ status: "failed", stage: "QA", last_error: "gate failed" });
    let retried = false;
    (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
      if (args.method === "queue.list") return Promise.resolve({ items: [item], summary });
      if (args.method === "job.inspect") return Promise.resolve({ ...jobInspect, queue_item: item });
      if (args.method === "production.retry") {
        retried = true;
        return Promise.resolve({ retried: true, queue_id: item.queue_id });
      }
      return Promise.reject(new Error(`unexpected ${args.method}`));
    });

    renderScreen();
    const retryBtn = await screen.findByText("Retry");
    fireEvent.click(retryBtn);
    expect(retried).toBe(false);
    fireEvent.click(await screen.findByText("Confirm retry?"));
    await waitFor(() => expect(retried).toBe(true));
  });

  it("hides Cancel on a succeeded job", async () => {
    const item = mockQueueAndInspect({ status: "succeeded", stage: "COMPLETE" });
    (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
      if (args.method === "queue.list") return Promise.resolve({ items: [item], summary });
      if (args.method === "job.inspect") return Promise.resolve({ ...jobInspect, queue_item: item });
      return Promise.reject(new Error(`unexpected ${args.method}`));
    });

    renderScreen();
    await waitFor(() =>
      expect(screen.getAllByText("Quantum Computing Basics").length).toBeGreaterThan(0),
    );
    expect(screen.queryByText("Cancel")).toBeNull();
    expect(screen.queryByText("Retry")).toBeNull();
  });

  it("validates the start form and calls production.start", async () => {
    mockQueueAndInspect();
    let startedWith: Record<string, unknown> | null = null;
    const engineRunning = {
      engine: "moneyprinterturbo",
      version: "v1.3.6",
      running: true,
      managed: true,
      mode: "http_api",
      endpoint: "http://127.0.0.1:8080",
      pid: 1234,
    };
    (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string; params?: Record<string, unknown> }) => {
      if (args.method === "queue.list") return Promise.resolve({ items: [], summary });
      if (args.method === "production.engine.status") return Promise.resolve(engineRunning);
      if (args.method === "production.engine.ensure") return Promise.resolve(engineRunning);
      if (args.method === "production.start") {
        startedWith = args.params ?? null;
        return Promise.resolve({
          queue_id: "desk-abc",
          job_id: "prod-topic-1234",
          status: "succeeded",
          media_path: "/tmp/out.mp4",
          qa_status: "APPROVED",
          error: null,
        });
      }
      return Promise.reject(new Error(`unexpected ${args.method}`));
    });

    renderScreen();

    const button = await screen.findByText("Start production");
    // Empty topic must not fire the mutation.
    fireEvent.click(button);
    await new Promise((r) => setTimeout(r, 50));
    expect(startedWith).toBeNull();

    const topicInput = screen.getByLabelText("Production topic");
    fireEvent.change(topicInput, { target: { value: "Deep dive into WebAssembly" } });
    fireEvent.click(button);

    await waitFor(() => {
      expect(startedWith).not.toBeNull();
      expect((startedWith as Record<string, unknown>).topic).toBe("Deep dive into WebAssembly");
      // M2 never enables publishing from the desktop.
      expect((startedWith as Record<string, unknown>).auto_publish ?? false).toBe(false);
    });
  });

  it("surfaces a clear error when the production engine cannot start", async () => {
    mockQueueAndInspect();
    const engineDown = {
      engine: "moneyprinterturbo",
      version: "v1.3.6",
      running: false,
      managed: false,
      mode: "not_installed",
      endpoint: "http://127.0.0.1:8080",
      error: "MoneyPrinterTurbo v1.3.6 was not found on this machine.",
    };
    let ensureCalled = false;
    let startedWith: Record<string, unknown> | null = null;
    (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string; params?: Record<string, unknown> }) => {
      if (args.method === "queue.list") return Promise.resolve({ items: [], summary });
      if (args.method === "production.engine.status") return Promise.resolve(engineDown);
      if (args.method === "production.engine.ensure") {
        ensureCalled = true;
        return Promise.resolve(engineDown);
      }
      if (args.method === "production.start") {
        startedWith = args.params ?? null;
        return Promise.resolve({ queue_id: "q", job_id: "j", status: "succeeded", media_path: null, qa_status: null, error: null });
      }
      return Promise.reject(new Error(`unexpected ${args.method}`));
    });

    renderScreen();
    const button = await screen.findByText("Start production");
    fireEvent.change(screen.getByLabelText("Production topic"), {
      target: { value: "Deep dive into WebAssembly" },
    });
    fireEvent.click(button);

    // The engine ensure runs first and must report the failure clearly...
    await waitFor(() => expect(ensureCalled).toBe(true));
    expect(await screen.findByText(/Production engine unavailable/i)).toBeInTheDocument();
    expect(screen.getByText(/was not found on this machine/i)).toBeInTheDocument();
    // ...and production.start must never be attempted.
    expect(startedWith).toBeNull();
  });

  it("shows the empty state when there are no jobs", async () => {
    (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
      if (args.method === "queue.list") return Promise.resolve({ items: [], summary });
      return Promise.reject(new Error(`unexpected ${args.method}`));
    });
    renderScreen();
    await waitFor(() => expect(screen.getByText("No production jobs yet.")).toBeInTheDocument());
  });

  it("never leaks secrets from production payloads into the DOM", async () => {
    const item = mockQueueAndInspect();
    (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
      if (args.method === "queue.list") return Promise.resolve({ items: [item], summary });
      if (args.method === "job.inspect") return Promise.resolve({ ...jobInspect, queue_item: item });
      return Promise.reject(new Error(`unexpected ${args.method}`));
    });
    const { container } = renderScreen();
    await waitFor(() =>
      expect(screen.getAllByText("Quantum Computing Basics").length).toBeGreaterThan(0),
    );
    const text = container.textContent?.toLowerCase() ?? "";
    for (const kw of ["access_token", "client_secret", "gemini_api_key", "refresh_token", "authorization_code"]) {
      expect(text).not.toContain(kw);
    }
  });

  it("renders failed stage with stage-failed class when job failed", async () => {
    mockQueueAndInspect({ status: "failed", stage: "RENDER" });
    const { container } = renderScreen();
    await waitFor(() =>
      expect(screen.getAllByText("Quantum Computing Basics").length).toBeGreaterThan(0),
    );
    const failedItem = container.querySelector(".stage-failed");
    expect(failedItem).toBeInTheDocument();
    expect(failedItem?.textContent).toContain("RENDER");
  });
});