import { useState } from "react";
import {
  useAutonomyProposalsQuery,
  useAutonomyRunMutation,
  useAutonomyStatusQuery,
  useProposalApproveMutation,
  useProposalRejectMutation,
} from "../api/hooks";
import StatusBadge from "./StatusBadge";
import type {
  AutonomyMode,
  AutonomyRunMode,
  AutonomyRunRecord,
  AutonomyStatus,
  ProposalActionResponse,
} from "../api/types";

const MODE_LABEL: Record<AutonomyMode, string> = {
  manual: "MANUAL",
  assisted: "ASSISTED",
  autonomous: "AUTONOMOUS",
};

const RUN_MODES: Array<{ id: AutonomyRunMode; label: string; hint: string }> = [
  {
    id: "level3",
    label: "Run Level 3",
    hint: "Trend discovery → ideation → scoring → PolicyGate → queue → STOP",
  },
  {
    id: "level4",
    label: "Run Level 4",
    hint: "Eligible queue items → preflight → claim → production → QA → READY_TO_PUBLISH → STOP",
  },
  {
    id: "level3_then_level4",
    label: "Run Level 3 → Level 4",
    hint: "Level 3 first; Level 4 only if Level 3 completed (failure isolation)",
  },
];

function toneForOperational(status: string): "ok" | "warn" | "bad" {
  if (status === "ready") return "ok";
  if (status === "running") return "warn";
  return "bad";
}

function toneForRunStatus(status: string): "ok" | "warn" | "bad" | "info" {
  if (status === "completed" || status === "completed_manual_mode") return "ok";
  if (status === "blocked") return "warn";
  if (status === "failed" || status === "interrupted") return "bad";
  return "info";
}

function StatusHeader({ status }: { status: AutonomyStatus }) {
  return (
    <div className="card">
      <div className="card-header">
        <span>Autonomy state</span>
        <StatusBadge
          label={status.operational_status.toUpperCase()}
          tone={toneForOperational(status.operational_status)}
        />
      </div>
      <dl className="kv">
        <dt>Current mode</dt>
        <dd>{MODE_LABEL[status.mode]}</dd>
        <dt>Active autonomy level</dt>
        <dd>{status.level_name} ({status.level})</dd>
        <dt>Active strategy</dt>
        <dd>{status.active_strategy_version ?? "none"}</dd>
        <dt>Learning</dt>
        <dd>{String(status.learning?.status ?? "unknown")}</dd>
      </dl>
      <div className="banner banner-info publish-boundary" role="note">
        {status.publish_boundary}
      </div>
    </div>
  );
}

function CycleControls({ status }: { status: AutonomyStatus | undefined }) {
  const run = useAutonomyRunMutation();
  const [activeMode, setActiveMode] = useState<AutonomyRunMode | null>(null);
  const [channelId, setChannelId] = useState("default");
  const [dryRun, setDryRun] = useState(false);

  const busy = run.isPending;

  const onRun = (mode: AutonomyRunMode) => {
    if (busy) return;
    setActiveMode(mode);
    run.mutate(
      { mode, channel_id: channelId || "default", dry_run: dryRun, limit: 10 },
      { onSettled: () => setActiveMode(null) },
    );
  };

  return (
    <div className="card">
      <div className="card-header">
        <span>Cycle controls</span>
        {busy ? (
          <StatusBadge label="RUNNING" tone="warn" />
        ) : (
          <StatusBadge
            label={status?.operational_status ? status.operational_status.toUpperCase() : "—"}
            tone={status ? toneForOperational(status.operational_status) : "info"}
          />
        )}
      </div>
      <div className="card-body">
        <div className="cycle-controls">
          <label className="cycle-field">
            Channel
            <input
              className="text-input"
              type="text"
              value={channelId}
              onChange={(e) => setChannelId(e.target.value)}
              aria-label="Autonomy channel"
              disabled={busy}
            />
          </label>
          <label className="cycle-toggle">
            <input
              type="checkbox"
              checked={dryRun}
              onChange={(e) => setDryRun(e.target.checked)}
              disabled={busy}
            />
            dry run
          </label>
        </div>
        <div className="cycle-buttons">
          {RUN_MODES.map((m) => (
            <button
              key={m.id}
              className="primary-btn"
              onClick={() => onRun(m.id)}
              disabled={busy}
              aria-label={m.label}
              title={m.hint}
            >
              {activeMode === m.id && busy ? "Running…" : m.label}
            </button>
          ))}
        </div>
        {run.data ? (
          <div className="run-result">
            <h4 className="drawer-subtitle">Last run result</h4>
            <dl className="kv">
              <dt>Run</dt>
              <dd>{run.data.run_id}</dd>
              <dt>Mode</dt>
              <dd>{run.data.operation_mode}</dd>
              <dt>Status</dt>
              <dd>
                <StatusBadge
                  label={String(run.data.status)}
                  tone={toneForRunStatus(String(run.data.status))}
                />
              </dd>
              {run.data.level4_skipped_reason ? (
                <>
                  <dt>Level 4</dt>
                  <dd className="muted small">{run.data.level4_skipped_reason}</dd>
                </>
              ) : null}
            </dl>
          </div>
        ) : null}
        {run.isError ? (
          <div className="banner banner-warn">
            {(run.error as Error)?.message ?? "Autonomy run failed"}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function LimitsCard({ status }: { status: AutonomyStatus }) {
  const p = status.policy;
  const rows: Array<[string, string]> = [
    ["Max ideas / cycle", String(p.max_ideas_per_cycle)],
    ["Max auto-queue / cycle", String(p.max_auto_queue_per_cycle)],
    ["Max daily jobs", String(p.max_jobs_per_day)],
    ["Max concurrent jobs", String(p.max_concurrent_jobs)],
    ["Queue capacity", String(p.max_queued_jobs)],
    ["Topic cooldown", `${p.topic_cooldown_days} days`],
    ["Minimum score", p.min_score_threshold.toFixed(2)],
    ["Similarity threshold", p.similarity_threshold.toFixed(2)],
    ["Trend provider", p.trend_provider],
    ["Strategy influence scale", p.strategy_influence_scale.toFixed(2)],
  ];
  return (
    <div className="card">
      <div className="card-header">
        <span>Current limits</span>
        <span className="muted small">read-only</span>
      </div>
      <dl className="kv">
        {rows.map(([k, v]) => (
          <div key={k} className="kv-row">
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function ActivityCard({ status }: { status: AutonomyStatus }) {
  const a = status.activity;
  const counts: Array<[string, number]> = [
    ["Proposed", a.proposal_counts.proposed],
    ["Approved", a.proposal_counts.approved],
    ["Queued", a.proposal_counts.queued],
    ["Rejected", a.proposal_counts.rejected],
    ["Ready to publish", a.ready_to_publish],
    ["Produced today", a.produced_today],
    ["Queued today", a.queued_today],
  ];
  return (
    <div className="card">
      <div className="card-header">
        <span>Latest activity</span>
        <span className="muted small">live backend counts</span>
      </div>
      <div className="summary-grid">
        {counts.map(([label, value]) => (
          <div key={label} className="summary-card">
            <div className="summary-value">{value}</div>
            <div className="summary-label">{label}</div>
          </div>
        ))}
      </div>
      <h4 className="drawer-subtitle">Recent cycles</h4>
      {a.recent_runs.length === 0 ? (
        <p className="muted small" style={{ padding: "0 14px" }}>
          No autonomy runs recorded yet.
        </p>
      ) : (
        <table className="queue-table">
          <thead>
            <tr>
              <th>Run</th>
              <th>Level</th>
              <th>Status</th>
              <th>Signals</th>
              <th>Queued</th>
              <th>Started</th>
            </tr>
          </thead>
          <tbody>
            {a.recent_runs.map((r: AutonomyRunRecord) => (
              <tr key={r.run_id}>
                <td>{r.run_id}</td>
                <td>{r.autonomy_level}</td>
                <td>
                  <StatusBadge label={r.status} tone={toneForRunStatus(r.status)} />
                </td>
                <td>{r.signals_discovered}</td>
                <td>{r.jobs_queued}</td>
                <td className="log-time">{r.started_at ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function ProposalsCard() {
  const { data, isLoading, isError } = useAutonomyProposalsQuery();
  const approve = useProposalApproveMutation();
  const reject = useProposalRejectMutation();
  const [confirming, setConfirming] = useState<string | null>(null);

  if (isLoading) return null;
  if (isError || !data) return null;
  const items = data.items;

  if (items.length === 0) {
    return (
      <div className="card">
        <div className="card-header">
          <span>Proposals</span>
        </div>
        <div className="card-body muted">No proposals yet. Run a Level 3 cycle to generate candidates.</div>
      </div>
    );
  }

  const onApprove = (id: string) => {
    if (approve.isPending) return;
    approve.mutate({ proposal_id: id });
  };
  const onReject = (id: string) => {
    if (reject.isPending) return;
    if (confirming !== id) {
      setConfirming(id);
      window.setTimeout(() => setConfirming((c) => (c === id ? null : c)), 3500);
      return;
    }
    setConfirming(null);
    reject.mutate({ proposal_id: id });
  };

  return (
    <div className="card">
      <div className="card-header">
        <span>Proposals</span>
        <span className="muted small">{items.length} total</span>
      </div>
      <table className="queue-table">
        <thead>
          <tr>
            <th>Topic</th>
            <th>Status</th>
            <th>Score</th>
            <th>Decision</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {items.map((p) => {
            const isProposed = p.status === "proposed";
            return (
              <tr key={p.proposal_id}>
                <td>{p.proposed_topic ?? p.proposal_id}</td>
                <td>
                  <StatusBadge
                    label={p.status}
                    tone={
                      p.status === "queued" || p.status === "approved"
                        ? "ok"
                        : p.status === "rejected"
                          ? "bad"
                          : "info"
                    }
                  />
                </td>
                <td>{p.total_score != null ? p.total_score.toFixed(2) : "—"}</td>
                <td className="muted small">{p.decision_reason ?? "—"}</td>
                <td>
                  {isProposed ? (
                    <div className="item-actions">
                      <button
                        className="ghost-btn"
                        onClick={() => onApprove(p.proposal_id)}
                        disabled={approve.isPending}
                      >
                        Approve
                      </button>
                      <button
                        className="danger-btn"
                        onClick={() => onReject(p.proposal_id)}
                        disabled={reject.isPending}
                      >
                        {confirming === p.proposal_id ? "Confirm reject?" : "Reject"}
                      </button>
                    </div>
                  ) : (
                    <span className="muted small">decided</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {approve.data ? (
        <div className="banner banner-info">
          Proposal {(approve.data as ProposalActionResponse).proposal_id} approved → job{" "}
          {(approve.data as ProposalActionResponse).job_id}
        </div>
      ) : null}
      {approve.isError ? (
        <div className="banner banner-warn">
          {(approve.error as Error)?.message ?? "Approve failed"}
        </div>
      ) : null}
      {reject.isError ? (
        <div className="banner banner-warn">
          {(reject.error as Error)?.message ?? "Reject failed"}
        </div>
      ) : null}
    </div>
  );
}

export default function AutopilotScreen() {
  const { data, isLoading, isError } = useAutonomyStatusQuery();

  if (isLoading) {
    return (
      <div className="production-screen">
        <h2 className="page-title">Autopilot</h2>
        <div className="card-body muted">Loading autonomy state…</div>
      </div>
    );
  }
  if (isError || !data) {
    return (
      <div className="production-screen">
        <h2 className="page-title">Autopilot</h2>
        <div className="card-body muted">Autonomy status unavailable</div>
      </div>
    );
  }

  return (
    <div className="production-screen">
      <div>
        <h2 className="page-title">Autopilot</h2>
        <p className="muted small">
          Operates the existing autonomy engine. Level 4 stops at
          READY_TO_PUBLISH — publishing is not available on this surface.
        </p>
      </div>
      <div className="production-grid">
        <StatusHeader status={data} />
        <CycleControls status={data} />
      </div>
      <div className="production-grid">
        <LimitsCard status={data} />
        <ActivityCard status={data} />
      </div>
      <ProposalsCard />
    </div>
  );
}