import { useState } from "react";
import {
  useScheduleDeleteMutation,
  useScheduleInspectQuery,
  useScheduleRunNowMutation,
  useScheduleToggleMutation,
  useSchedulesQuery,
  useSchedulerStatusQuery,
  useScheduleCreateMutation,
  useScheduleUpdateMutation,
} from "../api/hooks";
import StatusBadge from "./StatusBadge";
import type {
  OperationMode,
  Schedule,
  ScheduleCadence,
  ScheduleRunSummary,
} from "../api/types";

const CADENCES: ScheduleCadence[] = ["hourly", "daily", "weekly", "weekdays"];
const WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

const MODE_HINT: Record<string, string> = {
  level3: "Discovery → proposals → queue (no production)",
  level4: "Production of eligible queue items → QA → READY_TO_PUBLISH",
  level3_then_level4: "Level 3 then Level 4 if Level 3 completed",
};

const MODE_LABEL: Record<string, string> = {
  level3: "Level 3",
  level4: "Level 4",
  level3_then_level4: "Level 3 → Level 4",
};

function toneForState(state: string): "ok" | "warn" | "bad" {
  if (state === "running") return "ok";
  if (state === "degraded") return "bad";
  return "warn";
}

function toneForRunStatus(status: string): "ok" | "warn" | "bad" | "info" {
  if (status === "completed") return "ok";
  if (status === "blocked" || status === "skipped") return "warn";
  if (status === "failed") return "bad";
  return "info";
}

function formatTime(ts: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString();
}

function parseCycleSummary(json: string | null): Record<string, unknown> | null {
  if (!json) return null;
  try {
    const parsed = JSON.parse(json);
    return typeof parsed === "object" && parsed !== null ? parsed : null;
  } catch {
    return null;
  }
}

function SchedulerStatusCard() {
  const { data, isLoading, isError } = useSchedulerStatusQuery();
  if (isLoading) return <div className="card-body muted">Loading scheduler status…</div>;
  if (isError || !data) return <div className="card-body muted">Scheduler status unavailable</div>;
  const summary = data.summary as Record<string, unknown>;
  return (
    <div className="card">
      <div className="card-header">
        <span>Scheduler status</span>
        <StatusBadge label={data.state.toUpperCase()} tone={toneForState(data.state)} />
      </div>
      <dl className="kv">
        <dt>Enabled schedules</dt>
        <dd>{String(summary.enabled_schedules ?? 0)}</dd>
        <dt>Due now</dt>
        <dd>{String(summary.due_now ?? 0)}</dd>
        <dt>Total runs</dt>
        <dd>{String(summary.total_runs ?? 0)}</dd>
        <dt>Schedules failing</dt>
        <dd>{String(summary.schedules_failing ?? 0)}</dd>
        <dt>Last run</dt>
        <dd>{formatTime((summary.last_run_at as string) ?? null)}</dd>
        <dt>Active executions</dt>
        <dd>{String(data.active_executions)}</dd>
      </dl>
      <div className="banner banner-info publish-boundary" role="note">
        The scheduler daemon runs in the backend. This surface inspects state and
        triggers run-now; scheduled Level 4 runs stop at READY_TO_PUBLISH.
      </div>
    </div>
  );
}

function ScheduleForm({
  initial,
 onSubmit,
  submitting,
  submitLabel,
  onCancel,
}: {
  initial?: Schedule;
  onSubmit: (params: Record<string, unknown>) => void;
  submitting: boolean;
  submitLabel: string;
  onCancel?: () => void;
}) {
  const [channelId, setChannelId] = useState(initial?.channel_id ?? "default");
  const [level, setLevel] = useState<number>(initial?.autonomy_level ?? 3);
  const [mode, setMode] = useState<OperationMode>(
    (initial?.operation_mode as OperationMode) ?? "level3",
  );
  const [cadence, setCadence] = useState<ScheduleCadence>(
    (initial?.cadence as ScheduleCadence) ?? "daily",
  );
  const [timezone, setTimezone] = useState(initial?.timezone ?? "UTC");
  const [days, setDays] = useState<string[]>(initial?.days_of_week ?? []);
  const [maxItems, setMaxItems] = useState<number>(initial?.max_items_per_run ?? 10);
  const [dryRun, setDryRun] = useState<boolean>(initial?.dry_run ?? false);
  const [includeLearning, setIncludeLearning] = useState<boolean>(
    initial?.include_learning ?? false,
  );
  const [error, setError] = useState<string | null>(null);

  const usesWeekdays = cadence === "weekly" || cadence === "weekdays";

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!channelId.trim()) {
      setError("Channel is required.");
      return;
    }
    if (maxItems < 1) {
      setError("Max items per run must be at least 1.");
      return;
    }
    if (level === 3 && (mode === "level4" || mode === "level3_then_level4")) {
      setError("Level 4 operation modes require autonomy level 4.");
      return;
    }
    setError(null);
    onSubmit({
      channel_id: channelId.trim(),
      autonomy_level: level,
      operation_mode: mode,
      cadence,
      timezone,
      days_of_week: usesWeekdays ? days : [],
      max_items_per_run: maxItems,
      dry_run: dryRun,
      include_learning: includeLearning,
    });
  };

  return (
    <form className="start-form" onSubmit={submit}>
      <div className="start-form-row">
        <label>
          Channel
          <input
            className="text-input"
            type="text"
            value={channelId}
            onChange={(e) => setChannelId(e.target.value)}
            aria-label="Schedule channel"
            disabled={submitting}
          />
        </label>
        <label>
          Autonomy level
          <select
            className="select-input"
            value={level}
            onChange={(e) => setLevel(Number(e.target.value))}
            aria-label="Autonomy level"
            disabled={submitting}
          >
            <option value={3}>Level 3 (auto-queue)</option>
            <option value={4}>Level 4 (auto-produce)</option>
          </select>
        </label>
        <label>
          Operation mode
          <select
            className="select-input"
            value={mode}
            onChange={(e) => setMode(e.target.value as OperationMode)}
            aria-label="Operation mode"
            disabled={submitting}
          >
            <option value="level3">Level 3 only</option>
            <option value="level4">Level 4 only</option>
            <option value="level3_then_level4">Level 3 → Level 4</option>
          </select>
        </label>
        <label>
          Cadence
          <select
            className="select-input"
            value={cadence}
            onChange={(e) => setCadence(e.target.value as ScheduleCadence)}
            aria-label="Cadence"
            disabled={submitting}
          >
            {CADENCES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label>
          Timezone
          <input
            className="text-input"
            type="text"
            value={timezone}
            onChange={(e) => setTimezone(e.target.value)}
            aria-label="Timezone"
            disabled={submitting}
          />
        </label>
        <label>
          Max items / run
          <input
            className="text-input"
            type="number"
            min={1}
            value={maxItems}
            onChange={(e) => setMaxItems(Number(e.target.value))}
            aria-label="Max items per run"
            disabled={submitting}
          />
        </label>
      </div>
      {usesWeekdays ? (
        <div className="weekday-picker">
          <span className="muted small">Days of week</span>
          <div className="weekday-buttons">
            {WEEKDAYS.map((d) => (
              <button
                key={d}
                type="button"
                className={
                  days.includes(d) ? "weekday-btn active" : "weekday-btn"
                }
                onClick={() =>
                  setDays((prev) =>
                    prev.includes(d) ? prev.filter((x) => x !== d) : [...prev, d],
                  )
                }
                aria-pressed={days.includes(d)}
                disabled={submitting}
              >
                {d}
              </button>
            ))}
          </div>
        </div>
      ) : null}
      <div className="start-form-actions">
        <div className="cycle-controls">
          <label className="cycle-toggle">
            <input
              type="checkbox"
              checked={dryRun}
              onChange={(e) => setDryRun(e.target.checked)}
              disabled={submitting}
            />
            dry run
          </label>
          <label className="cycle-toggle">
            <input
              type="checkbox"
              checked={includeLearning}
              onChange={(e) => setIncludeLearning(e.target.checked)}
              disabled={submitting}
            />
            include learning (opt-in, isolated)
          </label>
        </div>
        <div className="item-actions">
          {onCancel ? (
            <button
              type="button"
              className="ghost-btn"
              onClick={onCancel}
              disabled={submitting}
            >
              Cancel
            </button>
          ) : null}
          <button className="primary-btn" type="submit" disabled={submitting}>
            {submitting ? "Saving…" : submitLabel}
          </button>
        </div>
      </div>
      {error ? <div className="banner banner-warn">{error}</div> : null}
    </form>
  );
}

function RunHistory({ scheduleId }: { scheduleId: string }) {
  const { data, isLoading, isError } = useScheduleInspectQuery(scheduleId);
  if (isLoading) return <div className="card-body muted">Loading history…</div>;
  if (isError || !data) return <div className="card-body muted">History unavailable</div>;

  const runs = data.runs;
  if (runs.length === 0) {
    return (
      <div className="card">
        <div className="card-header">
          <span>Execution history</span>
        </div>
        <div className="card-body muted">No runs recorded for this schedule.</div>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="card-header">
        <span>Execution history</span>
        <span className="muted small">{runs.length} most recent</span>
      </div>
      <table className="queue-table">
        <thead>
          <tr>
            <th>Run</th>
            <th>Status</th>
            <th>Mode</th>
            <th>Level 3</th>
            <th>Level 4</th>
            <th>Learning</th>
            <th>Started</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => {
            const summary = parseCycleSummary(r.cycle_summary_json);
            const mode = String(summary?.operation_mode ?? r.autonomy_level);
            const l3 = (summary?.level3 as Record<string, unknown> | null) ?? null;
            const l4 = (summary?.level4 as Record<string, unknown> | null) ?? null;
            const l3Skipped = summary?.level4_skipped_reason as string | undefined;
            return (
              <tr key={r.run_id}>
                <td>{r.run_id}</td>
                <td>
                  <StatusBadge label={r.status} tone={toneForRunStatus(r.status)} />
                </td>
                <td className="small">{MODE_LABEL[mode] ?? mode}</td>
                <td className="small">
                  {l3 ? `${String(l3.status)} · q=${String(l3.jobs_queued ?? 0)}` : "—"}
                </td>
                <td className="small">
                  {l4
                    ? `${String(l4.status)} · ready=${String(l4.jobs_ready_to_publish ?? 0)}`
                    : l3Skipped
                      ? "skipped"
                      : "—"}
                </td>
                <td className="small">
                  {summary?.learning &&
                    typeof summary?.learning === "object" && "status" in summary?.learning
                    ? String((summary?.learning as { status: string }).status)
                    : "off"}
                </td>
                <td className="log-time">{formatTime(r.started_at)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ScheduleRow({ schedule }: { schedule: Schedule }) {
  const toggle = useScheduleToggleMutation();
  const remove = useScheduleDeleteMutation();
  const runNow = useScheduleRunNowMutation();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmRun, setConfirmRun] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [editing, setEditing] = useState(false);
  const update = useScheduleUpdateMutation();

  const busy = toggle.isPending || remove.isPending || runNow.isPending || update.isPending;

  const onToggle = () => {
    if (busy) return;
    toggle.mutate({ schedule_id: schedule.schedule_id, enabled: !schedule.enabled });
  };

  const onDelete = () => {
    if (busy) return;
    if (!confirmDelete) {
      setConfirmDelete(true);
      window.setTimeout(() => setConfirmDelete(false), 3500);
      return;
    }
    remove.mutate({ schedule_id: schedule.schedule_id });
  };

  const onRunNow = () => {
    if (busy) return;
    if (!confirmRun) {
      setConfirmRun(true);
      window.setTimeout(() => setConfirmRun(false), 3500);
      return;
    }
    setConfirmRun(false);
    runNow.mutate({ schedule_id: schedule.schedule_id });
  };

  return (
    <>
      <tr className={schedule.enabled ? "" : "row-disabled"}>
        <td>{schedule.schedule_id}</td>
        <td>{schedule.channel_id}</td>
        <td>
          <StatusBadge
            label={schedule.enabled ? "enabled" : "disabled"}
            tone={schedule.enabled ? "ok" : "info"}
          />
        </td>
        <td title={MODE_HINT[schedule.operation_mode ?? "level3"]}>
          {MODE_LABEL[schedule.operation_mode ?? "level3"] ?? schedule.operation_mode}
        </td>
        <td>{formatTime(schedule.next_run_at)}</td>
        <td>
          {schedule.last_run_status ? (
            <StatusBadge
              label={schedule.last_run_status}
              tone={toneForRunStatus(schedule.last_run_status)}
            />
          ) : (
            "—"
          )}
        </td>
        <td>
          <div className="item-actions">
            <button
              className="ghost-btn"
              onClick={() => {
                setShowHistory((v) => !v);
                setEditing(false);
              }}
              aria-label={`Inspect ${schedule.schedule_id}`}
            >
              {showHistory ? "Hide" : "Inspect"}
            </button>
            <button
              className="ghost-btn"
              onClick={() => {
                setEditing((v) => !v);
                setShowHistory(false);
              }}
            >
              {editing ? "Close" : "Edit"}
            </button>
            <button
              className="ghost-btn"
              onClick={onToggle}
              disabled={busy}
            >
              {schedule.enabled ? "Disable" : "Enable"}
            </button>
            <button
              className="primary-btn"
              onClick={onRunNow}
              disabled={busy || !schedule.enabled}
              title={
                schedule.enabled
                  ? "Execute this schedule immediately"
                  : "Enable the schedule before running"
              }
            >
              {confirmRun ? "Confirm run?" : "Run Now"}
            </button>
            <button
              className="danger-btn"
              onClick={onDelete}
              disabled={busy}
            >
              {confirmDelete ? "Confirm delete?" : "Delete"}
            </button>
          </div>
        </td>
      </tr>
      {editing ? (
        <tr>
          <td colSpan={7}>
            <ScheduleForm
              initial={schedule}
              submitting={update.isPending}
              submitLabel="Save changes"
              onCancel={() => setEditing(false)}
              onSubmit={(params) =>
                update.mutate(
                  { schedule_id: schedule.schedule_id, ...params },
                  { onSuccess: () => setEditing(false) },
                )
              }
            />
          </td>
        </tr>
      ) : null}
      {showHistory ? (
        <tr>
          <td colSpan={7}>
            <RunHistory scheduleId={schedule.schedule_id} />
          </td>
        </tr>
      ) : null}
      {runNow.data ? (
        <tr>
          <td colSpan={7}>
            <RunNowResult result={runNow.data} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

function RunNowResult({ result }: { result: ScheduleRunSummary }) {
  return (
    <div className="banner banner-info">
      <strong>{result.status}</strong> · run {result.run_id} · mode{" "}
      {MODE_LABEL[result.operation_mode] ?? result.operation_mode}
      {result.level3_run_id ? ` · L3 ${result.level3_status}` : null}
      {result.level4_run_id
        ? ` · L4 ${result.level4_status} (ready ${result.level4_jobs_ready_to_publish})`
        : null}
      {result.learning_status ? ` · learning ${result.learning_status}` : null}
      {result.error_message ? ` · ${result.error_message}` : null}
    </div>
  );
}

function SchedulesCard() {
  const { data, isLoading, isError } = useSchedulesQuery();
  const create = useScheduleCreateMutation();
  const [showForm, setShowForm] = useState(false);

  if (isLoading) return <div className="card-body muted">Loading schedules…</div>;
  if (isError || !data) return <div className="card-body muted">Schedules unavailable</div>;

  const items = data.items;

  return (
    <div className="card">
      <div className="card-header">
        <span>Schedules</span>
        <div className="item-actions">
          <button
            className="primary-btn"
            onClick={() => setShowForm((v) => !v)}
            aria-label="Create schedule"
          >
            {showForm ? "Close" : "New schedule"}
          </button>
        </div>
      </div>
      {showForm ? (
        <div style={{ padding: "14px" }}>
          <ScheduleForm
            submitting={create.isPending}
            submitLabel="Create schedule"
            onCancel={() => setShowForm(false)}
            onSubmit={(params) =>
              create.mutate(params, { onSuccess: () => setShowForm(false) })
            }
          />
          {create.isError ? (
            <div className="banner banner-warn">
              {(create.error as Error)?.message ?? "Failed to create schedule"}
            </div>
          ) : null}
        </div>
      ) : null}
      {items.length === 0 ? (
        <div className="card-body muted">
          No schedules configured. Create one to run recurring autonomy cycles.
        </div>
      ) : (
        <table className="queue-table">
          <thead>
            <tr>
              <th>Schedule</th>
              <th>Channel</th>
              <th>Enabled</th>
              <th>Mode</th>
              <th>Next run</th>
              <th>Last run</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {items.map((s) => (
              <ScheduleRow key={s.schedule_id} schedule={s} />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function SchedulerScreen() {
  return (
    <div className="production-screen">
      <div>
        <h2 className="page-title">Scheduler</h2>
        <p className="muted small">
          Recurring autonomy cycles. Operation modes: <strong>level3</strong> (discovery →
          queue), <strong>level4</strong> (production → QA → READY_TO_PUBLISH),{" "}
          <strong>level3_then_level4</strong> (both, with failure isolation).
        </p>
      </div>
      <SchedulerStatusCard />
      <SchedulesCard />
    </div>
  );
}