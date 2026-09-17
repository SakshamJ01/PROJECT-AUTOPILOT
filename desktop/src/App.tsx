import StatusBadge from "./components/StatusBadge";
import Dashboard from "./components/Dashboard";
import SystemPanel from "./components/SystemPanel";
import JobDrawer from "./components/JobDrawer";
import { useEngineStatusQuery } from "./api/hooks";
import { useUiStore } from "./state/ui";

export default function App() {
  const page = useUiStore((s) => s.page);
  const setPage = useUiStore((s) => s.setPage);
  const { data: engineStatus, isError } = useEngineStatusQuery();

  const reachable = !isError && engineStatus?.running !== false;

  return (
    <div className="app">
      <header className="app-header">
        <div className="brand">Autopilot Desktop</div>
        <nav className="nav">
          <button
            className={page === "dashboard" ? "nav-item active" : "nav-item"}
            onClick={() => setPage("dashboard")}
          >
            Dashboard
          </button>
          <button
            className={page === "system" ? "nav-item active" : "nav-item"}
            onClick={() => setPage("system")}
          >
            System & Logs
          </button>
        </nav>
        <StatusBadge
          label={reachable ? "engine online" : "engine offline"}
          tone={reachable ? "ok" : "bad"}
        />
      </header>
      <main className="app-main">
        {!reachable ? (
          <div className="banner banner-warn">
            Backend engine is not reachable. Start the Python bridge (python -m
            autopilot.bridge) and restart the app.
          </div>
        ) : null}
        {page === "dashboard" ? <Dashboard /> : <SystemPanel />}
      </main>
      {page === "dashboard" ? <JobDrawer /> : null}
    </div>
  );
}