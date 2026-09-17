import { useSystemStatusQuery, useLogsQuery } from "../api/hooks";
import { useUiStore } from "../state/ui";
import StatusBadge from "./StatusBadge";
import type { LogEntry } from "../api/types";

function SystemCard() {
  const { data, isLoading, isError } = useSystemStatusQuery();
  if (isLoading) return <div className="card-body muted">Loading system status…</div>;
  if (isError || !data)
    return <div className="card-body muted">System status unavailable</div>;
  const sys = data as {
    state: string;
    pid: number;
    bridge_version: string;
    python: string;
    db: { path: string; exists: boolean; schema_version: number | null };
    artifacts_dir: string;
  };
  return (
    <div className="card">
      <div className="card-header">
        <span>System</span>
        <StatusBadge label={sys.state} tone="ok" />
      </div>
      <dl className="kv">
        <dt>PID</dt>
        <dd>{String(sys.pid)}</dd>
        <dt>Bridge</dt>
        <dd>v{sys.bridge_version}</dd>
        <dt>Python</dt>
        <dd>{sys.python}</dd>
        <dt>Database</dt>
        <dd>{sys.db.path}</dd>
        <dt>Schema</dt>
        <dd>v{String(sys.db.schema_version)}</dd>
        <dt>Artifacts</dt>
        <dd>{sys.artifacts_dir}</dd>
      </dl>
    </div>
  );
}

function formatTime(timestamp: string | null): string {
  if (!timestamp) return "—";
  const d = new Date(timestamp);
  if (Number.isNaN(d.getTime())) return timestamp;
  return d.toLocaleTimeString();
}

function LogRow({ entry }: { entry: LogEntry }) {
  return (
    <tr className={entry.severity}>
      <td className="log-time">{formatTime(entry.timestamp)}</td>
      <td>
        <StatusBadge
          label={entry.severity}
          tone={entry.severity === "error" ? "bad" : "info"}
        />
      </td>
      <td className="log-stage">{entry.stage ?? ""}</td>
      <td className="log-message">{entry.message}</td>
      <td className="muted">{entry.job_id ?? "—"}</td>
    </tr>
  );
}

function LogViewer() {
  const severityFilter = useUiStore((s) => s.severityFilter);
  const logSearch = useUiStore((s) => s.logSearch);
  const logAutoRefresh = useUiStore((s) => s.logAutoRefresh);
  const setSeverityFilter = useUiStore((s) => s.setSeverityFilter);
  const setLogSearch = useUiStore((s) => s.setLogSearch);
  const setLogAutoRefresh = useUiStore((s) => s.setLogAutoRefresh);

  const { data, isLoading, isError } = useLogsQuery(
    {
      severity: severityFilter === "all" ? undefined : severityFilter,
      search: logSearch || undefined,
    },
    logAutoRefresh,
  );

  return (
    <div className="card">
      <div className="card-header">
        <span>Logs</span>
        <div className="log-controls">
          <select
            value={severityFilter}
            onChange={(e) =>
              setSeverityFilter(e.target.value as "all" | "info" | "error")
            }
            aria-label="Severity filter"
          >
            <option value="all">All severities</option>
            <option value="info">Info</option>
            <option value="error">Errors</option>
          </select>
          <input
            type="search"
            placeholder="Search message / job / topic"
            value={logSearch}
            onChange={(e) => setLogSearch(e.target.value)}
            aria-label="Search logs"
          />
          <label className="toggle">
            <input
              type="checkbox"
              checked={logAutoRefresh}
              onChange={(e) => setLogAutoRefresh(e.target.checked)}
            />
            auto-refresh
          </label>
        </div>
      </div>
      <div className="log-table-wrap">
        <table className="log-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Severity</th>
              <th>Stage</th>
              <th>Message</th>
              <th>Job</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={5} className="muted">
                  Loading logs…
                </td>
              </tr>
            ) : isError || !data ? (
              <tr>
                <td colSpan={5} className="muted">
                  Logs unavailable
                </td>
              </tr>
            ) : data.entries.length === 0 ? (
              <tr>
                <td colSpan={5} className="muted">
                  No matching entries
                </td>
              </tr>
            ) : (
              data.entries.map((entry, index) => (
                <LogRow key={`${entry.timestamp}-${index}`} entry={entry} />
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function SystemPanel() {
  return (
    <div className="system-panel">
      <SystemCard />
      <LogViewer />
    </div>
  );
}