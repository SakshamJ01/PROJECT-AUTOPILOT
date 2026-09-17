import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import AnalyticsScreen from "../components/AnalyticsScreen";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
}));

import { invoke } from "@tauri-apps/api/core";
import type { Mock } from "vitest";

const analyticsStatus = {
  status: "AVAILABLE",
  default_provider: "mock",
  published_job_count: 1,
  jobs_with_snapshots: 1,
  snapshot_count: 2,
  last_observed_at: "2026-09-17T12:00:00Z" as string | null,
  has_published_jobs: true,
  learning: { status: "AVAILABLE", current_strategy_version: "strat-v1" },
  youtube: { status: "needs_auth", authenticated: false, secrets_present: true },
};

const reportRow = {
  job_id: "job-pub-1",
  topic: "AI Breakthroughs",
  platform: "youtube",
  remote_id: "vid-abc",
  views: 1200,
  likes: 80,
  comments: 15,
  engagement_rate: 0.079,
  snapshots_recorded: 2,
  is_synthetic: true,
  latest_observed_at: "2026-09-17T12:00:00Z",
};

function renderScreen() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <AnalyticsScreen />
    </QueryClientProvider>,
  );
}

function mockAll(report: unknown[] = [reportRow], status = analyticsStatus) {
  (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
    if (args.method === "analytics.status") return Promise.resolve(status);
    if (args.method === "analytics.report")
      return Promise.resolve({ report, channel_attribution: null, rows: report.length, note: null });
    if (args.method === "analytics.sync")
      return Promise.resolve({ synced_count: 1, total_targeted: 1 });
    return Promise.reject(new Error(`unexpected ${args.method}`));
  });
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("Analytics screen", () => {
  it("renders summary counts from real stored data", async () => {
    mockAll();
    renderScreen();

    await waitFor(() => expect(screen.getByText("Published jobs")).toBeInTheDocument());
    expect(screen.getByText("Jobs with snapshots")).toBeInTheDocument();
    expect(screen.getByText("Snapshots stored")).toBeInTheDocument();
    expect(screen.getByText("Last observation")).toBeInTheDocument();
  });

  it("shows an explicit empty state when there is no analytics data", async () => {
    mockAll([], {
      ...analyticsStatus,
      has_published_jobs: false,
      published_job_count: 0,
      jobs_with_snapshots: 0,
      snapshot_count: 0,
      last_observed_at: null,
    });
    renderScreen();

    await waitFor(() =>
      expect(screen.getByText(/No stored analytics yet/)).toBeInTheDocument(),
    );
    // Summary cards reflect the empty backend state, not fabricated metrics.
    expect(screen.getAllByText("0").length).toBeGreaterThan(0);
  });

  it("renders report rows with real backend fields", async () => {
    mockAll();
    renderScreen();

    await waitFor(() => expect(screen.getByText("job-pub-1")).toBeInTheDocument());
    expect(screen.getByText("AI Breakthroughs")).toBeInTheDocument();
    expect(screen.getAllByText("1,200").length).toBeGreaterThan(0);
    expect(screen.getAllByText("7.9%").length).toBeGreaterThan(0);
  });

  it("exposes the Sync Analytics action and confirms before running", async () => {
    let synced: Record<string, unknown> | null = null;
    (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
      if (args.method === "analytics.status") return Promise.resolve(analyticsStatus);
      if (args.method === "analytics.report")
        return Promise.resolve({ report: [reportRow], channel_attribution: null, rows: 1, note: null });
      if (args.method === "analytics.sync") {
        synced = args;
        return Promise.resolve({ synced_count: 1, total_targeted: 1 });
      }
      return Promise.reject(new Error(`unexpected ${args.method}`));
    });

    renderScreen();
    const btn = await screen.findByText("Sync Analytics");
    fireEvent.click(btn);
    // Two-click confirm: first click arms, does not sync.
    expect(synced).toBe(null);
    await waitFor(() => expect(screen.getByText("Confirm sync?")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Confirm sync?"));

    await waitFor(() => expect(synced).not.toBeNull());
  });

  it("surfaces sync failures without fabricating data", async () => {
    (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
      if (args.method === "analytics.status") return Promise.resolve(analyticsStatus);
      if (args.method === "analytics.report")
        return Promise.resolve({ report: [], channel_attribution: null, rows: 0, note: null });
      if (args.method === "analytics.sync")
        return Promise.reject(new Error("Provider unavailable"));
      return Promise.reject(new Error(`unexpected ${args.method}`));
    });

    renderScreen();
    fireEvent.click(await screen.findByText("Sync Analytics"));
    await waitFor(() => expect(screen.getByText("Confirm sync?")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Confirm sync?"));

    await waitFor(() =>
      expect(screen.getByText(/Provider unavailable/)).toBeInTheDocument(),
    );
  });

  it("never exposes credential material", async () => {
    mockAll();
    renderScreen();

    await waitFor(() => expect(screen.getByText("mock")).toBeInTheDocument());
    expect(screen.queryByText(/access_token/i)).toBeNull();
    expect(screen.queryByText(/refresh_token/i)).toBeNull();
    expect(screen.queryByText(/client_secret/i)).toBeNull();
  });

  it("renders channel attribution when present", async () => {
    const attribution = {
      channel_id: "default",
      total_videos: 3,
      average_views: 500,
      top_durations: [{ category: "short (<30s)", sample_size: 2, avg_views: 600, max_views: 900 }],
      top_hooks: [],
      top_engines: [],
      recommendation: "Maintain balanced cadence.",
      status: "ready",
    };
    (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
      if (args.method === "analytics.status") return Promise.resolve(analyticsStatus);
      if (args.method === "analytics.report")
        return Promise.resolve({
          report: [reportRow],
          channel_attribution: attribution,
          rows: 1,
          note: null,
        });
      return Promise.reject(new Error(`unexpected ${args.method}`));
    });

    renderScreen();
    await waitFor(() => expect(screen.getByText("Channel attribution — default")).toBeInTheDocument());
    expect(screen.getByText("short (<30s)")).toBeInTheDocument();
    expect(screen.getByText("Maintain balanced cadence.")).toBeInTheDocument();
  });

  it("shows an explicit insufficient-data state for attribution", async () => {
    (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
      if (args.method === "analytics.status") return Promise.resolve(analyticsStatus);
      if (args.method === "analytics.report")
        return Promise.resolve({
          report: [],
          channel_attribution: {
            channel_id: "default",
            total_videos: 0,
            average_views: 0,
            top_durations: [],
            top_hooks: [],
            top_engines: [],
            recommendation: "",
            status: "insufficient_data",
            message: "No performance records found for channel 'default'.",
          },
          rows: 0,
          note: null,
        });
      return Promise.reject(new Error(`unexpected ${args.method}`));
    });

    renderScreen();
    await waitFor(() =>
      expect(screen.getByText(/No performance records found/)).toBeInTheDocument(),
    );
  });
});
