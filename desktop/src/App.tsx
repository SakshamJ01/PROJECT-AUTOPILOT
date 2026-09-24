import { useEffect, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import StatusBadge from "./components/StatusBadge";
import Dashboard from "./components/Dashboard";
import SystemPanel from "./components/SystemPanel";
import QueueScreen from "./components/QueueScreen";
import ProductionScreen from "./components/ProductionScreen";
import AutopilotScreen from "./components/AutopilotScreen";
import SchedulerScreen from "./components/SchedulerScreen";
import PublishingScreen from "./components/PublishingScreen";
import AnalyticsScreen from "./components/AnalyticsScreen";
import StrategyScreen from "./components/StrategyScreen";
import SettingsScreen from "./components/SettingsScreen";
import JobDrawer from "./components/JobDrawer";
import NotificationCenter from "./components/NotificationCenter";
import { useEngineStatusQuery, useAutonomyPublishStatusQuery } from "./api/hooks";
import { useUiStore } from "./state/ui";
import type { Page } from "./state/ui";

const PAGES: Array<{ id: Page; label: string }> = [
  { id: "dashboard", label: "Dashboard" },
  { id: "queue", label: "Queue" },
  { id: "production", label: "Production" },
  { id: "autopilot", label: "Autopilot" },
  { id: "scheduler", label: "Scheduler" },
  { id: "publishing", label: "Publishing" },
  { id: "analytics", label: "Analytics" },
  { id: "strategy", label: "Strategy" },
  { id: "settings", label: "Settings" },
  { id: "system", label: "System & Logs" },
];

// Settings sections have no dedicated screen of their own; the Settings screen
// stays mounted while a section is selected.
const SETTINGS_SECTIONS: ReadonlySet<Page> = new Set<Page>([
  "settings",
  "general",
  "autonomy",
  "publishing_safety",
  "providers",
  "storage",
]);

export default function App() {
  const page = useUiStore((s) => s.page);
  const setPage = useUiStore((s) => s.setPage);
  const { data: engineStatus, isError } = useEngineStatusQuery();
  const { data: autonomyPublish } = useAutonomyPublishStatusQuery();
  const [disconnected, setDisconnected] = useState(false);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    listen("bridge://disconnect", () => {
      setDisconnected(true);
    })
      .then((fn) => {
        unlisten = fn;
      })
      .catch(() => {
        // Ignored when outside Tauri runtime (e.g. unit tests)
      });
    return () => {
      if (unlisten) unlisten();
    };
  }, []);

  // The header badge derives from the backend-controlled switch, never from
  // client state. Unknown/unreachable is treated as OFF (fail-closed display).
  const autonomyPublishOn = autonomyPublish?.enabled === true;

  const reachable = !disconnected && !isError && engineStatus?.running !== false;

  return (
    <div className="app">
      <header className="app-header">
        <div className="brand">Autopilot Desktop</div>
        <nav className="nav">
          {PAGES.map((p) => (
            <button
              key={p.id}
              className={page === p.id ? "nav-item active" : "nav-item"}
              onClick={() => setPage(p.id)}
            >
              {p.label}
            </button>
          ))}
        </nav>
        <StatusBadge
          label={reachable ? "engine online" : "engine offline"}
          tone={reachable ? "ok" : "bad"}
        />
        {/* Autonomy publish status badge — backend-derived display */}
        <StatusBadge
          label={
            autonomyPublishOn
              ? "AUTONOMOUS PUBLISHING: ON"
              : "AUTONOMOUS PUBLISHING: OFF"
          }
          tone={autonomyPublishOn ? "warn" : "ok"}
        />
      </header>
      <main className="app-main">
        {!reachable ? (
          <div className="banner banner-bad">
            ⚠️ Backend engine process is not responding or has stopped. Ensure Python 3.10+ is available and restart the application.
          </div>
        ) : null}
        {page === "dashboard" ? <Dashboard /> : null}
        {page === "queue" ? <QueueScreen /> : null}
        {page === "production" ? <ProductionScreen /> : null}
        {page === "autopilot" ? <AutopilotScreen /> : null}
        {page === "scheduler" ? <SchedulerScreen /> : null}
        {page === "publishing" ? <PublishingScreen /> : null}
        {page === "analytics" ? <AnalyticsScreen /> : null}
        {page === "strategy" ? <StrategyScreen /> : null}
        {SETTINGS_SECTIONS.has(page) ? <SettingsScreen /> : null}
        {page === "system" ? <SystemPanel /> : null}
      </main>
      <JobDrawer />
      <NotificationCenter />
    </div>
  );
}