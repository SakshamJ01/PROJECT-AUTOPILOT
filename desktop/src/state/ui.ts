import { create } from "zustand";

export type SeverityFilter = "all" | "info" | "error";

interface UiState {
  page: "dashboard" | "system";
  selectedJobId: string | null;
  severityFilter: SeverityFilter;
  logSearch: string;
  logAutoRefresh: boolean;
  setPage: (page: UiState["page"]) => void;
  setSelectedJobId: (id: string | null) => void;
  setSeverityFilter: (filter: SeverityFilter) => void;
  setLogSearch: (search: string) => void;
  setLogAutoRefresh: (on: boolean) => void;
}

export const useUiStore = create<UiState>((set) => ({
  page: "dashboard",
  selectedJobId: null,
  severityFilter: "all",
  logSearch: "",
  logAutoRefresh: true,
  setPage: (page) => set({ page }),
  setSelectedJobId: (selectedJobId) => set({ selectedJobId }),
  setSeverityFilter: (severityFilter) => set({ severityFilter }),
  setLogSearch: (logSearch) => set({ logSearch }),
  setLogAutoRefresh: (logAutoRefresh) => set({ logAutoRefresh }),
}));