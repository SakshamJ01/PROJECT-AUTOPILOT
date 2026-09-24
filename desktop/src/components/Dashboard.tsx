import {
  useAnalyticsStatusQuery,
  useAutonomyPublishStatusQuery,
  useErrorsQuery,
  useHealthQuery,
  useProductionEngineStatusQuery,
  usePublishingStatusQuery,
  useQueueQuery,
  useStrategyStatusQuery,
} from "../api/hooks";
import { useUiStore } from "../state/ui";
import StatusBadge from "./StatusBadge";

function toneForStatus(status: string): "ok" | "bad" | "warn" | "info" {
  switch (status) {
    case "AVAILABLE":
    case "healthy":
    case "active":
    case "running":
    case "configured":
      return "ok";
    case "degraded":
    case "unconfigured":
    case "retry_wait":
    case "blocked":
    case "standby":
      return "warn";
    default:
      return status === "failed" || status === "dead_letter" ? "bad" : "info";
  }
}

function HealthCard({ title, value }: { title: string; value: string }) {
  return (
    <div className="health-card">
      <div className="health-card-title">{title}</div>
      <StatusBadge label={value} tone={toneForStatus(value)} />
    </div>
  );
}

function HealthGrid() {
  const { data, isLoading, isError } = useHealthQuery();
  const { data: mpt } = useProductionEngineStatusQuery();
  if (isLoading) return <div className="card-body muted">Loading health…</div>;
  if (isError || !data)
    return <div className="card-body muted">Health unavailable</div>;
  return (
    <>
      <HealthCard title="Database" value={data.db.exists ? "AVAILABLE" : "missing"} />
      <HealthCard title="Scheduler" value={data.scheduler.status} />
      <HealthCard title="Queue" value={data.queue_engine.status} />
      <HealthCard title="Worker" value={data.worker.status} />
      <HealthCard title="MPT Renderer" value={mpt?.running ? "active" : "standby"} />
      <HealthCard title="QA Engine" value={data.qa_engine.status} />
      <HealthCard title="Analytics" value={data.analytics_engine.status} />
      <HealthCard title="Publishing" value={data.publishing.status} />
      <HealthCard title="Learning" value={data.learning_engine.status} />
    </>
  );
}

function ReadyPane() {
  const { data } = useHealthQuery();
  if (!data) return null;
  const readyCount = data.publishing.counts["ready"] ?? 0;
  const publishedCount = data.publishing.counts["published"] ?? 0;
  return (
    <div className="card">
      <div className="card-header">
        <span>Publishing readiness</span>
        <span className="muted">
          {readyCount} ready · {publishedCount} published
        </span>
      </div>
      <div className="ready-row">
        <span>Next schedule: {data.scheduler.next_schedule ?? "not scheduled"}</span>
        <StatusBadge
          label={data.autonomy_auto_publish_enabled ? "auto-publish on" : "auto-publish off"}
          tone={data.autonomy_auto_publish_enabled ? "warn" : "info"}
        />
      </div>
    </div>
  );
}

function QueueTable() {
  const { data, isLoading, isError } = useQueueQuery();
  const setSelectedJobId = useUiStore((s) => s.setSelectedJobId);

  if (isLoading) return <div className="card-body muted">Loading queue…</div>;
  if (isError || !data) return <div className="card-body muted">Queue unavailable</div>;

  const summary = data.summary;
  return (
    <div className="card">
      <div className="card-header">
        <span>Queue</span>
        <span className="muted">
          {summary.running} running · {summary.queued} queued · {summary.failed} failed ·{" "}
          {summary.succeeded} succeeded
        </span>
      </div>
      <table className="queue-table">
        <thead>
          <tr>
            <th>Topic</th>
            <th>Status</th>
            <th>Stage</th>
            <th>Priority</th>
            <th>Channel</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((item) => (
            <tr key={item.queue_id} onClick={() => setSelectedJobId(item.job_id)}>
              <td>{item.topic ?? item.job_id}</td>
              <td>
                <StatusBadge label={item.status} tone={toneForStatus(item.status)} />
              </td>
              <td>{item.stage}</td>
              <td>{item.priority}</td>
              <td>{item.channel_id ?? "—"}</td>
            </tr>
          ))}
          {data.items.length === 0 ? (
            <tr>
              <td colSpan={5} className="muted">
                No queue items
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}

function M4SummaryGrid() {
  const { data: pub } = usePublishingStatusQuery();
  const { data: analytics } = useAnalyticsStatusQuery();
  const { data: strategy } = useStrategyStatusQuery();
  const { data: switchState } = useAutonomyPublishStatusQuery();
  const setPage = useUiStore((s) => s.setPage);

  const cards: Array<{
    label: string;
    value: number | string;
    badge: string;
    tone: "ok" | "warn" | "bad" | "info";
    page: "publishing" | "analytics" | "strategy";
  }> = [
    {
      label: "Ready to Publish",
      value: pub?.ready_to_publish ?? "—",
      badge: (pub?.ready_to_publish ?? 0) > 0 ? "awaiting" : "idle",
      tone: (pub?.ready_to_publish ?? 0) > 0 ? "warn" : "info",
      page: "publishing" as const,
    },
    {
      label: "Published",
      value: pub?.published ?? "—",
      badge: "total",
      tone: "ok",
      page: "publishing" as const,
    },
    {
      label: "Analytics Status",
      value: analytics?.has_published_jobs
        ? `${analytics.snapshot_count} snapshots`
        : "no data",
      badge: analytics?.has_published_jobs ? "synced" : "none",
      tone: analytics?.has_published_jobs ? "ok" : "info",
      page: "analytics" as const,
    },
    {
      label: "Active Strategy",
      value: strategy?.active_strategy_version ?? "—",
      badge: "active",
      tone: "ok",
      page: "strategy" as const,
    },
    {
      label: "Last Sync",
      value: analytics?.last_observed_at
        ? new Date(analytics.last_observed_at).toLocaleDateString()
        : "never",
      badge: analytics?.last_observed_at ? "recent" : "never",
      tone: analytics?.last_observed_at ? "ok" : "info",
      page: "analytics" as const,
    },
    {
      label: "Autonomous Public Publish",
      value: switchState ? (switchState.enabled ? "ON" : "OFF") : "—",
      badge: switchState?.enabled ? "ENABLED" : "DISABLED",
      tone: switchState?.enabled ? "bad" : "ok",
      page: "publishing" as const,
    },
  ];

  return (
    <div className="summary-grid">
      {cards.map((c) => (
        <button
          key={c.label}
          className="summary-card"
          onClick={() => setPage(c.page)}
          style={{ cursor: "pointer", textAlign: "left" }}
          aria-label={`${c.label}: ${c.value} (${c.badge})`}
        >
          <div className="summary-value">{c.value}</div>
          <div className="summary-label">{c.label}</div>
          <div style={{ marginTop: "6px" }}>
            <StatusBadge label={c.badge} tone={c.tone} />
          </div>
        </button>
      ))}
    </div>
  );
}

function RecentFailuresCard() {
  const { data } = useErrorsQuery({ limit: 5 });
  const setSelectedJobId = useUiStore((s) => s.setSelectedJobId);
  const setJobDrawerTab = useUiStore((s) => s.setJobDrawerTab);

  if (!data || data.errors.length === 0) return null;

  return (
    <div className="card" style={{ borderColor: "rgba(229, 72, 77, 0.4)" }}>
      <div className="card-header">
        <span style={{ color: "var(--bad)", fontWeight: 600 }}>⚠️ Recent Failures & Errors</span>
        <span className="muted">{data.total} recorded</span>
      </div>
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
        {data.errors.slice(0, 3).map((err) => (
          <div
            key={err.error_id}
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              padding: "6px 8px",
              background: "rgba(229, 72, 77, 0.08)",
              borderRadius: "4px",
              cursor: err.job_id ? "pointer" : "default",
            }}
            onClick={() => {
              if (err.job_id) {
                setSelectedJobId(err.job_id);
                setJobDrawerTab("errors");
              }
            }}
          >
            <div>
              <span className="status-badge bad" style={{ marginRight: "8px" }}>
                {err.error_type}
              </span>
              <span style={{ fontWeight: 500 }}>{err.topic ?? err.job_id ?? "System"}</span>
              <span className="muted small" style={{ marginLeft: "8px" }}>
                ({err.stage})
              </span>
              <div className="small" style={{ marginTop: "2px", color: "var(--text)" }}>
                {err.message}
              </div>
            </div>
            <div className="muted small">{new Date(err.occurred_at).toLocaleTimeString()}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Dashboard() {
  return (
    <div className="dashboard">
      <div className="health-grid">
        <HealthGrid />
      </div>
      <RecentFailuresCard />
      <M4SummaryGrid />
      <ReadyPane />
      <QueueTable />
    </div>
  );
}