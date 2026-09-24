import { useEffect } from "react";
import { useUiStore } from "../state/ui";

function formatTimestamp(ts: number): string {
  const d = new Date(ts);
  return d.toLocaleTimeString();
}

export default function NotificationCenter() {
  const notifications = useUiStore((s) => s.notifications);
  const markNotificationRead = useUiStore((s) => s.markNotificationRead);
  const clearNotifications = useUiStore((s) => s.clearNotifications);

  const list = Object.values(notifications).sort((a, b) => b.timestamp - a.timestamp);
  const activeToasts = list.filter((n) => !n.read).slice(0, 5);

  // Auto-dismiss unread toasts after 6 seconds
  useEffect(() => {
    if (activeToasts.length === 0) return;
    const timers = activeToasts.map((n) =>
      window.setTimeout(() => {
        markNotificationRead(n.id);
      }, 6000),
    );
    return () => {
      timers.forEach(clearTimeout);
    };
  }, [activeToasts, markNotificationRead]);

  if (activeToasts.length === 0) return null;

  return (
    <aside
      className="notification-toast-container"
      role="region"
      aria-label="System notifications"
    >
      <div className="toast-header-bar">
        <span className="toast-count-badge">
          {activeToasts.length} notification{activeToasts.length > 1 ? "s" : ""}
        </span>
        <button
          className="ghost-btn small-btn"
          onClick={() => clearNotifications()}
          aria-label="Dismiss all notifications"
        >
          Dismiss all
        </button>
      </div>
      <div className="toast-list">
        {activeToasts.map((n) => {
          const tone =
            n.severity === "error"
              ? "toast-error"
              : n.severity === "info"
                ? "toast-info"
                : "toast-default";
          return (
            <div
              key={n.id}
              className={`toast-card ${tone}`}
              role={n.severity === "error" ? "alert" : "status"}
            >
              <div className="toast-main">
                <div className="toast-title-row">
                  <span className="toast-icon">
                    {n.severity === "error" ? "⚠️" : "ℹ️"}
                  </span>
                  <strong className="toast-title">{n.title}</strong>
                  <span className="toast-time">{formatTimestamp(n.timestamp)}</span>
                </div>
                <div className="toast-desc">{n.description}</div>
              </div>
              <button
                className="toast-close-btn"
                onClick={() => markNotificationRead(n.id)}
                aria-label={`Close notification: ${n.title}`}
              >
                ✕
              </button>
            </div>
          );
        })}
      </div>
    </aside>
  );
}
