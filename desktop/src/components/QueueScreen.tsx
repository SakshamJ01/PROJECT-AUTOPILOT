import { useState } from "react";
import { useQueueQuery } from "../api/hooks";
import { useUiStore } from "../state/ui";
import StatusBadge from "./StatusBadge";
import type { QueueItem, QueueSummary } from "../api/types";

const STATUS_OPTIONS = [
  "all",
  "queued",
  "running",
  "retry_wait",
  "succeeded",
  "failed",
  "blocked",
  "cancelled",
  "dead_letter",
] as const;

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

function SummaryStats({ summary }: { summary: QueueSummary }) {
  const stats: Array<{ label: string; value: number; tone: string }> = [
    { label: "Running", value: summary.running, tone: "ok" },
    { label: "Queued", value: summary.queued, tone: "ok" },
    { label: "Retry", value: summary.retry_wait, tone: "warn" },
    { label: "Failed", value: summary.failed, tone: "bad" },
    { label: "Dead-letter", value: summary.dead_letter, tone: "bad" },
    { label: "Blocked", value: summary.blocked, tone: "warn" },
    { label: "Cancelled", value: summary.cancelled, tone: "info" },
    { label: "Succeeded", value: summary.succeeded, tone: "ok" },
  ];
  return (
    <div className="summary-grid">
      {stats.map((s) => (
        <div key={s.label} className="summary-card">
          <div className="summary-value">{s.value}</div>
          <div className="summary-label">{s.label}</div>
        </div>
      ))}
    </div>
  );
}

function formatTime(ts: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleTimeString();
}

function ActionCell({ item }: { item: QueueItem }) {
  const setSelectedJobId = useUiStore((s) => s.setSelectedJobId);
  return (
    <div className="item-actions">
      <button
        className="ghost-btn"
        onClick={(e) => {
          e.stopPropagation();
          setSelectedJobId(item.job_id);
        }}
        aria-label={`Inspect ${item.job_id}`}
      >
        Inspect
      </button>
    </div>
  );
}

function QueueTable({ items }: { items: QueueItem[] }) {
  const setSelectedJobId = useUiStore((s) => s.setSelectedJobId);
  if (items.length === 0) {
    return (
      <div className="card-body muted">No queue items match the current filters.</div>
    );
  }
  return (
    <table className="queue-table">
      <thead>
        <tr>
          <th>Topic</th>
          <th>Status</th>
          <th>Stage</th>
          <th>Policy</th>
          <th>Channel</th>
          <th>Priority</th>
          <th>Created</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr key={item.queue_id} onClick={() => setSelectedJobId(item.job_id)}>
            <td>
              {item.topic ?? item.job_id}
              {item.attempt_count > 0 ? (
                <span className="muted small"> attempt {item.attempt_count}</span>
              ) : null}
            </td>
            <td>
              <StatusBadge label={item.status} tone={toneForStatus(item.status)} />
            </td>
            <td>{item.stage}</td>
            <td>{item.policy ?? "—"}</td>
            <td>{item.channel_id ?? "—"}</td>
            <td>{item.priority}</td>
            <td className="log-time">{formatTime(item.started_at ?? item.scheduled_at)}</td>
            <td>
              <ActionCell item={item} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function QueueScreen() {
  const [statusFilter, setStatusFilter] = useState<(typeof STATUS_OPTIONS)[number]>("all");
  const [search, setSearch] = useState("");
  const { data, isLoading, isError } = useQueueQuery({
    status: statusFilter,
    search: search || undefined,
  });

  return (
    <div className="queue-screen">
      <div className="toolbar">
        <input
          type="search"
          placeholder="Search by topic or job id"
          className="text-input"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Search queue"
        />
        <select
          className="select-input"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as (typeof STATUS_OPTIONS)[number])}
          aria-label="Filter by status"
        >
          {STATUS_OPTIONS.map((s) => (
            <option key={s} value={s}>
              {s === "all" ? "All statuses" : s.replace("_", " ")}
            </option>
          ))}
        </select>
      </div>
      {data ? <SummaryStats summary={data.summary} /> : null}
      <div className="card">
        <div className="card-header">
          <span>Queue items</span>
          <span className="muted">{data?.items.length ?? 0} shown</span>
        </div>
        {isLoading ? (
          <div className="card-body muted">Loading queue…</div>
        ) : isError || !data ? (
          <div className="card-body muted">Queue unavailable</div>
        ) : (
          <QueueTable items={data.items} />
        )}
      </div>
    </div>
  );
}