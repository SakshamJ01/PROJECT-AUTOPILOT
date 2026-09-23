import { useMemo, useState } from "react";
import {
  useJobInspectQuery,
  useProductionCancelMutation,
  useProductionEngineEnsureMutation,
  useProductionEngineStatusQuery,
  useProductionRetryMutation,
  useProductionStartMutation,
  useQueueQuery,
} from "../api/hooks";
import { useUiStore } from "../state/ui";
import StatusBadge from "./StatusBadge";
import ErrorBanner from "./ErrorBanner";
import type { QueueItem } from "../api/types";

export const STAGES = [
  "RESEARCH",
  "SCRIPT",
  "VOICE",
  "ASSETS",
  "RENDER",
  "QA",
  "PUBLISH",
  "COMPLETE",
] as const;

const POLICIES = ["local_only", "ollama", "cheap_first", "mock"] as const;
const PROFILES = ["short_vertical", "long_form", "podcast"] as const;

function toneForStatus(status: string): "ok" | "bad" | "warn" | "info" {
  switch (status) {
    case "queued":
    case "running":
    case "succeeded":
      return "ok";
    case "retry_wait":
    case "blocked":
      return "warn";
    case "failed":
    case "dead_letter":
      return "bad";
    default:
      return "info";
  }
}

const isTerminal = (s: string) =>
  ["succeeded", "failed", "cancelled", "blocked", "dead_letter"].includes(s);

/** Pick the focused item: active first, then most recently started, else latest. */
function selectFocused(items: QueueItem[]): QueueItem | null {
  if (items.length === 0) return null;
  const active = items.find((i) => i.status === "running");
  if (active) return active;
  const claimed = items.find((i) => !isTerminal(i.status));
  if (claimed) return claimed;
  return [...items].sort((a, b) =>
    String(b.started_at ?? b.completed_at ?? "").localeCompare(
      String(a.started_at ?? a.completed_at ?? ""),
    ),
  )[0];
}

function fileBase(path: string): string {
  const clean = path.replace(/\\/g, "/");
  const parts = clean.split("/");
  return parts[parts.length - 1] || path;
}

function StageTimeline({ current }: { current: string }) {
  const idx = STAGES.indexOf(current as (typeof STAGES)[number]);
  return (
    <ol className="stage-timeline">
      {STAGES.map((stage, i) => {
        const done = idx > i;
        const active = idx === i;
        const reachable = idx >= 0;
        return (
          <li
            key={stage}
            className={
              done
                ? "stage-done"
                : active
                  ? "stage-active"
                  : reachable
                    ? "stage-pending"
                    : "stage-future"
            }
          >
            <span className="stage-dot" />
            <span className="stage-name">{stage}</span>
          </li>
        );
      })}
    </ol>
  );
}

function JobStatusCard({ item }: { item: QueueItem }) {
  const setJobDrawerTab = useUiStore((s) => s.setJobDrawerTab);
  const setSelectedJobId = useUiStore((s) => s.setSelectedJobId);
  const showTab = (tab: "artifacts" | "errors" | "timeline") => {
    setSelectedJobId(item.job_id);
    setJobDrawerTab(tab);
  };

  return (
    <div className="card">
      <div className="card-header">
        <span>{item.topic ?? item.job_id}</span>
        <StatusBadge label={item.status} tone={toneForStatus(item.status)} />
      </div>
      <div className="card-body">
        <dl className="kv">
          <dt>Job</dt>
          <dd>{item.job_id}</dd>
          <dt>Channel</dt>
          <dd>{item.channel_id ?? "—"}</dd>
          <dt>Policy</dt>
          <dd>{item.policy ?? "—"}</dd>
          <dt>Profile</dt>
          <dd>{item.profile ?? "—"}</dd>
          {item.last_error ? (
            <>
              <dt>Last error</dt>
              <dd className="err-type">{item.last_error}</dd>
            </>
          ) : null}
        </dl>
        <StageTimeline current={item.stage} />
        <div className="job-links">
          <button className="ghost-btn" onClick={() => showTab("timeline")}>
            Timeline
          </button>
          <button className="ghost-btn" onClick={() => showTab("artifacts")}>
            Artifacts
          </button>
          <button className="ghost-btn" onClick={() => showTab("errors")}>
            Errors
          </button>
        </div>
      </div>
    </div>
  );
}

function ProvidersCard({ item }: { item: QueueItem }) {
  const providers = item.providers;
  if (!providers || Object.keys(providers).length === 0) {
    return (
      <div className="card">
        <div className="card-header">
          <span>Providers</span>
        </div>
        <div className="card-body muted">Resolved inside the pipeline.</div>
      </div>
    );
  }
  return (
    <div className="card">
      <div className="card-header">
        <span>Providers (resolved)</span>
      </div>
      <dl className="kv">
        {Object.entries(providers).map(([key, value]) => (
          <div key={key} className="kv-row">
            <dt>{key}</dt>
            <dd>{String(value)}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function useFocusedJobId(items: QueueItem[] | undefined): string | null {
  const focused = useMemo(() => (items ? selectFocused(items) : null), [items]);
  return focused?.job_id ?? null;
}

function ProductionDetail({ item }: { item: QueueItem }) {
  const { data, isLoading, isError } = useJobInspectQuery(item.job_id);
  const artifacts = data?.artifacts ?? [];
  const errors = data?.errors ?? [];

  return (
    <div className="production-grid">
      <JobStatusCard item={item} />
      <ProvidersCard item={item} />
      <div className="card">
        <div className="card-header">
          <span>Artifacts</span>
          <span className="muted">{artifacts.length}</span>
        </div>
        <div className="artifact-grid">
          {isLoading ? <span className="muted card-body">Loading…</span> : null}
          {isError ? <span className="muted card-body">Unavailable</span> : null}
          {!isLoading && !isError && artifacts.length === 0 ? (
            <span className="muted card-body">No artifacts yet</span>
          ) : null}
          {artifacts.map((a) => {
            const path = String((a as Record<string, unknown>).artifact_path ?? "");
            const type = String((a as Record<string, unknown>).artifact_type ?? "artifact");
            return (
              <div
                key={String((a as Record<string, unknown>).artifact_id ?? path)}
                className={`artifact-card ${type === "media" ? "artifact-media" : ""}`}
              >
                <StatusBadge label={type} tone={type === "media" ? "ok" : "info"} />
                <span className="artifact-name" title={path}>
                  {fileBase(path)}
                </span>
              </div>
            );
          })}
        </div>
      </div>
      <div className="card">
        <div className="card-header">
          <span>Errors</span>
          <span className="muted">{errors.length}</span>
        </div>
        <ul className="plain-list">
          {errors.length === 0 ? (
            <li className="muted">None</li>
          ) : (
            errors.map((err) => (
              <li key={err.error_id}>
                <span className="err-type">{err.error_type}</span> {err.stage ? `@ ${err.stage} · ` : ""}
                {err.message}
              </li>
            ))
          )}
        </ul>
      </div>
    </div>
  );
}

type EnsureMutation = ReturnType<typeof useProductionEngineEnsureMutation>;

function ProductionEngineCard({ ensure }: { ensure: EnsureMutation }) {
  const status = useProductionEngineStatusQuery();
  const [dismissed, setDismissed] = useState(false);

  const data = status.data;
  const error = ensure.isError
    ? ((ensure.error as Error)?.message ?? "Failed to start the production engine")
    : ensure.data && !ensure.data.running
      ? (ensure.data.error ?? "MoneyPrinterTurbo is not running")
      : null;

  if (error && !dismissed) {
    return (
      <div className="banner banner-warn">
        <strong>Production engine unavailable:</strong> {error}
        <div className="start-form-actions">
          <button
            className="primary-btn"
            type="button"
            disabled={ensure.isPending}
            onClick={() => {
              setDismissed(false);
              ensure.mutate();
            }}
          >
            {ensure.isPending ? "Starting engine…" : "Retry engine startup"}
          </button>
          <button type="button" onClick={() => setDismissed(true)}>
            Dismiss
          </button>
        </div>
      </div>
    );
  }

  if (!data) return null;
  const tone = data.running ? "ok" : "warn";
  const label = data.running
    ? `Production engine ready — ${data.engine} ${data.version}${data.managed ? ` (pid ${data.pid})` : ""}`
    : `Production engine not running — ${data.engine} ${data.version} will start on next production`;
  return (
    <div className={`banner banner-${tone === "ok" ? "info" : "warn"}`}>
      {label}
      {!data.running ? (
        <div className="start-form-actions">
          <button
            className="primary-btn"
            type="button"
            disabled={ensure.isPending}
            onClick={() => ensure.mutate()}
          >
            {ensure.isPending ? "Starting engine…" : "Start engine now"}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function StartForm({ onDone }: { onDone: () => void }) {
  const mutation = useProductionStartMutation();
  const ensure = useProductionEngineEnsureMutation();
  const [topic, setTopic] = useState("");
  const [channel, setChannel] = useState("default");
  const [policy, setPolicy] = useState<(typeof POLICIES)[number]>("local_only");
  const [profile, setProfile] = useState<(typeof PROFILES)[number]>("short_vertical");

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!topic.trim()) return;
    // Guarantee the MoneyPrinterTurbo service is up before the render stage;
    // its error is surfaced in the engine card rather than mid-pipeline.
    const runStart = () =>
      mutation.mutate(
        { topic: topic.trim(), channel, policy, profile },
        {
          onSuccess: () => {
            setTopic("");
            onDone();
          },
        },
      );
    if (!ensure.data?.running) {
      ensure.mutate(undefined, { onSuccess: (res) => res.running && runStart() });
    } else {
      runStart();
    }
  };

  return (
    <form className="start-form" onSubmit={submit}>
      <div className="start-form-row">
        <label>
          Topic
          <input
            className="text-input"
            type="text"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="What should the video be about?"
            aria-label="Production topic"
          />
        </label>
        <label>
          Channel
          <input
            className="text-input"
            type="text"
            value={channel}
            onChange={(e) => setChannel(e.target.value)}
            aria-label="Channel"
          />
        </label>
        <label>
          Policy
          <select
            className="select-input"
            value={policy}
            onChange={(e) => setPolicy(e.target.value as (typeof POLICIES)[number])}
            aria-label="Production policy"
          >
            {POLICIES.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        <label>
          Profile
          <select
            className="select-input"
            value={profile}
            onChange={(e) => setProfile(e.target.value as (typeof PROFILES)[number])}
            aria-label="Production profile"
          >
            {PROFILES.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="start-form-actions">
        <p className="muted small">
          Runs the full production pipeline on the local engine. Publishing is
          never enabled from the desktop.
        </p>
        <button
          className="primary-btn"
          type="submit"
          disabled={!topic.trim() || mutation.isPending || ensure.isPending}
        >
          {mutation.isPending
            ? "Starting…"
            : ensure.isPending
              ? "Preparing engine…"
              : "Start production"}
        </button>
      </div>
      <ProductionEngineCard ensure={ensure} />
      {mutation.isError ? (
        <ErrorBanner
          error={mutation.error}
          title="Failed to start production"
          onRetry={() => submit(new Event("submit") as unknown as React.FormEvent)}
          retryLabel="Retry Production"
        />
      ) : null}
      {mutation.data ? (
        <div className="banner banner-info">
          Started job {mutation.data.job_id} → {mutation.data.status}
        </div>
      ) : null}
    </form>
  );
}

function Controls({ item }: { item: QueueItem }) {
  const cancel = useProductionCancelMutation();
  const retry = useProductionRetryMutation();
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [confirmRetry, setConfirmRetry] = useState(false);

  const cancellable = ["queued", "running", "retry_wait"].includes(item.status);
  const retryable = ["failed", "dead_letter", "cancelled", "blocked", "retry_wait"].includes(
    item.status,
  );

  const onCancel = () => {
    if (!confirmCancel) {
      setConfirmCancel(true);
      window.setTimeout(() => setConfirmCancel(false), 3500);
      return;
    }
    cancel.mutate({ queue_id: item.queue_id });
    setConfirmCancel(false);
  };

  const onRetry = () => {
    if (!confirmRetry) {
      setConfirmRetry(true);
      window.setTimeout(() => setConfirmRetry(false), 3500);
      return;
    }
    retry.mutate({ queue_id: item.queue_id });
    setConfirmRetry(false);
  };

  return (
    <div className="controls-row">
      {cancellable ? (
        <button className="danger-btn" onClick={onCancel} disabled={cancel.isPending}>
          {confirmCancel ? "Confirm cancel?" : "Cancel"}
        </button>
      ) : null}
      {retryable ? (
        <button className="primary-btn" onClick={onRetry} disabled={retry.isPending}>
          {confirmRetry ? "Confirm retry?" : "Retry"}
        </button>
      ) : null}
    </div>
  );
}

export default function ProductionScreen() {
  const { data } = useQueueQuery();
  const items = data?.items ?? [];
  const focusedJobId = useFocusedJobId(items);
  const focused = items.find((i) => i.job_id === focusedJobId) ?? null;
  const [showForm, setShowForm] = useState(true);

  const history = items.filter((i) => i.status !== "running");
  const recent = history.slice(0, 8);

  return (
    <div className="production-screen">
      <div className="toolbar">
        <div>
          <h2 className="page-title">Production</h2>
          <p className="muted small">
            Start, monitor and control local production jobs. Publishing is
            disabled from the desktop surface.
          </p>
        </div>
        <button className="ghost-btn" onClick={() => setShowForm((v) => !v)}>
          {showForm ? "Hide form" : "New production"}
        </button>
      </div>

      {showForm ? <StartForm onDone={() => setShowForm(false)} /> : null}

      {focused ? (
        <>
          <Controls item={focused} />
          <ProductionDetail item={focused} />
        </>
      ) : (
        <div className="card-body muted">No production jobs yet.</div>
      )}

      {recent.length > 0 ? (
        <div className="card">
          <div className="card-header">
            <span>Recent jobs</span>
          </div>
          <table className="queue-table">
            <thead>
              <tr>
                <th>Topic</th>
                <th>Status</th>
                <th>Stage</th>
                <th>Channel</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((item) => (
                <tr key={item.queue_id} onClick={() => useUiStore.getState().setSelectedJobId(item.job_id)}>
                  <td>{item.topic ?? item.job_id}</td>
                  <td>
                    <StatusBadge label={item.status} tone={toneForStatus(item.status)} />
                  </td>
                  <td>{item.stage}</td>
                  <td>{item.channel_id ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}