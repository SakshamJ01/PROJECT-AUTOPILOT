import { useState } from "react";
import { useUiStore } from "../state/ui";
import {
  useAutonomyPublishStatusQuery,
  useAutonomyPublishEnableMutation,
  useAutonomyPublishDisableMutation,
} from "../api/hooks";
import StatusBadge from "./StatusBadge";
import type { AutonomyPublishSwitchResult } from "../api/types";

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