import { useState } from "react";
import {
  useAnalyticsReportQuery,
  useAnalyticsStatusQuery,
  useAnalyticsSyncMutation,
} from "../api/hooks";
import StatusBadge from "./StatusBadge";
import type { AnalyticsReportRow, ChannelAttribution } from "../api/types";

function formatNum(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return v.toLocaleString();
}

function formatPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

function SyncControls() {
  const sync = useAnalyticsSyncMutation();
  const [dryRun, setDryRun] = useState(false);
  const [confirm, setConfirm] = useState(false);

  const onSync = () => {
    if (sync.isPending) return;
    if (!dryRun && !confirm) {
      setConfirm(true);
      window.setTimeout(() => setConfirm(false), 3500);
      return;
    }
    setConfirm(false);
    sync.mutate({ sync_all: true, dry_run: dryRun });
  };

  return (
    <div className="card">
      <div className="card-header">
        <span>Analytics synchronisation</span>
        {sync.isPending ? (
          <StatusBadge label="SYNCING" tone="warn" />
        ) : (
          <StatusBadge label="IDLE" tone="info" />
        )}
      </div>
      <div className="card-body">
        <div className="cycle-controls">
          <label className="cycle-toggle">
            <input
              type="checkbox"
              checked={dryRun}
              onChange={(e) => setDryRun(e.target.checked)}
              disabled={sync.isPending}
            />
            dry run (no API calls)
          </label>
        </div>
        <div className="item-actions" style={{ marginTop: "10px" }}>
          <button
            className="primary-btn"
            onClick={onSync}
            disabled={sync.isPending}
            aria-label="Sync analytics"
          >
            {confirm ? "Confirm sync?" : "Sync Analytics"}
          </button>
        </div>
        {sync.data ? (
          <div className="banner banner-info" style={{ marginTop: "10px" }}>
            <strong>
              {sync.data.synced_count ?? sync.data.synced ?? 0}
            </strong>{" "}
            job(s) synced of {(sync.data.total_targeted ?? sync.data.results?.length ?? 0)}
            {sync.data.note ? ` · ${sync.data.note}` : null}
          </div>
        ) : null}
        {sync.isError ? (
          <div className="banner banner-warn" style={{ marginTop: "10px" }}>
            {(sync.error as Error)?.message ?? "Analytics sync failed"}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function MetricGrid({ row }: { row: AnalyticsReportRow }) {
  return (
    <div className="summary-grid" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
      <div className="summary-card">
        <div className="summary-value">{formatNum(row.views)}</div>
        <div className="summary-label">Views</div>
      </div>
      <div className="summary-card">
        <div className="summary-value">{formatNum(row.likes)}</div>
        <div className="summary-label">Likes</div>
      </div>
      <div className="summary-card">
        <div className="summary-value">{formatNum(row.comments)}</div>
        <div className="summary-label">Comments</div>
      </div>
      <div className="summary-card">
        <div className="summary-value">{formatPct(row.engagement_rate)}</div>
        <div className="summary-label">Engagement</div>
      </div>
    </div>
  );
}

function AttributionCard({
  attribution,
}: {
  attribution: ChannelAttribution | null;
}) {
  if (!attribution) return null;
  if (attribution.status === "insufficient_data") {
    return (
      <div className="card">
        <div className="card-header">
          <span>Channel attribution</span>
          <span className="muted small">insufficient data</span>
        </div>
        <div className="card-body muted">{attribution.message}</div>
      </div>
    );
  }
  const rankings = [
    { title: "By duration", rows: attribution.top_durations },
    { title: "By hook", rows: attribution.top_hooks },
    { title: "By engine", rows: attribution.top_engines },
  ];
  return (
    <div className="card">
      <div className="card-header">
        <span>Channel attribution — {attribution.channel_id}</span>
        <span className="muted small">
          {attribution.total_videos} videos · {formatNum(attribution.average_views)} avg views
        </span>
      </div>
      <div className="production-grid">
        {rankings.map((r) => (
          <div key={r.title}>
            <h4 className="drawer-subtitle">{r.title}</h4>
            {r.rows.length === 0 ? (
              <p className="muted small">No data.</p>
            ) : (
              <table className="queue-table">
                <thead>
                  <tr>
                    <th>Category</th>
                    <th>n</th>
                    <th>Avg views</th>
                  </tr>
                </thead>
                <tbody>
                  {r.rows.map((x) => (
                    <tr key={x.category}>
                      <td className="small">{x.category}</td>
                      <td>{x.sample_size}</td>
                      <td>{formatNum(x.avg_views)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        ))}
      </div>
      <div className="card-body muted small">{attribution.recommendation}</div>
    </div>
  );
}

export default function AnalyticsScreen() {
  const { data: status, isLoading: statusLoading } = useAnalyticsStatusQuery();
  const [channelId, setChannelId] = useState("default");
  const { data, isLoading, isError } = useAnalyticsReportQuery(channelId || undefined);

  if (statusLoading || isLoading) {
    return (
      <div className="production-screen">
        <h2 className="page-title">Analytics</h2>
        <div className="card-body muted">Loading analytics…</div>
      </div>
    );
  }
  if (isError || !data) {
    return (
      <div className="production-screen">
        <h2 className="page-title">Analytics</h2>
        <div className="card-body muted">Analytics unavailable</div>
      </div>
    );
  }

  const rows = data.report;
  const hasData = rows.length > 0;

  return (
    <div className="production-screen">
      <div>
        <h2 className="page-title">Analytics</h2>
        <p className="muted small">
          Real stored analytics only. Metrics come from the existing backend
          provider; nothing is fabricated. Missing metrics show an explicit
          unavailable state.
        </p>
      </div>

      <div className="summary-grid">
        <div className="summary-card">
          <div className="summary-value">{status?.published_job_count ?? 0}</div>
          <div className="summary-label">Published jobs</div>
        </div>
        <div className="summary-card">
          <div className="summary-value">{status?.jobs_with_snapshots ?? 0}</div>
          <div className="summary-label">Jobs with snapshots</div>
        </div>
        <div className="summary-card">
          <div className="summary-value">{status?.snapshot_count ?? 0}</div>
          <div className="summary-label">Snapshots stored</div>
        </div>
        <div className="summary-card">
          <div className="summary-value">
            {status?.last_observed_at
              ? new Date(status.last_observed_at).toLocaleDateString()
              : "—"}
          </div>
          <div className="summary-label">Last observation</div>
        </div>
      </div>

      <div className="production-grid">
        <div className="card">
          <div className="card-header">
            <span>Provider</span>
            <StatusBadge
              label={status?.default_provider ?? "—"}
              tone={status?.default_provider === "mock" ? "warn" : "ok"}
            />
          </div>
          <div className="card-body">
            <div className="kv-row">
              <dt>Has published jobs</dt>
              <dd>{String(Boolean(status?.has_published_jobs))}</dd>
            </div>
            <div className="kv-row">
              <dt>YouTube auth</dt>
              <dd>{status?.youtube.status ?? "—"}</dd>
            </div>
            <div className="muted small" style={{ marginTop: "6px" }}>
              {status?.youtube.authenticated
                ? "Analytics sync is authorised."
                : "Authenticate YouTube on the backend to sync live metrics."}
            </div>
          </div>
        </div>
        <SyncControls />
      </div>

      <div className="card">
        <div className="card-header">
          <span>Performance report</span>
          <input
            className="text-input"
            type="text"
            placeholder="channel id"
            value={channelId}
            onChange={(e) => setChannelId(e.target.value)}
            aria-label="Channel id for attribution"
            style={{ maxWidth: "180px" }}
          />
        </div>
        {!hasData ? (
          <div className="card-body muted">
            No stored analytics yet. Publish content and run a sync to populate
            the report.
            {data.note ? <div className="small" style={{ marginTop: "6px" }}>{data.note}</div> : null}
          </div>
        ) : (
          <table className="queue-table">
            <thead>
              <tr>
                <th>Job</th>
                <th>Topic</th>
                <th>Platform</th>
                <th>Views</th>
                <th>Likes</th>
                <th>Comments</th>
                <th>Engagement</th>
                <th>Snapshots</th>
                <th>Observed</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.job_id}>
                  <td>{r.job_id}</td>
                  <td>{r.topic}</td>
                  <td>{r.platform}</td>
                  <td>{formatNum(r.views)}</td>
                  <td>{formatNum(r.likes)}</td>
                  <td>{formatNum(r.comments)}</td>
                  <td>{formatPct(r.engagement_rate)}</td>
                  <td>{r.snapshots_recorded}</td>
                  <td className="log-time">
                    {r.latest_observed_at
                      ? new Date(r.latest_observed_at).toLocaleString()
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {hasData ? <MetricGrid row={rows[0]} /> : null}

      <AttributionCard attribution={data.channel_attribution} />
    </div>
  );
}
