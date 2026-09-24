import { useSystemStatusQuery, useLogsQuery, useHealthQuery, useProductionEngineStatusQuery } from "../api/hooks";
import { useUiStore } from "../state/ui";
import StatusBadge from "./StatusBadge";
import type { LogEntry } from "../api/types";
import { useQueryClient } from "@tanstack/react-query";

function toneForStatus(status: string): "ok" | "bad" | "warn" | "info" {
  switch (status.toLowerCase()) {
    case "available":
    case "healthy":
    case "active":
    case "running":
    case "configured":
    case "ready":
      return "ok";
    case "degraded":
    case "unconfigured":
    case "standby":
    case "retry_wait":
    case "blocked":
      return "warn";
    case "failed":
    case "dead_letter":
    case "missing":
    case "error":
      return "bad";
    default:
      return "info";
  }
}

function SystemHealthMatrix() {
  const qc = useQueryClient();
  const { data: health, isLoading: healthLoading, isError: healthError, dataUpdatedAt } = useHealthQuery();
  const { data: sys, isLoading: sysLoading } = useSystemStatusQuery();
  const { data: mpt, isLoading: mptLoading } = useProductionEngineStatusQuery();

  const handleRefresh = () => {
    qc.invalidateQueries({ queryKey: ["engine", "health"] });
    qc.invalidateQueries({ queryKey: ["engine", "system.status"] });
    qc.invalidateQueries({ queryKey: ["engine", "production.engine_status"] });
  };

  if (healthLoading || sysLoading || mptLoading) {
    return (
      <div className="card">
        <div className="card-header">
          <span>System Health & Subsystem Status</span>
          <span className="muted small">Probing subsystems…</span>
        </div>
        <div className="card-body muted">Loading health telemetry…</div>
      </div>
    );
  }

  if (healthError || !health) {
    return (
      <div className="card">
        <div className="card-header">
          <span>System Health & Subsystem Status</span>
          <button className="ghost-btn" onClick={handleRefresh}>Retry Probe</button>
        </div>
        <div className="card-body muted">
          Subsystem health telemetry unavailable. Ensure backend engine bridge is connected.
        </div>
      </div>
    );
  }

  const sysObj = sys as Record<string, unknown> | undefined;
  const ffmpegObj = health.ffmpeg as Record<string, unknown> | undefined;
  const ytObj = health.providers?.youtube as Record<string, unknown> | undefined;

  const items = [
    {
      name: "Desktop App",
      status: "ready",
      version: "v0.1.0",
      details: "Tauri 2.0 Native Shell (x86_64 Windows)",
      fix: null,
    },
    {
      name: "Python Bridge",
      status: String(sysObj?.state ?? "ready"),
      version: sysObj?.bridge_version ? `v${sysObj.bridge_version}` : "v1.0.0",
      details: `PID ${sysObj?.pid ?? "—"} · Python ${sysObj?.python ?? "3.14"}`,
      fix: null,
    },
    {
      name: "Database (SQLite)",
      status: health.db.exists ? "AVAILABLE" : "missing",
      version: `schema v${health.db.schema_version ?? 4}`,
      details: health.db.path,
      fix: health.db.exists ? null : "Database file missing. Restart app to initialize.",
    },
    {
      name: "FFmpeg Binary",
      status: health.ffmpeg?.available ? "AVAILABLE" : "missing",
      version: health.ffmpeg?.version ? String(health.ffmpeg.version).slice(0, 20) : "system",
      details: String(ffmpegObj?.path ?? "PATH lookup"),
      fix: health.ffmpeg?.available ? null : "Install FFmpeg or add to system PATH for video encoding.",
    },
    {
      name: "MoneyPrinterTurbo",
      status: mpt?.running ? "active" : "standby",
      version: mpt?.version ?? "daemon",
      details: mpt?.running ? `PID ${mpt.pid ?? "—"} · http://127.0.0.1:8501` : "Managed auto-start on production",
      fix: null,
    },
    {
      name: "Ollama / LLM Provider",
      status: health.providers?.configured?.llm ? "configured" : "unconfigured",
      version: String(health.providers?.configured?.llm ?? "ollama"),
      details: "Local LLM script & ideation generator",
      fix: health.providers?.configured?.llm ? null : "Configure LLM provider in Settings.",
    },
    {
      name: "Voice Synthesis (TTS)",
      status: health.providers?.configured?.tts ? "configured" : "unconfigured",
      version: String(health.providers?.configured?.tts ?? "windows_sapi"),
      details: "Native Windows SAPI narration synthesis",
      fix: null,
    },
    {
      name: "Research Provider",
      status: "configured",
      version: "wikipedia",
      details: "Wikipedia API source acquisition with Crawl4AI fallback",
      fix: null,
    },
    {
      name: "Asset Provider",
      status: health.providers?.configured?.asset ? "configured" : "unconfigured",
      version: String(health.providers?.configured?.asset ?? "openverse"),
      details: "Openverse verified vertical CC assets",
      fix: null,
    },
    {
      name: "YouTube Integration",
      status: String(ytObj?.status ?? "unconfigured"),
      version: ytObj?.token_present ? "OAuth valid" : "no token",
      details: ytObj?.token_present ? "Publishing authorization active" : "Manual operator review required",
      fix: ytObj?.token_present ? null : "Authenticate channel in Settings or Publishing screen.",
    },
    {
      name: "Analytics Engine",
      status: health.analytics_engine.status,
      version: health.analytics_engine.default_provider ?? "youtube_analytics",
      details: "Syncs views, likes, comments, and engagement",
      fix: null,
    },
    {
      name: "Scheduler",
      status: health.scheduler.status,
      version: "cadence manager",
      details: `Next: ${health.scheduler.next_schedule ?? "none"}`,
      fix: null,
    },
    {
      name: "Autonomy Engine",
      status: health.autonomy_engine.status,
      version: `Level ${health.autonomy_engine.autonomy_level}`,
      details: `Auto-publish: ${health.autonomy_auto_publish_enabled ? "ON (Level 4)" : "OFF (Operator Approval)"}`,
      fix: null,
    },
    {
      name: "QA Engine",
      status: health.qa_engine.status,
      version: "rule evaluator",
      details: "Duration, silence, caption alignment, and checksum verification",
      fix: null,
    },
  ];

  return (
    <div className="card" style={{ marginBottom: "16px" }}>
      <div className="card-header">
        <div>
          <span>System Health & Subsystem Matrix</span>
          <span className="muted small" style={{ marginLeft: "12px" }}>
            {dataUpdatedAt ? `Last probed: ${new Date(dataUpdatedAt).toLocaleTimeString()}` : ""}
          </span>
        </div>
        <button className="ghost-btn" onClick={handleRefresh}>Refresh Probe</button>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: "12px", padding: "16px" }}>
        {items.map((item) => (
          <div key={item.name} className="health-card" style={{ padding: "12px", borderRadius: "8px", border: "1px solid var(--border)", background: "rgba(255, 255, 255, 0.02)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
              <strong style={{ fontSize: "0.95rem" }}>{item.name}</strong>
              <StatusBadge label={item.status} tone={toneForStatus(item.status)} />
            </div>
            <div className="muted small" style={{ marginBottom: "4px" }}>
              {item.version} · {item.details}
            </div>
            {item.fix ? (
              <div className="err-type small" style={{ marginTop: "4px" }}>
                ⚠️ {item.fix}
              </div>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

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
        <span>Host Environment & Storage</span>
        <StatusBadge label={sys.state} tone="ok" />
      </div>
      <dl className="kv">
        <dt>Process PID</dt>
        <dd>{String(sys.pid)}</dd>
        <dt>Bridge Version</dt>
        <dd>v{sys.bridge_version}</dd>
        <dt>Python Runtime</dt>
        <dd>{sys.python}</dd>
        <dt>Database Location</dt>
        <dd style={{ wordBreak: "break-all" }}>{sys.db.path}</dd>
        <dt>Database Schema</dt>
        <dd>v{String(sys.db.schema_version)}</dd>
        <dt>Artifacts Storage</dt>
        <dd style={{ wordBreak: "break-all" }}>{sys.artifacts_dir}</dd>
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
      <SystemHealthMatrix />
      <SystemCard />
      <LogViewer />
    </div>
  );
}