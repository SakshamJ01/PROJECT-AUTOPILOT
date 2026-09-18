import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import PublishingScreen from "../components/PublishingScreen";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
}));

import { invoke } from "@tauri-apps/api/core";
import type { Mock } from "vitest";

const baseSummary = {
  counts: {},
  ready_to_publish: 1,
  published: 2,
  publish_failures: 0,
  awaiting_approval: 1,
  approved: 1,
  rejected: 0,
  learning: { status: "AVAILABLE", current_strategy_version: "strat-v1" },
  youtube: {
    status: "needs_auth",
    authenticated: false,
    secrets_present: true,
    guidance: "Run the YouTube OAuth flow on the backend.",
  },
  default_visibility: "private",
  autonomy_auto_publish_enabled: false,
};

const authStatus = {
  status: "needs_auth",
  authenticated: false,
  secrets_present: true,
  guidance: "Run the YouTube OAuth flow on the backend (autopilot youtube auth).",
};

const switchStatus = {
  enabled: false,
  state: "DISABLED",
  label: "AUTONOMOUS PUBLIC PUBLISHING DISABLED",
  default: false,
  controlled_by: "backend",
  guardrails: ["QA PASS required", "kill switch"],
  boundary: "Autonomous public publishing is disabled by default.",
};

function makeReadyItem(overrides: Record<string, unknown> = {}) {
  return {
    job_id: "job-ready-1",
    topic: "Quantum Computing Basics",
    channel_id: "default",
    status: "APPROVED",
    approval_status: null,
    approval_id: null,
    qa_status: "PASS",
    qa_publish_allowed: true,
    media_checksum_sha256: "abc123def456",
    approved_checksum: null,
    checksum_matches: false,
    published: false,
    remote_video_id: null,
    visibility: null,
    published_at: null,
    idempotency_key: null,
    publication_count: 0,
    ...overrides,
  };
}

function renderScreen() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <PublishingScreen />
    </QueryClientProvider>,
  );
}

function mockAll(itemOverrides: Record<string, unknown> = {}) {
  const item = makeReadyItem(itemOverrides);
  (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
    if (args.method === "publishing.status")
      return Promise.resolve({ ...baseSummary, status: "AVAILABLE", publish_boundary: "Approval required." });
    if (args.method === "publishing.list_ready")
      return Promise.resolve({ items: [item], summary: baseSummary });
    if (args.method === "youtube.auth_status") return Promise.resolve(authStatus);
    if (args.method === "autonomy.publish_status") return Promise.resolve(switchStatus);
    return Promise.reject(new Error(`unexpected ${args.method}`));
  });
  return item;
}

beforeEach(() => {
  useUiStoreReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

function useUiStoreReset() {
  // no-op placeholder for symmetry with other screens
}

describe("Publishing screen", () => {
  it("renders summary cards and the kill-switch banner as DISABLED", async () => {
    mockAll();
    renderScreen();

    await waitFor(() =>
      expect(screen.getAllByText("Ready to publish").length).toBeGreaterThan(0),
    );
    expect(screen.getByText("Awaiting approval")).toBeInTheDocument();
    expect(screen.getAllByText("Published").length).toBeGreaterThan(0);
    // Kill switch visible and disabled by default.
    await waitFor(() =>
      expect(
        screen.getByText(/AUTONOMOUS PUBLIC PUBLISHING DISABLED/),
      ).toBeInTheDocument(),
    );
  });

  it("shows an explicit unavailable state when no ready jobs exist", async () => {
    (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
      if (args.method === "publishing.status")
        return Promise.resolve({ ...baseSummary, status: "AVAILABLE", publish_boundary: "x" });
      if (args.method === "publishing.list_ready")
        return Promise.resolve({ items: [], summary: baseSummary });
      if (args.method === "youtube.auth_status") return Promise.resolve(authStatus);
      if (args.method === "autonomy.publish_status") return Promise.resolve(switchStatus);
      return Promise.reject(new Error(`unexpected ${args.method}`));
    });
    renderScreen();

    await waitFor(() =>
      expect(screen.getByText(/No READY_TO_PUBLISH jobs/)).toBeInTheDocument(),
    );
  });

  it("renders ready jobs with QA and approval state", async () => {
    mockAll();
    renderScreen();

    await waitFor(() => expect(screen.getByText("job-ready-1")).toBeInTheDocument());
    expect(screen.getByText("Quantum Computing Basics")).toBeInTheDocument();
    expect(screen.getByText("PASS")).toBeInTheDocument();
    expect(screen.getAllByText("none").length).toBeGreaterThan(0);
  });

  it("shows YouTube auth status without exposing secrets", async () => {
    mockAll();
    renderScreen();

    await waitFor(() => expect(screen.getByText("NEEDS_AUTH")).toBeInTheDocument());
    expect(screen.getByText(/Run the YouTube OAuth flow/)).toBeInTheDocument();
    // No token material in the DOM.
    expect(screen.queryByText(/access_token/i)).toBeNull();
    expect(screen.queryByText(/refresh_token/i)).toBeNull();
  });

  it("approves a job via the Approve action", async () => {
    const item = mockAll();
    let approved: Record<string, unknown> | null = null;
    (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
      if (args.method === "publishing.status")
        return Promise.resolve({ ...baseSummary, status: "AVAILABLE", publish_boundary: "x" });
      if (args.method === "publishing.list_ready")
        return Promise.resolve({ items: [item], summary: baseSummary });
      if (args.method === "youtube.auth_status") return Promise.resolve(authStatus);
      if (args.method === "autonomy.publish_status") return Promise.resolve(switchStatus);
      if (args.method === "publishing.approve") {
        approved = args;
        return Promise.resolve({ job_id: item.job_id, status: "approved" });
      }
      return Promise.reject(new Error(`unexpected ${args.method}`));
    });

    renderScreen();
    await waitFor(() => expect(screen.getByText("Approve")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Approve"));

    await waitFor(() => expect(approved).not.toBeNull());
    expect(approved).not.toBeNull();
  });

  it("reject flow requires confirmation", async () => {
    const item = mockAll({ approval_status: "approved" });
    let rejected = false;
    (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
      if (args.method === "publishing.status")
        return Promise.resolve({ ...baseSummary, status: "AVAILABLE", publish_boundary: "x" });
      if (args.method === "publishing.list_ready")
        return Promise.resolve({ items: [item], summary: baseSummary });
      if (args.method === "youtube.auth_status") return Promise.resolve(authStatus);
      if (args.method === "autonomy.publish_status") return Promise.resolve(switchStatus);
      if (args.method === "publishing.reject") {
        rejected = true;
        return Promise.resolve({ job_id: item.job_id, status: "rejected" });
      }
      return Promise.reject(new Error(`unexpected ${args.method}`));
    });

    renderScreen();
    await waitFor(() => expect(screen.getByText("Reject")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Reject"));
    // First click arms confirmation, does not reject yet.
    expect(rejected).toBe(false);
    expect(screen.getByText("Confirm reject?")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Confirm reject?"));
    await waitFor(() => expect(rejected).toBe(true));
  });

  it("publish confirmation gates behind a two-click confirm", async () => {
    const item = mockAll({ approval_status: "approved", checksum_matches: true });
    let published: Record<string, unknown> | null = null;
    (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
      if (args.method === "publishing.status")
        return Promise.resolve({ ...baseSummary, status: "AVAILABLE", publish_boundary: "x" });
      if (args.method === "publishing.list_ready")
        return Promise.resolve({ items: [item], summary: baseSummary });
      if (args.method === "youtube.auth_status") return Promise.resolve(authStatus);
      if (args.method === "autonomy.publish_status") return Promise.resolve(switchStatus);
      if (args.method === "publishing.publish") {
        published = args;
        return Promise.resolve({
          success: true,
          status: "PUBLISHED",
          job_id: item.job_id,
          remote_video_id: "vid-123",
          remote_url: "https://www.youtube.com/watch?v=vid-123",
          error_code: null,
          error_message: null,
        });
      }
      return Promise.reject(new Error(`unexpected ${args.method}`));
    });

    renderScreen();
    await waitFor(() => expect(screen.getByText("Inspect")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Inspect"));

    await waitFor(() => expect(screen.getByText("Publish")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Publish"));
    // First click arms confirmation.
    expect(published).toBe(null);
    await waitFor(() => expect(screen.getByText(/Click Publish again to confirm/)).toBeInTheDocument());
    fireEvent.click(screen.getByText("Publish"));

    await waitFor(() => expect(published).not.toBeNull());
    expect(published).not.toBeNull();
  });

  it("surfaces publish errors without reporting success", async () => {
    const item = mockAll({ approval_status: "approved", checksum_matches: true });
    (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
      if (args.method === "publishing.status")
        return Promise.resolve({ ...baseSummary, status: "AVAILABLE", publish_boundary: "x" });
      if (args.method === "publishing.list_ready")
        return Promise.resolve({ items: [item], summary: baseSummary });
      if (args.method === "youtube.auth_status") return Promise.resolve(authStatus);
      if (args.method === "autonomy.publish_status") return Promise.resolve(switchStatus);
      if (args.method === "publishing.publish")
        return Promise.resolve({
          success: false,
          status: "BLOCKED_APPROVAL",
          job_id: item.job_id,
          error_code: "APPROVAL_REQUIRED",
          error_message: "Explicit operator approval is required.",
          remote_video_id: null,
          remote_url: null,
        });
      return Promise.reject(new Error(`unexpected ${args.method}`));
    });

    renderScreen();
    fireEvent.click(await screen.findByText("Inspect"));
    await waitFor(() => expect(screen.getByText("Publish")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Publish"));
    await waitFor(() => expect(screen.getByText(/Click Publish again to confirm/)).toBeInTheDocument());
    fireEvent.click(screen.getByText("Publish"));

    await waitFor(() =>
      expect(screen.getByText(/APPROVAL_REQUIRED: Explicit operator approval is required/)).toBeInTheDocument(),
    );
  });

  it("offers private/unlisted/public visibility only", async () => {
    const item = mockAll({ approval_status: "approved", checksum_matches: true });
    (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
      if (args.method === "publishing.status")
        return Promise.resolve({ ...baseSummary, status: "AVAILABLE", publish_boundary: "x" });
      if (args.method === "publishing.list_ready")
        return Promise.resolve({ items: [item], summary: baseSummary });
      if (args.method === "youtube.auth_status") return Promise.resolve(authStatus);
      if (args.method === "autonomy.publish_status") return Promise.resolve(switchStatus);
      return Promise.reject(new Error(`unexpected ${args.method}`));
    });

    renderScreen();
    fireEvent.click(await screen.findByText("Inspect"));
    const select = await screen.findByLabelText("Publish visibility");
    const options = Array.from(select.querySelectorAll("option")).map((o) => o.textContent);
    expect(options).toEqual(["private", "unlisted", "public"]);
  });

  it("warns when public visibility is selected", async () => {
    const item = mockAll({ approval_status: "approved", checksum_matches: true });
    (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
      if (args.method === "publishing.status")
        return Promise.resolve({ ...baseSummary, status: "AVAILABLE", publish_boundary: "x" });
      if (args.method === "publishing.list_ready")
        return Promise.resolve({ items: [item], summary: baseSummary });
      if (args.method === "youtube.auth_status") return Promise.resolve(authStatus);
      if (args.method === "autonomy.publish_status") return Promise.resolve(switchStatus);
      return Promise.reject(new Error(`unexpected ${args.method}`));
    });

    renderScreen();
    fireEvent.click(await screen.findByText("Inspect"));
    const select = await screen.findByLabelText("Publish visibility");
    fireEvent.change(select, { target: { value: "public" } });

    await waitFor(() =>
      expect(screen.getByText(/Public visibility requires explicit operator approval/)).toBeInTheDocument(),
    );
  });

  it("kill switch disable requires confirmation and calls the backend", async () => {
    const item = makeReadyItem();
    let current = {
      enabled: true,
      state: "ENABLED",
      label: "AUTONOMOUS PUBLIC PUBLISHING ENABLED",
      default: false,
      controlled_by: "backend",
      guardrails: ["QA PASS required"],
      boundary: "Autonomous public publishing is disabled by default.",
      controlled_at: "2026-09-18T01:00:00",
      timestamp: "2026-09-18T01:00:01",
    };
    (invoke as unknown as Mock).mockImplementation((_cmd: string, args: { method: string }) => {
      if (args.method === "publishing.status")
        return Promise.resolve({ ...baseSummary, status: "AVAILABLE", publish_boundary: "x" });
      if (args.method === "publishing.list_ready")
        return Promise.resolve({ items: [item], summary: baseSummary });
      if (args.method === "youtube.auth_status") return Promise.resolve(authStatus);
      if (args.method === "autonomy.publish_status") return Promise.resolve(current);
      if (args.method === "autonomy.publish_disable") {
        current = {
          ...switchStatus,
          controlled_at: "2026-09-18T02:00:00",
          timestamp: "2026-09-18T02:00:01",
        };
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

    await waitFor(() =>
      expect(screen.getByText(/AUTONOMOUS PUBLIC PUBLISHING ENABLED/)).toBeInTheDocument(),
    );
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
    await waitFor(() =>
      expect(screen.getByText(/AUTONOMOUS PUBLIC PUBLISHING DISABLED/)).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: /kill switch/i })).not.toBeInTheDocument();
  });

  it("unpublishable job displays readiness badge and disables publish button with reason", async () => {
    mockAll({
      approval_status: null,
      publishable: false,
      publishability_reason: "Explicit operator approval is required before publishing.",
    });

    renderScreen();

    // Table displays AWAITING APPROVAL readiness badge
    await waitFor(() => expect(screen.getByText("AWAITING APPROVAL")).toBeInTheDocument());

    // Inspect the job
    fireEvent.click(screen.getByText("Inspect"));

    // Check warning message and disabled publish button
    await waitFor(() =>
      expect(
        screen.getByText(/Explicit operator approval is required before publishing/),
      ).toBeInTheDocument(),
    );
    const publishBtn = screen.getByRole("button", { name: /Publish job-ready-1/i });
    expect(publishBtn).toBeDisabled();
  });

  it("genuinely publishable job displays READY badge and enables publish button", async () => {
    mockAll({
      approval_status: "approved",
      checksum_matches: true,
      publishable: true,
      publishability_reason: null,
    });

    renderScreen();

    await waitFor(() => expect(screen.getByText("READY")).toBeInTheDocument());

    fireEvent.click(screen.getByText("Inspect"));

    await waitFor(() => {
      const publishBtn = screen.getByRole("button", { name: /Publish job-ready-1/i });
      expect(publishBtn).not.toBeDisabled();
    });
  });
});
