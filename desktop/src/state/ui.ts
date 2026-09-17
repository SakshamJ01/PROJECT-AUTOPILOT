import { create } from "zustand";

export type SeverityFilter = "all" | "info" | "error";
export type Page = "dashboard" | "system" | "queue" | "production" | "autopilot" | "scheduler";

interface UiState {
  page: Page;
  selectedJobId: string | null;
  jobDrawerTab: "overview" | "timeline" | "artifacts" | "errors" | "publication";
  severityFilter: SeverityFilter;
  logSearch: string;
  logAutoRefresh: boolean;
  setPage: (page: Page) => void;
  setSelectedJobId: (id: string | null) => void;
  setJobDrawerTab: (tab: UiState["jobDrawerTab"]) => void;
  setSeverityFilter: (filter: SeverityFilter) => void;
  setLogSearch: (search: string) => void;
  setLogAutoRefresh: (on: boolean) => void;
}

export const useUiStore = create<UiState>((set) => ({
  page: "dashboard",
  selectedJobId: null,
  jobDrawerTab: "overview",
  severityFilter: "all",
  logSearch: "",
  logAutoRefresh: true,
  setPage: (page) => set({ page }),
  setSelectedJobId: (selectedJobId) => set({ selectedJobId }),
  setJobDrawerTab: (jobDrawerTab) => set({ jobDrawerTab }),
  setSeverityFilter: (severityFilter) => set({ severityFilter }),
  setLogSearch: (logSearch) => set({ logSearch }),
  setLogAutoRefresh: (logAutoRefresh) => set({ logAutoRefresh }),
}));