import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import NotificationCenter from "../components/NotificationCenter";
import { useUiStore } from "../state/ui";

describe("NotificationCenter", () => {
  beforeEach(() => {
    useUiStore.setState({ notifications: {} });
    vi.useRealTimers();
  });

  it("renders nothing when there are no active notifications", () => {
    const { container } = render(<NotificationCenter />);
    expect(container.firstChild).toBeNull();
  });

  it("renders notifications with title, description, and severity tone", () => {
    act(() => {
      useUiStore.getState().addNotification("Test Info", "Information details", "info");
      useUiStore.getState().addNotification("Test Error", "Error details occurred", "error");
    });

    render(<NotificationCenter />);

    expect(screen.getByText("Test Info")).toBeInTheDocument();
    expect(screen.getByText("Information details")).toBeInTheDocument();
    expect(screen.getByText("Test Error")).toBeInTheDocument();
    expect(screen.getByText("Error details occurred")).toBeInTheDocument();
    expect(screen.getByText("2 notifications")).toBeInTheDocument();
  });

  it("allows dismissing an individual notification", () => {
    act(() => {
      useUiStore.getState().addNotification("Notice 1", "Details 1", "info");
    });

    render(<NotificationCenter />);
    expect(screen.getByText("Notice 1")).toBeInTheDocument();

    const closeBtn = screen.getByLabelText("Close notification: Notice 1");
    fireEvent.click(closeBtn);

    expect(screen.queryByText("Notice 1")).not.toBeInTheDocument();
  });

  it("allows dismissing all notifications", () => {
    act(() => {
      useUiStore.getState().addNotification("A", "Desc A", "info");
      useUiStore.getState().addNotification("B", "Desc B", "error");
    });

    render(<NotificationCenter />);
    expect(screen.getByText("2 notifications")).toBeInTheDocument();

    const dismissAllBtn = screen.getByRole("button", { name: "Dismiss all notifications" });
    fireEvent.click(dismissAllBtn);

    expect(screen.queryByText("A")).not.toBeInTheDocument();
    expect(screen.queryByText("B")).not.toBeInTheDocument();
  });
});
