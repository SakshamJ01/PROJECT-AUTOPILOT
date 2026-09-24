import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import JobDrawer from "../components/JobDrawer";
import { useUiStore } from "../state/ui";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
}));

import { invoke } from "@tauri-apps/api/core";
import type { Mock } from "vitest";

const mockJobData = {
  found: true,
  job_id: "job-abc-123",
  queue_item: {
    queue_id: "q-123",
    job_id: "job-abc-123",
    channel_id: "chan1",
    topic: "AI Agents Future",
    stage: "RENDER",
    status: "failed",
    attempt_count: 2,
    last_error: "Renderer timeout after 300s",
  },
  manifest: {
    manifest_id: "man-123",
    job_id: "job-abc-123",
    channel_id: "chan1",
    topic: "AI Agents Future",
    target_duration_sec: 60,
    language: "en",
    status: "failed",
    current_stage: "RENDER",
    retry_count: 2,
    publish_decision: "manual_approval_required",
    checksum_manifest: "sha256-abcdef1234567890",
  },
  stage_runs: [
    {
      stage_name: "RESEARCH",
      status: "succeeded",
      duration_ms: 1200,
      started_at: "2026-09-24T12:00:00Z",
      completed_at: "2026-09-24T12:00:01Z",
      metadata: { sources_count: 5 },
    },
    {
      stage_name: "RENDER",
      status: "failed",
      duration_ms: 45000,
      started_at: "2026-09-24T12:00:02Z",
      completed_at: "2026-09-24T12:00:47Z",
      error_message: "Renderer timeout after 300s",
    },
  ],
  artifacts: [
    {
      artifact_id: "art-1",
      job_id: "job-abc-123",
      stage: "SCRIPT",
      artifact_type: "script_text",
      file_path: "C:/tmp/script.json",
      file_size_bytes: 1024,
      sha256_hash: "sha256-111222333444",
      created_at: "2026-09-24T12:00:01Z",
    },
  ],
  qa_reports: [
    {
      qa_id: "qa-1",
      job_id: "job-abc-123",
      decision: "PASS",
      overall_score: 92,
      publish_allowed: true,
      reason_codes: ["PASS_ALL_CHECKS"],
      summary: "Quality checks passed",
      evaluated_at: "2026-09-24T12:00:48Z",
    },
  ],
  errors: [
    {
      error_id: "err-1",
      job_id: "job-abc-123",
      stage: "RENDER",
      error_type: "TIMEOUT",
      error_message: "Renderer timeout after 300s",
      occurred_at: "2026-09-24T12:00:47Z",
    },
  ],
  publications: [],
};

function renderDrawer() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <JobDrawer />
    </QueryClientProvider>
  );
}

describe("JobDrawer", () => {
  beforeEach(() => {
    useUiStore.setState({
      selectedJobId: null,
      jobDrawerTab: "overview",
      page: "queue",
    });
    vi.clearAllMocks();
  });

  it("does not render when no job is selected", () => {
    renderDrawer();
    expect(screen.queryByRole("tablist")).toBeNull();
  });

  it("renders tabs, overview metrics, and quick actions when job is loaded", async () => {
    (invoke as Mock).mockResolvedValue(mockJobData);
    useUiStore.setState({ selectedJobId: "job-abc-123" });

    renderDrawer();

    await waitFor(() => {
      expect(screen.getAllByText("job-abc-123").length).toBeGreaterThan(0);
    });

    expect(screen.getByText("AI Agents Future")).toBeDefined();
    expect(screen.getByText("Renderer timeout after 300s")).toBeDefined();
    expect(screen.getByText("Overview")).toBeDefined();
    expect(screen.getByText("Timeline")).toBeDefined();
    expect(screen.getByText("Artifacts")).toBeDefined();
    expect(screen.getByText("Errors")).toBeDefined();
    expect(screen.getByText("Publication")).toBeDefined();

    // Verify Action Bar buttons (Retry for failed job + QA allowed Approve button)
    expect(screen.getByRole("button", { name: "Retry job" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Approve for Publishing" })).toBeDefined();
  });

  it("switches to timeline tab and displays stage runs", async () => {
    (invoke as Mock).mockResolvedValue(mockJobData);
    useUiStore.setState({ selectedJobId: "job-abc-123" });

    renderDrawer();

    await waitFor(() => {
      expect(screen.getByText("Timeline")).toBeDefined();
    });

    fireEvent.click(screen.getByText("Timeline"));

    await waitFor(() => {
      expect(screen.getByText("RESEARCH")).toBeDefined();
      expect(screen.getByText("RENDER")).toBeDefined();
    });
  });

  it("switches to artifacts tab and displays artifacts with checksum", async () => {
    (invoke as Mock).mockResolvedValue(mockJobData);
    useUiStore.setState({ selectedJobId: "job-abc-123" });

    renderDrawer();

    await waitFor(() => {
      expect(screen.getByText("Artifacts")).toBeDefined();
    });

    fireEvent.click(screen.getByText("Artifacts"));

    await waitFor(() => {
      expect(screen.getByText("script_text")).toBeDefined();
      expect(screen.getByText("C:/tmp/script.json")).toBeDefined();
    });
  });

  it("switches to errors tab and displays errors", async () => {
    (invoke as Mock).mockResolvedValue(mockJobData);
    useUiStore.setState({ selectedJobId: "job-abc-123" });

    renderDrawer();

    await waitFor(() => {
      expect(screen.getByText("Errors")).toBeDefined();
    });

    fireEvent.click(screen.getByText("Errors"));

    await waitFor(() => {
      expect(screen.getByText("TIMEOUT")).toBeDefined();
    });
  });
});
