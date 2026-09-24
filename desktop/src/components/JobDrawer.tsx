import {
  useJobInspectQuery,
  usePublishApproveMutation,
  usePublishRejectMutation,
  useProductionRetryMutation,
} from "../api/hooks";
import { useState } from "react";
import { useUiStore } from "../state/ui";
import StatusBadge from "./StatusBadge";
import type { JobInspect } from "../api/types";

type Tab = "overview" | "timeline" | "artifacts" | "errors" | "publication";

const TABS: Array<{ id: Tab; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "timeline", label: "Timeline" },
  { id: "artifacts", label: "Artifacts" },
  { id: "errors", label: "Errors" },
  { id: "publication", label: "Publication" },
];

function formatTime(ts: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString();
}

function fileBase(path: string): string {
  const clean = path.replace(/\\/g, "/");
  const parts = clean.split("/");
  return parts[parts.length - 1] || path;
}

function OverviewTab({ data }: { data: JobInspect }) {
  const job = (data.job ?? (data as unknown as { manifest?: Record<string, unknown> }).manifest ?? data.queue_item ?? {}) as Record<string, unknown>;
  const queue = data.queue_item;
  const checksum = String(job.media_checksum_sha256 ?? job.checksum_manifest ?? job.sha256 ?? "—");
  const durationSec = job.duration_seconds ?? job.total_duration_sec ?? job.target_duration_sec;
  const narrationSec = job.narration_duration_sec ?? job.narration_sec;
  const renderSec = job.render_duration_sec ?? job.render_sec;

  return (
    <div className="tab-panel">
      <dl className="kv">
        <dt>Topic</dt>
        <dd>{String(job.topic ?? "—")}</dd>
        <dt>Channel</dt>
        <dd>{String(job.channel_id ?? "—")}</dd>
        <dt>Created</dt>
        <dd>{formatTime(String(job.created_at ?? ""))}</dd>
        <dt>Job ID</dt>
        <dd>{data.job_id}</dd>
        {durationSec != null ? (
          <>
            <dt>Video Duration</dt>
            <dd>{Number(durationSec).toFixed(1)}s (Narration: {narrationSec != null ? `${Number(narrationSec).toFixed(1)}s` : "—"}, Render: {renderSec != null ? `${Number(renderSec).toFixed(1)}s` : "—"})</dd>
          </>
        ) : null}
        {checksum !== "—" ? (
          <>
            <dt>Media SHA-256</dt>
            <dd className="small" style={{ wordBreak: "break-all" }}>{checksum}</dd>
          </>
        ) : null}
        {job.sources_json ? (
          <>
            <dt>Research Sources</dt>
            <dd className="small">{String(job.sources_json)}</dd>
          </>
        ) : null}
      </dl>
      <h4 className="drawer-subtitle">Queue status</h4>
      {queue ? (
        <dl className="kv">
          <dt>Status</dt>
          <dd>
            <StatusBadge
              label={queue.status}
              tone={queue.status === "failed" || queue.status === "dead_letter" ? "bad" : "ok"}
            />
          </dd>
          <dt>Stage</dt>
          <dd>{queue.stage}</dd>
          <dt>Attempts</dt>
          <dd>{queue.attempt_count}</dd>
          <dt>Policy</dt>
          <dd>{queue.policy ?? "—"}</dd>
          <dt>Profile</dt>
          <dd>{queue.profile ?? "—"}</dd>
          {queue.last_error ? (
            <>
              <dt>Last error</dt>
              <dd className="err-type">{queue.last_error}</dd>
            </>
          ) : null}
        </dl>
      ) : (
        <p className="muted small">This job is not queued.</p>
      )}
      <h4 className="drawer-subtitle">QA</h4>
      {data.qa_reports && data.qa_reports.length > 0 ? (
        <ul className="plain-list">
          {data.qa_reports.map((report, idx) => {
            const rep = report as unknown as Record<string, unknown>;
            return (
              <li key={String(rep.report_id ?? rep.qa_id ?? idx)}>
                <StatusBadge
                  label={String(rep.status ?? rep.decision ?? "UNKNOWN")}
                  tone={rep.publish_allowed ? "ok" : "warn"}
                />{" "}
                <span className="muted small">
                  {rep.overall_score != null
                    ? `score ${rep.overall_score}`
                    : "no score"}{" "}
                  · {formatTime(String(rep.created_at ?? rep.evaluated_at ?? ""))}
                </span>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="muted small">No QA reports yet.</p>
      )}
    </div>
  );
}

function TimelineTab({ data }: { data: JobInspect }) {
  const events = data.events ?? [];
  const stageRuns = (data as unknown as Record<string, unknown>).stage_runs as Array<Record<string, unknown>> | undefined;

  return (
    <div className="tab-panel">
      {stageRuns && stageRuns.length > 0 ? (
        <div style={{ marginBottom: "16px" }}>
          <h4 className="drawer-subtitle">Stages</h4>
          <ul className="plain-list">
            {stageRuns.map((sr, idx) => (
              <li key={String(sr.run_id ?? idx)} style={{ marginBottom: "6px" }}>
                <span className="status-badge" style={{ marginRight: "8px" }}>{String(sr.stage_name ?? sr.stage ?? "STAGE")}</span>
                <StatusBadge label={String(sr.status ?? "UNKNOWN")} tone={sr.status === "succeeded" ? "ok" : sr.status === "failed" ? "bad" : "info"} />
                <span className="muted small" style={{ marginLeft: "8px" }}>
                  {sr.duration_ms != null ? `${(Number(sr.duration_ms) / 1000).toFixed(1)}s` : "—"}
                </span>
                {sr.error_message ? <div className="err-type small" style={{ marginTop: "2px" }}>{String(sr.error_message)}</div> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <h4 className="drawer-subtitle">Event Log</h4>
      <ol className="timeline">
        {events.length === 0 ? (
          <li className="muted">No events</li>
        ) : (
          events.map((ev) => (
            <li key={ev.event_id}>
              <span className="timeline-time">{formatTime(ev.occurred_at)}</span>
              <span className="timeline-states">
                {ev.from_state ?? "—"}
                {ev.to_state ? ` → ${ev.to_state}` : ""}
              </span>
              {ev.reason ? <span className="timeline-reason"> ({ev.reason})</span> : null}
            </li>
          ))
        )}
      </ol>
    </div>
  );
}

function ArtifactsTab({ data }: { data: JobInspect }) {
  const artifacts = data.artifacts ?? [];
  if (artifacts.length === 0) {
    return (
      <div className="tab-panel">
        <p className="muted small">No artifacts produced yet.</p>
      </div>
    );
  }
  return (
    <div className="tab-panel">
      <ul className="artifact-list">
        {artifacts.map((a, idx) => {
          const art = a as Record<string, unknown>;
          const path = String(art.artifact_path ?? art.file_path ?? art.path ?? "");
          const type = String(art.artifact_type ?? art.type ?? "artifact");
          const sha = art.sha256 ?? art.sha256_hash ? String(art.sha256 ?? art.sha256_hash).slice(0, 16) : null;
          return (
            <li key={String(art.artifact_id ?? idx)} className="artifact-item">
              <StatusBadge label={type} tone={type === "media" ? "ok" : "info"} />
              <div style={{ flex: 1 }}>
                <div className="artifact-name" title={path}>
                  {fileBase(path) || path || "Artifact"}
                </div>
                {path ? <div className="muted small" style={{ wordBreak: "break-all" }}>{path}</div> : null}
                <div className="muted small">
                  {formatTime(String(art.created_at ?? ""))}
                  {sha ? ` · SHA: ${sha}…` : ""}
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function ErrorsTab({ data }: { data: JobInspect }) {
  const errors = data.errors ?? [];
  if (errors.length === 0) {
    return (
      <div className="tab-panel">
        <p className="muted small">No errors recorded.</p>
      </div>
    );
  }
  return (
    <div className="tab-panel">
      <ul className="plain-list">
        {errors.map((err, idx) => {
          const e = err as unknown as Record<string, unknown>;
          return (
            <li key={String(e.error_id ?? idx)} className="error-item">
              <div>
                <span className="err-type">{String(e.error_type ?? "ERROR")}</span>
                {e.stage ? <span className="muted small"> · {String(e.stage)}</span> : null}
              </div>
              <div>{String(e.message ?? e.error_message ?? "Unknown error")}</div>
              <div className="muted small">{formatTime(String(e.occurred_at ?? ""))}</div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function PublicationTab({ data }: { data: JobInspect }) {
  if (data.publications.length === 0) {
    return (
      <div className="tab-panel">
        <p className="muted small">
          Not yet published. You can review and publish this job from the Publishing screen once QA checks and approvals pass.
        </p>
      </div>
    );
  }
  return (
    <div className="tab-panel">
      <ul className="plain-list">
        {data.publications.map((p, idx) => {
          const pub = p as Record<string, unknown>;
          const isSuccess = pub.status === "SUCCESS" || pub.status === "published";
          const remoteUrl = pub.remote_url ? String(pub.remote_url) : null;
          return (
            <li key={String(pub.publish_id ?? idx)} className="card-body" style={{ background: "rgba(255, 255, 255, 0.02)", borderRadius: "6px", marginBottom: "8px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
                <StatusBadge
                  label={String(pub.status ?? "UNKNOWN")}
                  tone={isSuccess ? "ok" : "warn"}
                />
                <span className="muted small">{formatTime(String(pub.published_at ?? pub.created_at ?? null))}</span>
              </div>
              <dl className="kv">
                <dt>Platform</dt>
                <dd>{String(pub.platform ?? "YouTube")}</dd>
                <dt>Visibility</dt>
                <dd>{String(pub.visibility ?? "private")}</dd>
                {pub.remote_video_id ? (
                  <>
                    <dt>Video ID</dt>
                    <dd>{String(pub.remote_video_id)}</dd>
                  </>
                ) : null}
                {remoteUrl ? (
                  <>
                    <dt>URL</dt>
                    <dd>
                      <a href={remoteUrl} target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)" }}>
                        {remoteUrl}
                      </a>
                    </dd>
                  </>
                ) : null}
                {pub.error_message ? (
                  <>
                    <dt>Error</dt>
                    <dd className="err-type">{String(pub.error_message)}</dd>
                  </>
                ) : null}
              </dl>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export default function JobDrawer() {
  const selectedJobId = useUiStore((s) => s.selectedJobId);
  const setSelectedJobId = useUiStore((s) => s.setSelectedJobId);
  const setPage = useUiStore((s) => s.setPage);
  const tab = useUiStore((s) => s.jobDrawerTab);
  const setTab = useUiStore((s) => s.setJobDrawerTab);
  const { data, isError, isFetching } = useJobInspectQuery(selectedJobId);

  const approve = usePublishApproveMutation();
  const reject = usePublishRejectMutation();
  const retry = useProductionRetryMutation();
  const [confirmReject, setConfirmReject] = useState(false);
  const [confirmRetry, setConfirmRetry] = useState(false);

  if (!selectedJobId) return null;

  const close = () => setSelectedJobId(null);

  const queueItem = data?.queue_item;
  const isFailed = queueItem?.status === "failed" || Boolean(data?.errors && data.errors.length > 0);
  const hasQA = Boolean(data?.qa_reports && data.qa_reports.length > 0);
  const qaAllowed = Boolean(hasQA && data?.qa_reports?.[0]?.publish_allowed);

  const onApprove = () => {
    if (!selectedJobId) return;
    approve.mutate({ job_id: selectedJobId, notes: "Approved from Job Drawer" });
  };

  const onReject = () => {
    if (!selectedJobId) return;
    if (!confirmReject) {
      setConfirmReject(true);
      window.setTimeout(() => setConfirmReject(false), 3500);
      return;
    }
    reject.mutate({ job_id: selectedJobId, notes: "Rejected from Job Drawer" });
    setConfirmReject(false);
  };

  const onRetry = () => {
    if (!queueItem?.queue_id) return;
    if (!confirmRetry) {
      setConfirmRetry(true);
      window.setTimeout(() => setConfirmRetry(false), 3500);
      return;
    }
    retry.mutate({ queue_id: queueItem.queue_id });
    setConfirmRetry(false);
  };

  return (
    <div className="drawer-backdrop" onClick={close}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="card-header">
          <span>{data?.found ? data.job_id : `Job ${selectedJobId}`}</span>
          <button onClick={close} aria-label="Close drawer">
            ✕
          </button>
        </div>
        {isError || !data || !data.found ? (
          <div className="card-body muted">
            {isFetching ? "Loading job…" : isError ? "Job unavailable" : "Job not found"}
          </div>
        ) : (
          <>
            <div className="drawer-tabs" role="tablist">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  role="tab"
                  aria-selected={tab === t.id}
                  className={tab === t.id ? "drawer-tab active" : "drawer-tab"}
                  onClick={() => setTab(t.id)}
                >
                  {t.label}
                </button>
              ))}
            </div>
            {tab === "overview" ? <OverviewTab data={data} /> : null}
            {tab === "timeline" ? <TimelineTab data={data} /> : null}
            {tab === "artifacts" ? <ArtifactsTab data={data} /> : null}
            {tab === "errors" ? <ErrorsTab data={data} /> : null}
            {tab === "publication" ? <PublicationTab data={data} /> : null}

            <div className="card-header" style={{ borderTop: "1px solid var(--border)", marginTop: "auto", display: "flex", gap: "8px", flexWrap: "wrap" }}>
              {isFailed ? (
                <button
                  className="primary-btn"
                  disabled={retry.isPending}
                  onClick={onRetry}
                >
                  {confirmRetry ? "Confirm retry?" : retry.isPending ? "Retrying…" : "Retry job"}
                </button>
              ) : null}
              {qaAllowed ? (
                <>
                  <button
                    className="ghost-btn"
                    disabled={approve.isPending}
                    onClick={onApprove}
                  >
                    {approve.isPending ? "Approving…" : "Approve for Publishing"}
                  </button>
                  <button
                    className="danger-btn"
                    disabled={reject.isPending}
                    onClick={onReject}
                  >
                    {confirmReject ? "Confirm reject?" : reject.isPending ? "Rejecting…" : "Reject"}
                  </button>
                  <button
                    className="ghost-btn"
                    onClick={() => {
                      close();
                      setPage("publishing");
                    }}
                  >
                    Go to Publishing →
                  </button>
                </>
              ) : null}
            </div>
          </>
        )}
      </aside>
    </div>
  );
}