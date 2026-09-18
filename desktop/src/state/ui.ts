import { create } from "zustand";

export type SeverityFilter = "all" | "info" | "error";

export type Page =
  | "dashboard"
  | "system"
  | "queue"
  | "production"
  | "autopilot"
  | "scheduler"
  | "publishing"
  | "analytics"
  | "strategy"
  | "settings"
  | "general"
  | "autonomy"
  | "publishing_safety"
  | "providers"
  | "storage";

interface UiState {
  page: Page;
  selectedJobId: string | null;
  jobDrawerTab: "overview" | "timeline" | "artifacts" | "errors" | "publication";
  severityFilter: SeverityFilter;
  logSearch: string;
  logAutoRefresh: boolean;
  notifications: Record<
    string,
    {
      id: string;
      title: string;
      description: string;
      severity: SeverityFilter;
      timestamp: number;
      read: boolean;
    }
  >;
  setPage: (page: Page) => void;
  setSelectedJobId: (id: string | null) => void;
  setJobDrawerTab: (tab: UiState["jobDrawerTab"]) => void;
  setSeverityFilter: (filter: SeverityFilter) => void;
  setLogSearch: (search: string) => void;
  setLogAutoRefresh: (on: boolean) => void;
  addNotification: (
    title: string,
    description: string,
    severity?: SeverityFilter,
  ) => void;
  markNotificationRead: (id: string) => void;
  clearNotifications: (severity?: SeverityFilter) => void;
}

export const useUiStore = create<UiState>((set, get) => ({
  page: "dashboard",
  selectedJobId: null,
  jobDrawerTab: "overview",
  severityFilter: "all",
  logSearch: "",
  logAutoRefresh: true,
  notifications: {},

  setPage: (page) => set({ page }),
  setSelectedJobId: (id) => set({ selectedJobId: id }),
  setJobDrawerTab: (tab) => set({ jobDrawerTab: tab }),
  setSeverityFilter: (filter) => set({ severityFilter: filter }),
  setLogSearch: (search) => set({ logSearch: search }),
  setLogAutoRefresh: (on) => set({ logAutoRefresh: on }),

  addNotification: (title: string, description: string, severity?: SeverityFilter) => {
    const id = `${Date.now()}-${Math.random().toString(36).substring(2, 10)}`;
    const notification = {
      id,
      title,
      description,
      severity: severity || "info",
      timestamp: Date.now(),
      read: false,
    };
    set({ notifications: { ...get().notifications, [id]: notification } });
  },

  markNotificationRead: (id: string) =>
    set((state) => ({
      notifications: {
        ...state.notifications,
        [id]: { ...state.notifications[id], read: true },
      },
    })),

  clearNotifications: (severity?: SeverityFilter) =>
    set((state) => {
      const newNotifs: Record<
        string,
        {
          id: string;
          title: string;
          description: string;
          severity: SeverityFilter;
          timestamp: number;
          read: boolean;
        }
      > = {};
      Object.keys(state.notifications)
        .filter((k) => !(severity !== undefined && state.notifications[k].severity !== severity))
        .forEach((k) => (newNotifs[k] = state.notifications[k]));
      return { notifications: newNotifs };
    }),
}));