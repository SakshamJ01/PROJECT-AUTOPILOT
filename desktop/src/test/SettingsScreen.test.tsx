import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import SettingsScreen from "../components/SettingsScreen";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
}));

import { invoke } from "@tauri-apps/api/core";
import type { Mock } from "vitest";
import type { AutonomyPublishStatus } from "../api/types";

const disabledSwitch: AutonomyPublishStatus = {
  enabled: false,
  state: "DISABLED",
  label: "AUTONOMOUS PUBLIC PUBLISHING DISABLED",
  default: false,
  controlled_by: "backend",
  guardrails: ["QA PASS required", "kill switch"],
  boundary: "Autonomous public publishing is disabled by default.",
  controlled_at: null,
  timestamp: "2026-09-18T00:00:00",
};

const enabledSwitch: AutonomyPublishStatus = {
  enabled: true,
  state: "ENABLED",
  label: "AUTONOMOUS PUBLIC PUBLISHING ENABLED",
  default: false,
  controlled_by: "backend",
  guardrails: ["QA PASS required", "kill switch"],
  boundary: "Autonomous public publishing is disabled by default.",
  controlled_at: "2026-09-18T01:00:00",
  timestamp: "2026-09-18T01:00:01",
};

function renderScreen() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <SettingsScreen />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Settings screen — autonomous public publishing switch", () => {
  it("renders as DISABLED with an enable action (backend-controlled)", async () => {
    (invoke as unknown as Mock).mockImplementation((_cmd, args) => {
      if (args.method === "autonomy.publish_status") return Promise.resolve(disabledSwitch);
      return Promise.reject(new Error(`unexpected ${args.method}`));
    });

    renderScreen();

    await waitFor(() => {
      expect(screen.getByText("AUTONOMOUS PUBLIC PUBLISHING DISABLED")).toBeInTheDocument();
    });
    const enableBtn = screen.getByRole("button", {
      name: "Enable autonomous public publishing",
    });
    expect(enableBtn).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /kill switch/i })).not.toBeInTheDocument();
  });

  it("enable requires an explicit confirmation and does not flip optimistically", async () => {
    (invoke as unknown as Mock).mockImplementation((_cmd, args) => {
      if (args.method === "autonomy.publish_status") return Promise.resolve(disabledSwitch);
      return Promise.reject(new Error(`unexpected ${args.method}`));
    });

    renderScreen();

    await waitFor(() => {
      expect(screen.getByText("AUTONOMOUS PUBLIC PUBLISHING DISABLED")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "Enable autonomous public publishing" }));

    // Confirmation is shown; nothing was sent to the backend yet.
    expect(
      screen.getByRole("button", { name: /Confirm: enable autonomous public publishing/ }),
    ).toBeInTheDocument();
    expect(invoke).not.toHaveBeenCalledWith(
      "engine_call",
      expect.objectContaining({ method: "autonomy.publish_enable" }),
    );
  });

  it("enable success flips the switch to ENABLED (reflected via backend)", async () => {
    let current: AutonomyPublishStatus = { ...disabledSwitch };
    (invoke as unknown as Mock).mockImplementation((_cmd, args) => {
      if (args.method === "autonomy.publish_status") return Promise.resolve(current);
      if (args.method === "autonomy.publish_enable") {
        current = { ...enabledSwitch };
        return Promise.resolve({ ...enabledSwitch, ok: true, changed: true });
      }
      return Promise.reject(new Error(`unexpected ${args.method}`));
    });

    renderScreen();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Enable autonomous public publishing" })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "Enable autonomous public publishing" }));
    fireEvent.click(
      screen.getByRole("button", { name: /Confirm: enable autonomous public publishing/ }),
    );

    await waitFor(() => {
      expect(invoke).toHaveBeenCalledWith(
        "engine_call",
        expect.objectContaining({ method: "autonomy.publish_enable" }),
      );
    });
    // No optimistic flip: the UI only shows ON after the backend refetch confirms it.
    await waitFor(() => {
      expect(screen.getByText("AUTONOMOUS PUBLIC PUBLISHING ENABLED")).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /kill switch/i })).toBeInTheDocument();
  });

  it("enable failure shows the failed prerequisites and leaves switch DISABLED", async () => {
    (invoke as unknown as Mock).mockImplementation((_cmd, args) => {
      if (args.method === "autonomy.publish_status") return Promise.resolve(disabledSwitch);
      if (args.method === "autonomy.publish_enable") {
        return Promise.resolve({
          ok: false,
          changed: false,
          enabled: false,
          state: "DISABLED",
          label: "AUTONOMOUS PUBLIC PUBLISHING DISABLED",
          default: false,
          controlled_by: "backend",
          guardrails: [],
          boundary: "",
          reason:
            "Cannot enable autonomous public publishing. Prerequisite(s) not met: youtube_auth.",
          prerequisites: {
            ok: false,
            passed: ["qa_gate"],
            failed: ["youtube_auth", "daily_channel_limits"],
            checks: [],
          },
          controlled_at: null,
          timestamp: "2026-09-18T00:00:00",
        });
      }
      return Promise.reject(new Error(`unexpected ${args.method}`));
    });

    renderScreen();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Enable autonomous public publishing" })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "Enable autonomous public publishing" }));
    fireEvent.click(
      screen.getByRole("button", { name: /Confirm: enable autonomous public publishing/ }),
    );

    await waitFor(() => {
      expect(screen.getAllByText(/youtube_auth/).length).toBeGreaterThan(0);
      expect(screen.getAllByText(/daily_channel_limits/).length).toBeGreaterThan(0);
    });
    expect(screen.getByText("AUTONOMOUS PUBLIC PUBLISHING DISABLED")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Enable autonomous public publishing" })).toBeInTheDocument();
  });

  it("kill switch disable requires confirmation and returns to DISABLED", async () => {
    let current = { ...enabledSwitch };
    (invoke as unknown as Mock).mockImplementation((_cmd, args) => {
      if (args.method === "autonomy.publish_status") return Promise.resolve(current);
      if (args.method === "autonomy.publish_disable") {
        current = { ...disabledSwitch, controlled_at: "2026-09-18T02:00:00" };
        return Promise.resolve({
          ok: true,
          changed: true,
          enabled: false,
          state: "DISABLED",
          label: "AUTONOMOUS PUBLIC PUBLISHING DISABLED",
          default: false,
          controlled_by: "backend",
          guardrails: [],
          boundary: "",
          controlled_at: "2026-09-18T02:00:00",
          timestamp: "2026-09-18T02:00:01",
        });
      }
      return Promise.reject(new Error(`unexpected ${args.method}`));
    });

    renderScreen();

    await waitFor(() => {
      expect(screen.getByText("AUTONOMOUS PUBLIC PUBLISHING ENABLED")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /kill switch/i }));
    expect(invoke).not.toHaveBeenCalledWith(
      "engine_call",
      expect.objectContaining({ method: "autonomy.publish_disable" }),
    );
    fireEvent.click(screen.getByRole("button", { name: /Confirm: disable autonomous publishing/ }));

    await waitFor(() => {
      expect(invoke).toHaveBeenCalledWith(
        "engine_call",
        expect.objectContaining({ method: "autonomy.publish_disable" }),
      );
    });
    await waitFor(() => {
      expect(screen.getByText("AUTONOMOUS PUBLIC PUBLISHING DISABLED")).toBeInTheDocument();
    });
  });
});