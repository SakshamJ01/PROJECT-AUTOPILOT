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
import { useEngineStatusQuery } from "./api/hooks";
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
  const autonomyEnabled = useUiStore((s) => s.autonomyEnabled);
  const { data: engineStatus, isError } = useEngineStatusQuery();

  const reachable = !isError && engineStatus?.running !== false;

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
        {/* Autonomy publish status badge — header bar (read-only display) */}
        <StatusBadge
          label={autonomyEnabled ? "AUTONOMOUS PUBLISHING: ON" : "AUTONOMOUS PUBLISHING: OFF"}
          tone={autonomyEnabled ? "warn" : "ok"}
        />
      </header>
      <main className="app-main">
        {!reachable ? (
          <div className="banner banner-warn">
            Backend engine is not reachable. Start the Python bridge (python -m
            autopilot.bridge) and restart the app.
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
    </div>
  );
}