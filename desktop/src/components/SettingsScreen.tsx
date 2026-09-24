import { useState } from "react";
import {
  useAutonomyPublishStatusQuery,
  useAutonomyPublishEnableMutation,
  useAutonomyPublishDisableMutation,
  useSystemStatusQuery,
  useProductionEngineStatusQuery,
  useYouTubeAuthQuery,
  useHealthQuery,
} from "../api/hooks";
import StatusBadge from "./StatusBadge";
import type { AutonomyPublishSwitchResult } from "../api/types";

type SettingsSectionId = "general" | "autonomy" | "publishing_safety" | "providers" | "storage";

const SETTINGS_SECTIONS: Array<{ id: SettingsSectionId; label: string }> = [
  { id: "general", label: "General & Runtime" },
  { id: "providers", label: "Providers & AI Models" },
  { id: "storage", label: "Storage & Artifacts" },
  { id: "publishing_safety", label: "Publishing Safety" },
  { id: "autonomy", label: "Autonomy Levels" },
];

function AutonomousPublishingCard() {
  const { data, isLoading, isError } = useAutonomyPublishStatusQuery();
  const enable = useAutonomyPublishEnableMutation();
  const disable = useAutonomyPublishDisableMutation();
  const [confirmAction, setConfirmAction] = useState<null | "enable" | "disable">(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const on = data?.enabled === true;
  const enforcing = enable.isPending || disable.isPending;

  if (isLoading) {
    return (
      <div className="card">
        <div className="card-header">
          <span>Autonomous Public Publishing</span>
        </div>
        <div className="card-body muted">Loading publishing switch…</div>
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="card">
        <div className="card-header">
          <span>Autonomous Public Publishing</span>
        </div>
        <div className="card-body muted">
          Autonomous publishing status unavailable. Start the backend engine and
          retry; this control never changes the switch itself.
        </div>
      </div>
    );
  }

  const finalize = (res: AutonomyPublishSwitchResult | undefined) => {
    if (!res?.ok) {
      const failed = res?.prerequisites?.failed;
      if (failed && failed.length > 0) {
        setActionError(
          `Cannot enable autonomous public publishing. Prerequisite(s) not met: ${failed.join(", ")}.`,
        );
      } else {
        setActionError(
          (res?.reason ?? undefined) ||
            (confirmAction === "disable"
              ? "Cannot disable the switch right now."
              : "Cannot enable the switch right now."),
        );
      }
    }
    setConfirmAction(null);
  };

  const runAction = () => {
    if (confirmAction === "enable") {
      enable.mutate(undefined, { onSuccess: finalize });
    } else if (confirmAction === "disable") {
      disable.mutate(undefined, { onSuccess: finalize });
    }
  };

  return (
    <div className="card">
      <div className="card-header">
        <span>Autonomous Public Publishing</span>
        <StatusBadge
          label={on ? "ENABLED" : "DISABLED"}
          tone={on ? "bad" : "ok"}
        />
      </div>
      <div className="card-body">
        <p>
          <strong>{data.label}</strong>
        </p>
        <p className="muted small" style={{ marginTop: "6px" }}>
          {data.boundary}
        </p>
        <p className="muted small" style={{ marginTop: "6px" }}>
          Controlled by: {data.controlled_by} · default {data.default ? "ON" : "OFF"}
          {data.controlled_at ? ` · last changed ${data.controlled_at}` : ""}
        </p>
        <p className="muted small" style={{ marginTop: "6px" }}>
          Guardrails: {data.guardrails.join(" · ")}
        </p>

        {actionError ? (
          <div className="banner banner-warn" style={{ marginTop: "10px" }}>
            {actionError}
            {enable.data?.prerequisites?.failed ? (
              <ul style={{ margin: "6px 0 0 0", paddingLeft: "18px" }}>
                {enable.data.prerequisites.failed.map((name) => (
                  <li key={name} className="small muted">
                    {name}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}

        <div style={{ marginTop: "12px" }}>
          {on ? (
            confirmAction === "disable" ? (
              <div className="banner banner-warn" style={{ marginTop: "6px" }}>
                <p style={{ margin: 0 }}>
                  Disabling is the kill switch: it stops any further autonomous
                  public publication immediately, at the final publish boundary.
                  Approvals already granted stay intact.
                </p>
                <div className="item-actions" style={{ marginTop: "8px" }}>
                  <button
                    className="danger-btn"
                    disabled={enforcing}
                    onClick={runAction}
                  >
                    {enforcing ? "Disabling…" : "Confirm: disable autonomous publishing"}
                  </button>
                  <button className="ghost-btn" disabled={enforcing} onClick={() => setConfirmAction(null)}>
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <button
                className="danger-btn"
                disabled={enforcing}
                onClick={() => {
                  setActionError(null);
                  setConfirmAction("disable");
                }}
              >
                {enforcing ? "Working…" : "Disable (kill switch)"}
              </button>
            )
          ) : confirmAction === "enable" ? (
            <div className="banner banner-warn" style={{ marginTop: "6px" }}>
              <p style={{ margin: 0 }}>
                Enabling validates every publishing prerequisite first and fails
                closed otherwise. The backend is authoritative; this confirmation
                does not change state until the backend succeeds.
              </p>
              <div className="item-actions" style={{ marginTop: "8px" }}>
                <button
                  className="primary-btn"
                  disabled={enforcing}
                  onClick={runAction}
                >
                  {enforcing ? "Enabling…" : "Confirm: enable autonomous public publishing"}
                </button>
                <button className="ghost-btn" disabled={enforcing} onClick={() => setConfirmAction(null)}>
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <button
              className="primary-btn"
              disabled={enforcing}
              onClick={() => {
                setActionError(null);
                setConfirmAction("enable");
              }}
            >
              {enforcing ? "Working…" : "Enable autonomous public publishing"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function RuntimeSection() {
  const { data: sys } = useSystemStatusQuery();
  const { data: mpt } = useProductionEngineStatusQuery();
  const systemData = (sys ?? {}) as Record<string, unknown>;
  const dbData = (systemData.db ?? {}) as Record<string, unknown>;

  return (
    <div className="production-grid">
      <div className="card">
        <div className="card-header">
          <span>Engine Runtime</span>
          <StatusBadge label={String(systemData.state ?? "ONLINE")} tone="ok" />
        </div>
        <dl className="kv">
          <dt>Python</dt>
          <dd>{String(systemData.python ?? "—")}</dd>
          <dt>Process PID</dt>
          <dd>{String(systemData.pid ?? "—")}</dd>
          <dt>Bridge Version</dt>
          <dd>v{String(systemData.bridge_version ?? "1.0.0")}</dd>
          <dt>DB Schema Version</dt>
          <dd>v{String(dbData.schema_version ?? "4")}</dd>
        </dl>
      </div>

      <div className="card">
        <div className="card-header">
          <span>Video Rendering Engine</span>
          <StatusBadge
            label={mpt?.running ? "RUNNING" : "STANDBY"}
            tone={mpt?.running ? "ok" : "info"}
          />
        </div>
        <dl className="kv">
          <dt>Engine</dt>
          <dd>{mpt?.engine ?? "MoneyPrinterTurbo"}</dd>
          <dt>Managed Service</dt>
          <dd>{mpt?.managed ? `Yes (PID ${mpt.pid})` : "No / Standby"}</dd>
          <dt>Engine Endpoint</dt>
          <dd>{mpt?.endpoint ?? "http://127.0.0.1:8501"}</dd>
          <dt>Auto-start on demand</dt>
          <dd>Enabled</dd>
        </dl>
      </div>
    </div>
  );
}

function ProvidersSection() {
  const { data: health } = useHealthQuery();
  const { data: auth } = useYouTubeAuthQuery();

  return (
    <div className="production-grid">
      <div className="card">
        <div className="card-header">
          <span>Configured Content Providers</span>
          <span className="muted small">Local-First Priority</span>
        </div>
        <dl className="kv">
          <dt>LLM Script Generator</dt>
          <dd>Ollama (qwen3:4b, llama3.2, deepseek-r1) / Mock fallback</dd>
          <dt>TTS Voice Synthesizer</dt>
          <dd>Windows SAPI (Local Native)</dd>
          <dt>Research Engine</dt>
          <dd>Wikipedia API + Crawl4AI (Local Cached)</dd>
          <dt>Visual Asset Provider</dt>
          <dd>Openverse API + Pexels (Verified CC)</dd>
          <dt>Video Renderer</dt>
          <dd>FFmpeg + MoneyPrinterTurbo</dd>
        </dl>
      </div>

      <div className="card">
        <div className="card-header">
          <span>Publishing & YouTube OAuth</span>
          <StatusBadge
            label={auth?.status ?? "NEEDS_AUTH"}
            tone={auth?.authenticated ? "ok" : "warn"}
          />
        </div>
        <dl className="kv">
          <dt>Authenticated</dt>
          <dd>{auth?.authenticated ? "Yes" : "No"}</dd>
          <dt>Secrets Configured</dt>
          <dd>{auth?.secrets_present ? "Yes (client_secrets.json)" : "No"}</dd>
          <dt>Publisher Engine</dt>
          <dd>{health?.publishing?.status ?? "healthy"}</dd>
        </dl>
        <p className="muted small" style={{ marginTop: "8px" }}>
          {auth?.guidance ?? "YouTube OAuth token is stored locally in client_secrets.json."}
        </p>
      </div>
    </div>
  );
}

function StorageSection() {
  const { data: sys } = useSystemStatusQuery();
  const systemData = (sys ?? {}) as Record<string, unknown>;
  const dbData = (systemData.db ?? {}) as Record<string, unknown>;

  return (
    <div className="card">
      <div className="card-header">
        <span>Storage & Persistence</span>
        <span className="muted small">Local-Only / Fail-Closed</span>
      </div>
      <dl className="kv">
        <dt>Database Path</dt>
        <dd style={{ wordBreak: "break-all" }}>{String(dbData.path ?? "autopilot/data/autopilot.db")}</dd>
        <dt>Database Exists</dt>
        <dd>{dbData.exists ? "Yes (Active)" : "No"}</dd>
        <dt>Artifacts Directory</dt>
        <dd style={{ wordBreak: "break-all" }}>{String(systemData.artifacts_dir ?? "autopilot/artifacts/jobs")}</dd>
        <dt>Journal Mode</dt>
        <dd>WAL (Write-Ahead Logging, busy_timeout = 30000ms)</dd>
      </dl>
    </div>
  );
}

export default function SettingsScreen() {
  const [activeSection, setActiveSection] = useState<SettingsSectionId>("general");

  return (
    <div className="production-screen">
      <div>
        <h2 className="page-title">Settings & Configuration</h2>
        <p className="muted small">
          Operational controls, provider inspection, local database configuration, and publishing safety switches.
        </p>
      </div>

      <AutonomousPublishingCard />

      <div className="toolbar" role="tablist" aria-label="Settings sections" style={{ marginTop: "16px" }}>
        {SETTINGS_SECTIONS.map((section) => (
          <button
            key={section.id}
            role="tab"
            aria-selected={activeSection === section.id}
            className={activeSection === section.id ? "primary-btn" : "ghost-btn"}
            onClick={() => setActiveSection(section.id)}
          >
            {section.label}
          </button>
        ))}
      </div>

      {activeSection === "general" ? <RuntimeSection /> : null}
      {activeSection === "providers" ? <ProvidersSection /> : null}
      {activeSection === "storage" ? <StorageSection /> : null}
    </div>
  );
}