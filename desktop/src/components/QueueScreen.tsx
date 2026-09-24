import { useMemo, useState } from "react";
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

const QUICK_CATEGORIES = [
  { id: "all", label: "All Items" },
  { id: "running", label: "Running" },
  { id: "succeeded", label: "Completed" },
  { id: "failed", label: "Failed / Blocked" },
  { id: "review", label: "Needs Review" },
] as const;

const DATE_OPTIONS = [
  { id: "all", label: "All time" },
  { id: "today", label: "Today" },
  { id: "24h", label: "Last 24 hours" },
  { id: "7d", label: "Last 7 days" },
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
              <div>
                <span>{item.topic ?? item.job_id}</span>
                {item.attempt_count > 0 ? (
                  <span className="muted small"> attempt {item.attempt_count}</span>
                ) : null}
              </div>
              {item.last_error ? (
                <div className="queue-item-error" title={item.last_error}>
                  ⚠️ {item.last_error}
                </div>
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
  const [categoryFilter, setCategoryFilter] = useState<(typeof QUICK_CATEGORIES)[number]["id"]>("all");
  const [dateFilter, setDateFilter] = useState<(typeof DATE_OPTIONS)[number]["id"]>("all");
  const [search, setSearch] = useState("");

  const { data, isLoading, isError } = useQueueQuery({
    status: statusFilter,
    search: search || undefined,
  });

  const filteredItems = useMemo(() => {
    if (!data?.items) return [];
    let items = data.items;

    // Quick category filtering
    if (categoryFilter === "running") {
      items = items.filter((i) => i.status === "running");
    } else if (categoryFilter === "succeeded") {
      items = items.filter((i) => i.status === "succeeded");
    } else if (categoryFilter === "failed") {
      items = items.filter((i) => ["failed", "dead_letter", "blocked"].includes(i.status));
    } else if (categoryFilter === "review") {
      items = items.filter((i) => ["retry_wait", "blocked"].includes(i.status));
    }

    // Date filtering
    if (dateFilter !== "all") {
      const now = Date.now();
      const cutoff =
        dateFilter === "today"
          ? new Date().setHours(0, 0, 0, 0)
          : dateFilter === "24h"
            ? now - 24 * 3600 * 1000
            : now - 7 * 24 * 3600 * 1000;

      items = items.filter((i) => {
        const ts = i.started_at ?? i.scheduled_at ?? i.completed_at;
        if (!ts) return false;
        const t = new Date(ts).getTime();
        return !Number.isNaN(t) && t >= cutoff;
      });
    }

    return items;
  }, [data?.items, categoryFilter, dateFilter]);

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
        <select
          className="select-input"
          value={dateFilter}
          onChange={(e) => setDateFilter(e.target.value as (typeof DATE_OPTIONS)[number]["id"])}
          aria-label="Filter by date"
        >
          {DATE_OPTIONS.map((d) => (
            <option key={d.id} value={d.id}>
              {d.label}
            </option>
          ))}
        </select>
      </div>

      <div className="filter-tabs" style={{ marginBottom: "16px" }}>
        {QUICK_CATEGORIES.map((c) => (
          <button
            key={c.id}
            className={`filter-chip ${categoryFilter === c.id ? "active" : ""}`}
            onClick={() => setCategoryFilter(c.id)}
            type="button"
          >
            {c.label}
          </button>
        ))}
      </div>

      {data ? <SummaryStats summary={data.summary} /> : null}

      <div className="card">
        <div className="card-header">
          <span>Queue items</span>
          <span className="muted">
            {filteredItems.length} shown{data?.items ? ` (of ${data.items.length})` : ""}
          </span>
        </div>
        {isLoading ? (
          <div className="card-body muted">Loading queue…</div>
        ) : isError || !data ? (
          <div className="card-body muted">Queue unavailable</div>
        ) : (
          <QueueTable items={filteredItems} />
        )}
      </div>
    </div>
  );
}