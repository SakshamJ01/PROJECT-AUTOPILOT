import { useJobInspectQuery } from "../api/hooks";
import { useUiStore } from "../state/ui";

export default function JobDrawer() {
  const selectedJobId = useUiStore((s) => s.selectedJobId);
  const setSelectedJobId = useUiStore((s) => s.setSelectedJobId);
  const { data, isError } = useJobInspectQuery(selectedJobId);

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
            {isError ? "Job unavailable" : "Job not found"}
          </div>
        ) : (
          <>
            <div className="card-body">
              <dl className="kv">
                <dt>Topic</dt>
                <dd>{String((data.job as Record<string, unknown>)?.topic ?? "—")}</dd>
                <dt>Channel</dt>
                <dd>{String((data.job as Record<string, unknown>)?.channel_id ?? "—")}</dd>
                <dt>Status</dt>
                <dd>
                  {data.queue_item
                    ? `${data.queue_item.status} · ${data.queue_item.stage}`
                    : "no queue item"}
                </dd>
              </dl>
            </div>
            <h3 className="drawer-subtitle">Errors</h3>
            <ul className="plain-list">
              {data.errors.length === 0 ? (
                <li className="muted">None</li>
              ) : (
                data.errors.map((err) => (
                  <li key={err.error_id}>
                    <span className="err-type">{err.error_type}</span> — {err.message}
                  </li>
                ))
              )}
            </ul>
            <h3 className="drawer-subtitle">Timeline</h3>
            <ol className="timeline">
              {data.events.map((ev) => (
                <li key={ev.event_id}>
                  <span className="timeline-time">{ev.occurred_at ?? ""}</span>{" "}
                  {ev.from_state ?? "—"}
                  {ev.to_state ? ` → ${ev.to_state}` : ""}
                  {ev.reason ? ` ({ev.reason})` : ""}
                </li>
              ))}
              {data.events.length === 0 ? <li className="muted">No events</li> : null}
            </ol>
          </>
        )}
      </aside>
    </div>
  );
}