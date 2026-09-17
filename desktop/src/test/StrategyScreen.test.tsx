import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import StrategyScreen from "../components/StrategyScreen";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
}));

import { invoke } from "@tauri-apps/api/core";
import type { Mock } from "vitest";

const activeStrategy = {
  version_id: "strat-v1",
  parent_version_id: null,
  created_at: "2026-09-01T00:00:00Z",
  status: "active",
  niche_weights: { technology: 1.0, science: 0.9, general: 0.5 },
  hook_patterns: ["Did you know {fact}?"],
  topic_rules: { max_title_words: 12 },
  supporting_evidence_ids: [],
  rationale: "Initial baseline strategy",
};

const strategyStatus = {
  status: "AVAILABLE",
  channel_id: "default",
  active_strategy: activeStrategy,
  active_strategy_version: "strat-v1",
  learning: {
    status: "AVAILABLE",
    current_strategy_version: "strat-v1",
    last_learning_run: {
      run_id: "learn-1",
      status: "insufficient",
      observations_used: 0,
      resulting_strategy_version: null as string | null,
      input_fingerprint: "abc123",
      completed_at: "2026-09-17T10:00:00Z",
    },
  },
  last_learning_run: {
    run_id: "learn-1",
    status: "insufficient",
    observations_used: 0,
    resulting_strategy_version: null as string | null,
    input_fingerprint: "abc123",
    completed_at: "2026-09-17T10:00:00Z",
  },
  bounds: {
    min_samples: 5,
    min_category_observations: 2,
    window_days: 30,
    max_weight_delta: 0.15,
    max_params_per_update: 3,
    weight_floor: 0.3,
    weight_ceiling: 1.5,
    min_age_days: 2,
  },
  learning_boundary: "Learning only adjusts niche_weights and never publishes.",
};

const strategyShow = {
  found: true,
  version_id: "strat-v1",
  strategy: activeStrategy,
  ancestry: ["strat-v1"],
  learning_runs: [
    {
      run_id: "learn-1",
      status: "insufficient",
      observations_used: 0,
      resulting_strategy_version: null,
      completed_at: "2026-09-17T10:00:00Z",
    },
  ],
  is_active: true,
};

function renderScreen() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <StrategyScreen />
    </QueryClientProvider>,
  );
}

function mockAll(show = strategyShow, status = strategyStatus) {
  (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
    if (args.method === "strategy.status") return Promise.resolve(status);
    if (args.method === "strategy.show") return Promise.resolve(show);
    if (args.method === "strategy.learn")
      return Promise.resolve({
        run_id: "learn-2",
        channel_id: "default",
        status: "insufficient",
        dry_run: false,
        window_days: 30,
        observations_considered: 0,
        observations_used: 0,
        observations_excluded: 0,
        excluded_reasons: {},
        input_fingerprint: "def456",
        is_synthetic_input: false,
        parent_strategy_version: "strat-v1",
        resulting_strategy_version: null,
        reason: "Insufficient audience evidence.",
        error_message: null,
        started_at: "2026-09-17T11:00:00Z",
        completed_at: "2026-09-17T11:00:05Z",
      });
    return Promise.reject(new Error(`unexpected ${args.method}`));
  });
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("Strategy screen", () => {
  it("renders the active strategy version and status", async () => {
    mockAll();
    renderScreen();

    await waitFor(() => expect(screen.getAllByText("strat-v1").length).toBeGreaterThan(0));
    expect(screen.getByText("Active strategy")).toBeInTheDocument();
    expect(screen.getByText("Learning status")).toBeInTheDocument();
  });

  it("renders niche weights with real stored values", async () => {
    mockAll();
    renderScreen();

    await waitFor(() => expect(screen.getByText("Niche weights")).toBeInTheDocument());
    expect(screen.getByText("technology")).toBeInTheDocument();
    expect(screen.getByText("science")).toBeInTheDocument();
    expect(screen.getByText("1.000")).toBeInTheDocument();
  });

  it("shows learning bounds from the backend config", async () => {
    mockAll();
    renderScreen();

    await waitFor(() => expect(screen.getByText("Bounds")).toBeInTheDocument());
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByText("30 days")).toBeInTheDocument();
    expect(screen.getByText("±0.15")).toBeInTheDocument();
  });

  it("reflects learning eligibility from observations", async () => {
    mockAll();
    renderScreen();

    await waitFor(() =>
      expect(screen.getByText("awaiting published analytics")).toBeInTheDocument(),
    );
  });

  it("marks learning eligible when observations exist", async () => {
    mockAll(
      strategyShow,
      {
        ...strategyStatus,
        last_learning_run: {
          ...strategyStatus.last_learning_run,
          observations_used: 7,
          status: "applied",
          resulting_strategy_version: "strat-v2" as string | null,
        },
      },
    );
    renderScreen();

    await waitFor(() => expect(screen.getByText("eligible")).toBeInTheDocument());
  });

  it("Run Learning requires confirmation and delegates to the backend", async () => {
    let learned: Record<string, unknown> | null = null;
    (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
      if (args.method === "strategy.status") return Promise.resolve(strategyStatus);
      if (args.method === "strategy.show") return Promise.resolve(strategyShow);
      if (args.method === "strategy.learn") {
        learned = args;
        return Promise.resolve({
          run_id: "learn-2",
          status: "insufficient",
          observations_used: 0,
          reason: "Insufficient audience evidence.",
          error_message: null,
          parent_strategy_version: "strat-v1",
          resulting_strategy_version: null,
          input_fingerprint: "def456",
        });
      }
      return Promise.reject(new Error(`unexpected ${args.method}`));
    });

    renderScreen();
    const btn = await screen.findByText("Run Learning");
    fireEvent.click(btn);
    // Two-click confirm; first click must not learn.
    expect(learned).toBe(null);
    await waitFor(() => expect(screen.getByText("Confirm learning run?")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Confirm learning run?"));

    await waitFor(() => expect(learned).not.toBeNull());
  });

  it("surfaces learning failure without hiding the error", async () => {
    (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
      if (args.method === "strategy.status") return Promise.resolve(strategyStatus);
      if (args.method === "strategy.show") return Promise.resolve(strategyShow);
      if (args.method === "strategy.learn")
        return Promise.resolve({
          run_id: "learn-2",
          status: "failed",
          observations_used: 0,
          reason: "Learning run failed.",
          error_message: "Synthetic feed unavailable",
          parent_strategy_version: "strat-v1",
          resulting_strategy_version: null,
          input_fingerprint: "def456",
        });
      return Promise.reject(new Error(`unexpected ${args.method}`));
    });

    renderScreen();
    fireEvent.click(await screen.findByText("Run Learning"));
    await waitFor(() => expect(screen.getByText("Confirm learning run?")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Confirm learning run?"));

    await waitFor(() => expect(screen.getByText(/Synthetic feed unavailable/)).toBeInTheDocument());
  });

  it("renders learning run history", async () => {
    mockAll();
    renderScreen();

    await waitFor(() => expect(screen.getByText("Learning runs")).toBeInTheDocument());
    expect(screen.getByText("learn-1")).toBeInTheDocument();
    expect(screen.getByText("insufficient")).toBeInTheDocument();
  });

  it("never exposes credential material", async () => {
    mockAll();
    renderScreen();

    await waitFor(() => expect(screen.getByText("Active strategy")).toBeInTheDocument());
    expect(screen.queryByText(/access_token/i)).toBeNull();
    expect(screen.queryByText(/api_key/i)).toBeNull();
  });

  it("shows the learning boundary notice", async () => {
    mockAll();
    renderScreen();

    await waitFor(() =>
      expect(
        screen.getByText(/Learning only adjusts niche_weights/),
      ).toBeInTheDocument(),
    );
  });
});
