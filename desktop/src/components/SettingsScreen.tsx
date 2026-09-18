import { useUiStore } from "../state/ui";
import { useAutonomyPublishStatusQuery } from "../api/hooks";
import StatusBadge from "./StatusBadge";

const SETTINGS_SECTIONS = [
  { id: "general", label: "General" },
  { id: "autonomy", label: "Autonomy" },
  { id: "publishing_safety", label: "Publishing Safety" },
  { id: "queue", label: "Queue" },
  { id: "providers", label: "Providers" },
  { id: "storage", label: "Storage" },
  { id: "system", label: "System" },
] as const;

function AutonomousPublishingCard() {
  const { data, isLoading, isError } = useAutonomyPublishStatusQuery();

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

  // The bridge exposes no mutation method for this switch, so there is nothing
  // to act on here — the status is displayed exactly as the backend reports it.
  if (isError || !data) {
    return (
      <div className="card">
        <div className="card-header">
          <span>Autonomous Public Publishing</span>
        </div>
        <div className="card-body muted">
          Autonomous publishing status unavailable. The backend is authoritative;
          the desktop never flips this switch.
        </div>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="card-header">
        <span>Autonomous Public Publishing</span>
        <StatusBadge
          label={data.state === "ENABLED" ? "ENABLED" : "DISABLED"}
          tone={data.enabled ? "bad" : "ok"}
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
        </p>
        <p className="muted small" style={{ marginTop: "6px" }}>
          Guardrails: {data.guardrails.join(" · ")}
        </p>
        <div className="banner banner-warn" style={{ marginTop: "10px" }}>
          Read-only from the desktop. The bridge exposes no disable/enable or
          kill-switch mutation for this switch, so this panel never changes it.
        </div>
      </div>
    </div>
  );
}

export default function SettingsScreen() {
  const setPage = useUiStore((s) => s.setPage);

  return (
    <>
      <div className="card">
        <div className="card-header">
          <span>Settings</span>
        </div>
        <div className="card-body">
          <p>Changes are validated by the backend. Frontend validation is UX-only.</p>
          <p style={{ marginTop: "10px" }}>Select a section:</p>
          <div className="item-actions" style={{ flexWrap: "wrap" }}>
            {SETTINGS_SECTIONS.map((section) => (
              <button
                key={section.id}
                className="ghost-btn"
                onClick={() => setPage(section.id)}
              >
                {section.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <AutonomousPublishingCard />
    </>
  );
}
