import { useState } from "react";
import {
  usePublishApproveMutation,
  usePublishMutation,
  usePublishRejectMutation,
  usePublishingStatusQuery,
  useReadyListQuery,
  useYouTubeAuthQuery,
  useAutonomyPublishStatusQuery,
  useAutonomyPublishDisableMutation,
} from "../api/hooks";
import StatusBadge from "./StatusBadge";
import type {
  PublishVisibility,
  ReadyPublishItem,
  YouTubeAuthStatus,
} from "../api/types";

function toneForApproval(status: string | null): "ok" | "warn" | "bad" | "info" {
  if (status === "approved") return "ok";
  if (status === "rejected") return "bad";
  if (status === "pending") return "warn";
  return "info";
}

function KillSwitchCard() {
  const { data, isLoading, isError } = useAutonomyPublishStatusQuery();
  const disable = useAutonomyPublishDisableMutation();
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (isLoading) return null;
  if (isError || !data) return null;
  const on = data.enabled;
  return (
    <div
      className={on ? "banner banner-bad" : "banner banner-warn"}
      role="status"
    >
      <strong>{data.label}</strong>
      <div className="muted small" style={{ marginTop: "4px" }}>
        {data.boundary}
      </div>
      <div className="muted small" style={{ marginTop: "6px" }}>
        Guardrails: {data.guardrails.join(" · ")}
      </div>
      {on ? (
        confirming ? (
          <div style={{ marginTop: "8px" }}>
            <p className="small" style={{ margin: 0 }}>
              Kill switch — stops any further autonomous public publication
              immediately. Existing approvals stay intact.
            </p>
            <div className="item-actions" style={{ marginTop: "8px" }}>
              <button
                className="danger-btn"
                disabled={disable.isPending}
                onClick={() => {
                  setError(null);
                  disable.mutate(undefined, {
                    onSuccess: (res) => {
                      if (!res.ok) setError(res.reason ?? "Cannot disable right now.");
                      setConfirming(false);
                    },
                  });
                }}
              >
                {disable.isPending ? "Disabling…" : "Confirm: disable autonomous publishing"}
              </button>
              <button
                className="ghost-btn"
                disabled={disable.isPending}
                onClick={() => setConfirming(false)}
              >
                Cancel
              </button>
            </div>
            {error ? (
              <div className="muted small" style={{ marginTop: "6px" }}>
                {error}
              </div>
            ) : null}
          </div>
        ) : (
          <button
            className="danger-btn"
            style={{ marginTop: "8px" }}
            onClick={() => {
              setError(null);
              setConfirming(true);
            }}
          >
            Disable now (kill switch)
          </button>
        )
      ) : null}
    </div>
  );
}

function AuthCard({ auth }: { auth: YouTubeAuthStatus | undefined }) {
  const tone = auth
    ? auth.status === "authenticated"
      ? "ok"
      : auth.status === "needs_auth"
        ? "warn"
        : "bad"
    : "info";
  return (
    <div className="card">
      <div className="card-header">
        <span>YouTube authentication</span>
        <StatusBadge
          label={auth ? auth.status.toUpperCase() : "—"}
          tone={tone}
        />
      </div>
      <div className="card-body">
        {auth ? (
          <>
            <div className="kv-row">
              <dt>Authenticated</dt>
              <dd>{String(auth.authenticated)}</dd>
            </div>
            <div className="kv-row">
              <dt>Client secrets present</dt>
              <dd>{String(auth.secrets_present)}</dd>
            </div>
            <div className="muted small" style={{ marginTop: "6px" }}>
              {auth.guidance}
            </div>
          </>
        ) : (
          <div className="muted">Auth status unavailable</div>
        )}
      </div>
    </div>
  );
}

function PublishForm({
  item,
  onSubmit,
  submitting,
}: {
  item: ReadyPublishItem;
  onSubmit: (visibility: PublishVisibility, dryRun: boolean) => void;
  submitting: boolean;
}) {
  const [visibility, setVisibility] = useState<PublishVisibility>("private");
  const [dryRun, setDryRun] = useState(false);
  const isPublic = visibility === "public";

  return (
    <div className="start-form" style={{ padding: "12px 14px" }}>
      <div className="start-form-row">
        <label>
          Visibility
          <select
            className="select-input"
            value={visibility}
            onChange={(e) => setVisibility(e.target.value as PublishVisibility)}
            aria-label="Publish visibility"
            disabled={submitting}
          >
            <option value="private">private</option>
            <option value="unlisted">unlisted</option>
            <option value="public">public</option>
          </select>
        </label>
        <label className="cycle-toggle" style={{ alignSelf: "center" }}>
          <input
            type="checkbox"
            checked={dryRun}
            onChange={(e) => setDryRun(e.target.checked)}
            disabled={submitting}
          />
          dry run (no upload)
        </label>
      </div>
      {isPublic ? (
        <div className="banner banner-warn small" style={{ margin: "6px 0" }}>
          Public visibility requires explicit operator approval and is subject
          to all backend policy gates. Autonomous public publishing is disabled.
        </div>
      ) : null}
      <div className="item-actions">
        <button
          className="primary-btn"
          onClick={() => onSubmit(visibility, dryRun)}
          disabled={submitting}
          aria-label={`Publish ${item.job_id}`}
        >
          {submitting ? "Publishing…" : "Publish"}
        </button>
      </div>
    </div>
  );
}

function ReadyRow({ item }: { item: ReadyPublishItem }) {
  const approve = usePublishApproveMutation();
  const reject = usePublishRejectMutation();
  const publish = usePublishMutation();
  const [confirmReject, setConfirmReject] = useState(false);
  const [confirmPublish, setConfirmPublish] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [lastError, setLastError] = useState<string | null>(null);

  const busy =
    approve.isPending || reject.isPending || publish.isPending;

  const onApprove = () => {
    if (busy) return;
    setLastError(null);
    approve.mutate(
      { job_id: item.job_id, notes: "Approved via desktop control surface" },
      {
        onError: (err) =>
          setLastError((err as Error)?.message ?? "Approve failed"),
      },
    );
  };

  const onReject = () => {
    if (busy) return;
    if (!confirmReject) {
      setConfirmReject(true);
      window.setTimeout(() => setConfirmReject(false), 3500);
      return;
    }
    setConfirmReject(false);
    setLastError(null);
    reject.mutate(
      { job_id: item.job_id, notes: "Rejected via desktop control surface" },
      {
        onError: (err) =>
          setLastError((err as Error)?.message ?? "Reject failed"),
      },
    );
  };

  const onPublish = (visibility: PublishVisibility, dryRun: boolean) => {
    if (busy) return;
    if (!dryRun && !confirmPublish) {
      setConfirmPublish(true);
      window.setTimeout(() => setConfirmPublish(false), 3500);
      return;
    }
    setConfirmPublish(false);
    setLastError(null);
    publish.mutate(
      { job_id: item.job_id, visibility, dry_run: dryRun },
      {
        onError: (err) =>
          setLastError((err as Error)?.message ?? "Publish failed"),
      },
    );
  };

  const approved = item.approval_status === "approved";
  const canPublish =
    approved && item.qa_publish_allowed && item.checksum_matches && !item.published;

  return (
    <>
      <tr className={item.published ? "row-disabled" : ""}>
        <td>{item.job_id}</td>
        <td>{item.topic ?? "—"}</td>
        <td>{item.channel_id}</td>
        <td>
          <StatusBadge
            label={item.qa_status ?? "MISSING"}
            tone={item.qa_publish_allowed ? "ok" : "bad"}
          />
        </td>
        <td>
          <StatusBadge
            label={item.approval_status ?? "none"}
            tone={toneForApproval(item.approval_status)}
          />
        </td>
        <td className="small" title={item.media_checksum_sha256 ?? undefined}>
          {item.media_checksum_sha256
            ? `${item.media_checksum_sha256.slice(0, 12)}…`
            : "—"}
        </td>
        <td>
          {item.published ? (
            <span className="small">
              {item.remote_video_id ?? "published"}{" "}
              <span className="muted">({item.visibility ?? "—"})</span>
            </span>
          ) : (
            <span className="muted small">not published</span>
          )}
        </td>
        <td>
          <div className="item-actions">
            <button
              className="ghost-btn"
              onClick={() => setExpanded((v) => !v)}
              aria-label={`Inspect ${item.job_id}`}
            >
              {expanded ? "Hide" : "Inspect"}
            </button>
            {!item.published ? (
              <>
                <button
                  className="ghost-btn"
                  onClick={onApprove}
                  disabled={busy || approved}
                >
                  Approve
                </button>
                <button
                  className="danger-btn"
                  onClick={onReject}
                  disabled={busy || item.approval_status === "rejected"}
                >
                  {confirmReject ? "Confirm reject?" : "Reject"}
                </button>
              </>
            ) : null}
          </div>
        </td>
      </tr>
      {expanded && !item.published ? (
        <tr>
          <td colSpan={8}>
            <PublishForm
              item={item}
              onSubmit={onPublish}
              submitting={publish.isPending}
            />
            {!canPublish && !item.published ? (
              <div className="banner banner-warn small" style={{ margin: "0 14px 10px" }}>
                {approved
                  ? "Approved, but QA, checksum or media validation is not satisfied. The backend enforces every gate."
                  : "Explicit operator approval is required before publishing."}
              </div>
            ) : null}
            {confirmPublish ? (
              <div className="banner banner-warn small" style={{ margin: "0 14px 10px" }}>
                Click Publish again to confirm the upload.
              </div>
            ) : null}
          </td>
        </tr>
      ) : null}
      {publish.data ? (
        <tr>
          <td colSpan={8}>
            <div
              className={
                publish.data.success ? "banner banner-info" : "banner banner-warn"
              }
              style={{ margin: "0 14px 10px" }}
            >
              <strong>{publish.data.status}</strong> · {publish.data.job_id} ·{" "}
              {publish.data.visibility}
              {publish.data.remote_video_id
                ? ` · ${publish.data.remote_video_id}`
                : null}
              {publish.data.error_message
                ? ` · ${publish.data.error_code}: ${publish.data.error_message}`
                : null}
            </div>
          </td>
        </tr>
      ) : null}
      {lastError ? (
        <tr>
          <td colSpan={8}>
            <div className="banner banner-warn" style={{ margin: "0 14px 10px" }}>
              {lastError}
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}

export default function PublishingScreen() {
  const { data: status, isLoading: statusLoading } = usePublishingStatusQuery();
  const { data, isLoading, isError } = useReadyListQuery();
  const { data: auth } = useYouTubeAuthQuery();
  const [search, setSearch] = useState("");

  const items = (data?.items ?? []).filter(
    (i) =>
      !search ||
      i.job_id.toLowerCase().includes(search.toLowerCase()) ||
      (i.topic ?? "").toLowerCase().includes(search.toLowerCase()),
  );

  if (statusLoading || isLoading) {
    return (
      <div className="production-screen">
        <h2 className="page-title">Publishing</h2>
        <div className="card-body muted">Loading publishing state…</div>
      </div>
    );
  }
  if (isError || !data) {
    return (
      <div className="production-screen">
        <h2 className="page-title">Publishing</h2>
        <div className="card-body muted">Publishing state unavailable</div>
      </div>
    );
  }

  const summary = data.summary;

  return (
    <div className="production-screen">
      <div>
        <h2 className="page-title">Publishing</h2>
        <p className="muted small">
          Protected publishing: QA → approval → artifact checksum → idempotent
          upload. Every gate is enforced by the backend; this surface never
          relaxes one.
        </p>
      </div>

      <KillSwitchCard />

      <div className="summary-grid">
        <div className="summary-card">
          <div className="summary-value">{summary.ready_to_publish}</div>
          <div className="summary-label">Ready to publish</div>
        </div>
        <div className="summary-card">
          <div className="summary-value">{summary.awaiting_approval}</div>
          <div className="summary-label">Awaiting approval</div>
        </div>
        <div className="summary-card">
          <div className="summary-value">{summary.published}</div>
          <div className="summary-label">Published</div>
        </div>
        <div className="summary-card">
          <div className="summary-value">{summary.publish_failures}</div>
          <div className="summary-label">Publish failures</div>
        </div>
      </div>

      <div className="production-grid">
        <AuthCard auth={auth} />
        <div className="card">
          <div className="card-header">
            <span>Visibility policy</span>
            <span className="muted small">backend-defined</span>
          </div>
          <div className="card-body">
            <div className="kv-row">
              <dt>Default visibility</dt>
              <dd>{summary.default_visibility}</dd>
            </div>
            <div className="kv-row">
              <dt>Supported</dt>
              <dd>private · unlisted · public</dd>
            </div>
            <div className="muted small" style={{ marginTop: "6px" }}>
              Public publishing requires the explicit operator approval gate and
              all backend policy checks.
            </div>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <span>Ready to publish</span>
          <input
            className="text-input"
            type="text"
            placeholder="Search job or topic…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search ready jobs"
            style={{ maxWidth: "220px" }}
          />
        </div>
        {items.length === 0 ? (
          <div className="card-body muted">
            No READY_TO_PUBLISH jobs. Produce and QA-pass a job first, or check
            the Queue screen.
          </div>
        ) : (
          <table className="queue-table">
            <thead>
              <tr>
                <th>Job</th>
                <th>Topic</th>
                <th>Channel</th>
                <th>QA</th>
                <th>Approval</th>
                <th>Checksum</th>
                <th>Publication</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((i) => (
                <ReadyRow key={i.job_id} item={i} />
              ))}
            </tbody>
          </table>
        )}
      </div>

      {status?.publish_boundary ? (
        <div className="banner banner-info publish-boundary" role="note">
          {status.publish_boundary}
        </div>
      ) : null}
    </div>
  );
}
