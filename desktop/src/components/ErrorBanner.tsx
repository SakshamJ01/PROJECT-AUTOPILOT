import { useState } from "react";
import { parseEngineError, type StructuredError } from "../api/engine";
import StatusBadge from "./StatusBadge";

interface ErrorBannerProps {
  error: unknown;
  title?: string;
  onRetry?: () => void;
  retryLabel?: string;
  onDismiss?: () => void;
}

export default function ErrorBanner({
  error,
  title,
  onRetry,
  retryLabel = "Retry",
  onDismiss,
}: ErrorBannerProps) {
  const [showDetails, setShowDetails] = useState(false);
  if (!error) return null;

  const parsed: StructuredError = parseEngineError(error);
  const badgeTone =
    parsed.category === "timeout"
      ? "warn"
      : parsed.category === "validation"
        ? "info"
        : "bad";

  return (
    <div className="error-banner" role="alert">
      <div className="error-banner-header">
        <div className="error-banner-title">
          <StatusBadge label={parsed.category.toUpperCase()} tone={badgeTone} />
          <span>{title ?? "Operation Failed"}</span>
        </div>
        <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
          {onRetry ? (
            <button
              className="primary-btn"
              style={{ padding: "4px 10px", fontSize: "12px" }}
              onClick={onRetry}
            >
              {retryLabel}
            </button>
          ) : null}
          {onDismiss ? (
            <button
              className="ghost-btn"
              style={{ padding: "4px 8px", fontSize: "12px" }}
              onClick={onDismiss}
              aria-label="Dismiss error"
            >
              ✕
            </button>
          ) : null}
        </div>
      </div>

      <div className="error-banner-message">{parsed.message}</div>

      {parsed.actionHint ? (
        <div className="error-banner-hint">
          <span>💡</span>
          <span>{parsed.actionHint}</span>
        </div>
      ) : null}

      <div>
        <button
          className="ghost-btn"
          style={{ padding: "2px 6px", fontSize: "11px", color: "var(--muted)" }}
          onClick={() => setShowDetails((v) => !v)}
        >
          {showDetails ? "Hide details ▲" : "Show technical details ▼"}
        </button>
      </div>

      {showDetails ? (
        <div className="error-banner-details">
          {parsed.code != null ? `Error Code: ${parsed.code}\n` : ""}
          {parsed.raw}
        </div>
      ) : null}
    </div>
  );
}
