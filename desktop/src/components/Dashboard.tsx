import { useHealthQuery, useQueueQuery } from "../api/hooks";
import { useUiStore } from "../state/ui";
import StatusBadge from "./StatusBadge";

function toneForStatus(status: string): "ok" | "bad" | "warn" | "info" {
  switch (status) {
    case "AVAILABLE":
    case "healthy":
    case "active":
    case "configured":
      return "ok";
    case "degraded":
    case "unconfigured":
    case "retry_wait":
    case "blocked":
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
  if (isLoading) return <div className="card-body muted">Loading health…</div>;
  if (isError || !data)
    return <div className="card-body muted">Health unavailable</div>;
  return (
    <>
      <HealthCard title="Database" value={data.db.exists ? "AVAILABLE" : "missing"} />
      <HealthCard title="Scheduler" value={data.scheduler.status} />
      <HealthCard title="Queue" value={data.queue_engine.status} />
      <HealthCard title="Worker" value={data.worker.status} />
      <HealthCard title="Production engine" value={data.qa_engine.status} />
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

export default function Dashboard() {
  return (
    <div className="dashboard">
      <div className="health-grid">
        <HealthGrid />
      </div>
      <ReadyPane />
      <QueueTable />
    </div>
  );
}