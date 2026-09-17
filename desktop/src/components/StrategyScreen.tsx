import { useState } from "react";
import {
  useStrategyLearnMutation,
  useStrategyShowQuery,
  useStrategyStatusQuery,
} from "../api/hooks";
import StatusBadge from "./StatusBadge";
import type { StrategyDelta, StrategyLearnResult, StrategyVersion } from "../api/types";

function LearnControls() {
  const learn = useStrategyLearnMutation();
  const [dryRun, setDryRun] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [last, setLast] = useState<StrategyLearnResult | null>(null);

  const onLearn = () => {
    if (learn.isPending) return;
    if (!dryRun && !confirm) {
      setConfirm(true);
      window.setTimeout(() => setConfirm(false), 3500);
      return;
    }
    setConfirm(false);
    learn.mutate(
      { channel_id: "default", dry_run: dryRun },
      {
        onSuccess: (data) => setLast(data),
      },
    );
  };

  const toneFor = (status: string): "ok" | "warn" | "bad" | "info" => {
    if (status === "applied") return "ok";
    if (status === "failed") return "bad";
    if (status === "dry_run") return "info";
    return "warn";
  };

  return (
    <div className="card">
      <div className="card-header">
        <span>Learning control</span>
        {learn.isPending ? (
          <StatusBadge label="LEARNING" tone="warn" />
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
              disabled={learn.isPending}
            />
            dry run (propose only, no strategy version)
          </label>
        </div>
        <div className="item-actions" style={{ marginTop: "10px" }}>
          <button
            className="primary-btn"
            onClick={onLearn}
            disabled={learn.isPending}
            aria-label="Run learning"
          >
            {confirm ? "Confirm learning run?" : "Run Learning"}
          </button>
        </div>
        {last ? (
          <div style={{ marginTop: "12px" }}>
            <div className="item-actions" style={{ marginBottom: "6px" }}>
              <StatusBadge label={last.status.toUpperCase()} tone={toneFor(last.status)} />
              <span className="muted small">{last.run_id}</span>
            </div>
            <dl className="kv">
              <div className="kv-row">
                <dt>Observations</dt>
                <dd>
                  {last.observations_used} used / {last.observations_considered} considered (
                  {last.observations_excluded} excluded)
                </dd>
              </div>
              <div className="kv-row">
                <dt>Input fingerprint</dt>
                <dd className="small">{last.input_fingerprint.slice(0, 24)}…</dd>
              </div>
              <div className="kv-row">
                <dt>Strategy</dt>
                <dd className="small">
                  {last.parent_strategy_version} →{" "}
                  {last.resulting_strategy_version ?? "no change"}
                </dd>
              </div>
              <div className="kv-row">
                <dt>Synthetic input</dt>
                <dd>{String(last.is_synthetic_input)}</dd>
              </div>
            </dl>
            {last.reason ? (
              <div className="muted small" style={{ marginTop: "6px" }}>
                {last.reason}
              </div>
            ) : null}
            {last.error_message ? (
              <div className="banner banner-warn" style={{ marginTop: "8px" }}>
                {last.error_message}
              </div>
            ) : null}
          </div>
        ) : null}
        {learn.isError ? (
          <div className="banner banner-warn" style={{ marginTop: "10px" }}>
            {(learn.error as Error)?.message ?? "Learning run failed"}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function NicheWeights({ weights }: { weights: Record<string, number> }) {
  const entries = Object.entries(weights);
  if (entries.length === 0) {
    return <div className="muted small">No niche weights recorded.</div>;
  }
  const max = Math.max(...entries.map(([, v]) => v), 1);
  return (
    <div className="card">
      <div className="card-header">
        <span>Niche weights</span>
        <span className="muted small">bounded learning target</span>
      </div>
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
        {entries.map(([k, v]) => (
          <div key={k} style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <span className="small" style={{ minWidth: "110px" }}>
              {k}
            </span>
            <div
              style={{
                flex: 1,
                height: "8px",
                background: "var(--border)",
                borderRadius: "4px",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  width: `${Math.max(4, (v / max) * 100)}%`,
                  height: "100%",
                  background: "var(--accent)",
                }}
              />
            </div>
            <span className="small" style={{ minWidth: "48px", textAlign: "right" }}>
              {v.toFixed(3)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function LearningRuns({ runs }: { runs: Array<Record<string, unknown>> }) {
  if (runs.length === 0) {
    return (
      <div className="card">
        <div className="card-header">
          <span>Learning runs</span>
        </div>
        <div className="card-body muted">
          No learning runs recorded for this strategy yet.
        </div>
      </div>
    );
  }
  return (
    <div className="card">
      <div className="card-header">
        <span>Learning runs</span>
        <span className="muted small">{runs.length} most recent</span>
      </div>
      <table className="queue-table">
        <thead>
          <tr>
            <th>Run</th>
            <th>Status</th>
            <th>Used</th>
            <th>Resulting version</th>
            <th>Completed</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={String(r.run_id)}>
              <td className="small">{String(r.run_id)}</td>
              <td>
                <StatusBadge label={String(r.status)} tone={String(r.status) === "applied" ? "ok" : "info"} />
              </td>
              <td>{String(r.observations_used ?? 0)}</td>
              <td className="small">{String(r.resulting_strategy_version ?? "—")}</td>
              <td className="log-time">{String(r.completed_at ?? "—")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DeltasCard({ deltas }: { deltas: StrategyDelta[] }) {
  if (deltas.length === 0) return null;
  return (
    <div className="card">
      <div className="card-header">
        <span>Bounded deltas (last run)</span>
        <span className="muted small">niche_weights only</span>
      </div>
      <table className="queue-table">
        <thead>
          <tr>
            <th>Parameter</th>
            <th>Old</th>
            <th>New</th>
            <th>Delta</th>
            <th>n</th>
            <th>Confidence</th>
          </tr>
        </thead>
        <tbody>
          {deltas.map((d) => (
            <tr key={d.parameter}>
              <td>{d.parameter}</td>
              <td>{d.old_value.toFixed(3)}</td>
              <td>{d.new_value.toFixed(3)}</td>
              <td>{d.applied_delta >= 0 ? "+" : ""}{d.applied_delta.toFixed(3)}</td>
              <td>{d.sample_size}</td>
              <td>{d.confidence.toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function StrategyScreen() {
  const { data: status, isLoading: statusLoading } = useStrategyStatusQuery();
  const activeVersion = status?.active_strategy_version ?? null;
  const { data: shown } = useStrategyShowQuery(activeVersion);

  if (statusLoading) {
    return (
      <div className="production-screen">
        <h2 className="page-title">Strategy</h2>
        <div className="card-body muted">Loading strategy…</div>
      </div>
    );
  }
  if (!status) {
    return (
      <div className="production-screen">
        <h2 className="page-title">Strategy</h2>
        <div className="card-body muted">Strategy state unavailable</div>
      </div>
    );
  }

  const strategy: StrategyVersion | null = shown?.strategy ?? status.active_strategy;
  const deltas: StrategyDelta[] = (() => {
    const raw = (status.learning.last_learning_run ?? {}) as Record<string, unknown>;
    return Array.isArray(raw.deltas) ? (raw.deltas as StrategyDelta[]) : [];
  })();

  return (
    <div className="production-screen">
      <div>
        <h2 className="page-title">Strategy</h2>
        <p className="muted small">
          Active scoring strategy and bounded analytics-driven learning. Learning
          consumes stored analytics, adjusts niche weights only, and never
          publishes or alters publishing policy.
        </p>
      </div>

      <div className="production-grid">
        <div className="card">
          <div className="card-header">
            <span>Active strategy</span>
            <StatusBadge
              label={strategy?.status ?? "none"}
              tone={strategy?.status === "active" ? "ok" : "info"}
            />
          </div>
          <dl className="kv">
            <div className="kv-row">
              <dt>Version</dt>
              <dd>{strategy?.version_id ?? "—"}</dd>
            </div>
            <div className="kv-row">
              <dt>Parent</dt>
              <dd>{strategy?.parent_version_id ?? "—"}</dd>
            </div>
            <div className="kv-row">
              <dt>Created</dt>
              <dd className="log-time">
                {strategy?.created_at
                  ? new Date(strategy.created_at).toLocaleString()
                  : "—"}
              </dd>
            </div>
            <div className="kv-row">
              <dt>Ancestry depth</dt>
              <dd>{shown?.ancestry.length ?? 0}</dd>
            </div>
          </dl>
          {strategy?.rationale ? (
            <div className="card-body muted small">{strategy.rationale}</div>
          ) : null}
        </div>

        <div className="card">
          <div className="card-header">
            <span>Learning status</span>
            <StatusBadge
              label={status.learning.status}
              tone={status.learning.status === "AVAILABLE" ? "ok" : "warn"}
            />
          </div>
          <dl className="kv">
            <div className="kv-row">
              <dt>Current strategy</dt>
              <dd className="small">
                {status.learning.current_strategy_version ?? "—"}
              </dd>
            </div>
            <div className="kv-row">
              <dt>Last run</dt>
              <dd className="small">
                {status.last_learning_run
                  ? `${status.last_learning_run.status} · ${status.last_learning_run.run_id}`
                  : "none"}
              </dd>
            </div>
            <div className="kv-row">
              <dt>Eligibility</dt>
              <dd className="small">
                {status.last_learning_run?.observations_used != null &&
                status.last_learning_run.observations_used > 0
                  ? "eligible"
                  : "awaiting published analytics"}
              </dd>
            </div>
          </dl>
        </div>
      </div>

      <div className="production-grid">
        <NicheWeights weights={strategy?.niche_weights ?? {}} />
        <div className="card">
          <div className="card-header">
            <span>Bounds</span>
            <span className="muted small">config-defined</span>
          </div>
          <dl className="kv">
            <div className="kv-row">
              <dt>Min samples</dt>
              <dd>{status.bounds.min_samples}</dd>
            </div>
            <div className="kv-row">
              <dt>Min category observations</dt>
              <dd>{status.bounds.min_category_observations}</dd>
            </div>
            <div className="kv-row">
              <dt>Window</dt>
              <dd>{status.bounds.window_days} days</dd>
            </div>
            <div className="kv-row">
              <dt>Max weight delta</dt>
              <dd>±{status.bounds.max_weight_delta}</dd>
            </div>
            <div className="kv-row">
              <dt>Max params / update</dt>
              <dd>{status.bounds.max_params_per_update}</dd>
            </div>
            <div className="kv-row">
              <dt>Weight floor / ceiling</dt>
              <dd>
                {status.bounds.weight_floor} / {status.bounds.weight_ceiling}
              </dd>
            </div>
            <div className="kv-row">
              <dt>Min content age</dt>
              <dd>{status.bounds.min_age_days} days</dd>
            </div>
          </dl>
        </div>
      </div>

      <LearnControls />

      <DeltasCard deltas={deltas} />

      <LearningRuns runs={shown?.learning_runs ?? []} />

      {status.learning_boundary ? (
        <div className="banner banner-info publish-boundary" role="note">
          {status.learning_boundary}
        </div>
      ) : null}
    </div>
  );
}
