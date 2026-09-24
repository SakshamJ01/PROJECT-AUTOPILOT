import { useJobInspectQuery } from "../api/hooks";
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
  const job = (data.job ?? {}) as Record<string, unknown>;
  const queue = data.queue_item;
  return (
    <div className="tab-panel">
      <dl className="kv">
        <dt>Topic</dt>
        <dd>{String(job.topic ?? "—")}</dd>
        <dt>Channel</dt>
        <dd>{String(job.channel_id ?? "—")}</dd>
        <dt>Created</dt>
        <dd>{formatTime(String(job.created_at))}</dd>
        <dt>Job ID</dt>
        <dd>{data.job_id}</dd>
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
          {data.qa_reports.map((report) => (
            <li key={report.report_id}>
              <StatusBadge
                label={String(report.status ?? "UNKNOWN")}
                tone={report.publish_allowed ? "ok" : "warn"}
              />{" "}
              <span className="muted small">
                {report.overall_score != null
                  ? `score ${report.overall_score}`
                  : "no score"}{" "}
                · {formatTime(report.created_at ?? null)}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted small">No QA reports yet.</p>
      )}
    </div>
  );
}

function TimelineTab({ data }: { data: JobInspect }) {
  return (
    <div className="tab-panel">
      <ol className="timeline">
        {data.events.length === 0 ? (
          <li className="muted">No events</li>
        ) : (
          data.events.map((ev) => (
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
  if (data.artifacts.length === 0) {
    return (
      <div className="tab-panel">
        <p className="muted small">No artifacts produced yet.</p>
      </div>
    );
  }
  return (
    <div className="tab-panel">
      <ul className="artifact-list">
        {data.artifacts.map((a, idx) => {
          const art = a as Record<string, unknown>;
          const path = String(art.artifact_path ?? "");
          const type = String(art.artifact_type ?? "artifact");
          return (
            <li key={String(art.artifact_id ?? idx)} className="artifact-item">
              <StatusBadge label={type} tone={type === "media" ? "ok" : "info"} />
              <div>
                <div className="artifact-name" title={path}>
                  {fileBase(path)}
                </div>
                <div className="muted small">{formatTime(String(art.created_at))}</div>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function ErrorsTab({ data }: { data: JobInspect }) {
  if (data.errors.length === 0) {
    return (
      <div className="tab-panel">
        <p className="muted small">No errors recorded.</p>
      </div>
    );
  }
  return (
    <div className="tab-panel">
      <ul className="plain-list">
        {data.errors.map((err) => (
          <li key={err.error_id} className="error-item">
            <div>
              <span className="err-type">{err.error_type}</span>
              {err.stage ? <span className="muted small"> · {err.stage}</span> : null}
            </div>
            <div>{err.message}</div>
            <div className="muted small">{formatTime(err.occurred_at)}</div>
          </li>
        ))}
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
  const tab = useUiStore((s) => s.jobDrawerTab);
  const setTab = useUiStore((s) => s.setJobDrawerTab);
  const { data, isError, isFetching } = useJobInspectQuery(selectedJobId);

  if (!selectedJobId) return null;

  const close = () => setSelectedJobId(null);

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
          </>
        )}
      </aside>
    </div>
  );
}