import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../App";
import { useUiStore } from "../state/ui";
import { engineCall } from "../api/engine";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
}));

import { invoke } from "@tauri-apps/api/core";
import type { Mock } from "vitest";

function renderApp() {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0 },
    },
  });
  return render(
    <QueryClientProvider client={qc}>
      <App />
    </QueryClientProvider>,
  );
}

describe("App navigation and non-blocking engine calls", () => {
  beforeEach(() => {
    useUiStore.setState({ page: "dashboard", selectedJobId: null });
    vi.clearAllMocks();
  });

  it("navigates immediately between screens even when a backend engine call is slow or in-flight", async () => {
    // Simulate a slow in-flight call for dashboard data that does not resolve immediately
    let slowResolve: (val: unknown) => void;
    const slowPromise = new Promise((resolve) => {
      slowResolve = resolve;
    });

    (invoke as unknown as Mock).mockImplementation((cmd: string, args?: { method?: string }) => {
      if (cmd === "engine_status") {
        return Promise.resolve({ running: true, pid: 12345 });
      }
      if (cmd === "engine_call") {
        if (args?.method === "health.get") {
          return slowPromise;
        }
        if (args?.method === "autonomy.publish_status") {
          return Promise.resolve({
            enabled: false,
            state: "DISABLED",
            label: "AUTONOMOUS PUBLIC PUBLISHING DISABLED",
            guardrails: ["QA PASS required"],
            boundary: "Autonomous publishing is disabled.",
          });
        }
        if (args?.method === "queue.status") {
          return Promise.resolve({
            summary: {
              queued: 0,
              running: 0,
              retry_wait: 0,
              succeeded: 0,
              failed: 0,
              blocked: 0,
              cancelled: 0,
              dead_letter: 0,
              total: 0,
              active_workers: [],
            },
          });
        }
        if (args?.method === "queue.list") {
          return Promise.resolve({
            summary: {
              queued: 0,
              running: 0,
              retry_wait: 0,
              succeeded: 0,
              failed: 0,
              blocked: 0,
              cancelled: 0,
              dead_letter: 0,
              total: 0,
              active_workers: [],
            },
            items: [],
            total: 0,
          });
        }
        if (args?.method === "settings.get") {
          return Promise.resolve({ settings: {}, defaults: {}, schema: {} });
        }
        if (args?.method === "production.status") {
          return Promise.resolve({ engine: "remotion", presets: [], running: false });
        }
        return Promise.resolve({});
      }
      return Promise.resolve({});
    });

    renderApp();

    // App header should render online status
    await waitFor(() => {
      expect(screen.getByText(/engine online/i)).toBeInTheDocument();
    });

    // We are on Dashboard initially
    expect(screen.getByRole("button", { name: "Dashboard" })).toHaveClass("active");

    // Click Queue nav button while health.get is still in flight
    fireEvent.click(screen.getByRole("button", { name: "Queue" }));

    // Navigation switches immediately to Queue
    expect(screen.getByRole("button", { name: "Queue" })).toHaveClass("active");
    expect(screen.getByRole("button", { name: "Dashboard" })).not.toHaveClass("active");

    // Click Settings nav button
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    expect(screen.getByRole("button", { name: "Settings" })).toHaveClass("active");

    // Click Production nav button
    fireEvent.click(screen.getByRole("button", { name: "Production" }));
    expect(screen.getByRole("button", { name: "Production" })).toHaveClass("active");

    // Now resolve the slow promise
    slowResolve!({ status: "healthy", publishing: { counts: { ready: 0 } } });
  });

  it("handles concurrent engine calls independently without serial blocking", async () => {
    const callOrder: string[] = [];

    (invoke as unknown as Mock).mockImplementation((_cmd: string, args?: { method?: string }) => {
      if (args?.method === "slow.method") {
        return new Promise((resolve) => {
          setTimeout(() => {
            callOrder.push("slow_done");
            resolve({ result: "slow" });
          }, 50);
        });
      }
      if (args?.method === "fast.method") {
        return new Promise((resolve) => {
          setTimeout(() => {
            callOrder.push("fast_done");
            resolve({ result: "fast" });
          }, 5);
        });
      }
      return Promise.resolve({});
    });

    // Launch slow and fast calls concurrently
    const slowCall = engineCall("slow.method");
    const fastCall = engineCall("fast.method");

    const [slowRes, fastRes] = await Promise.all([slowCall, fastCall]);

    expect(slowRes).toEqual({ result: "slow" });
    expect(fastRes).toEqual({ result: "fast" });
    // Fast call finishes before slow call completes
    expect(callOrder).toEqual(["fast_done", "slow_done"]);
  });
});
