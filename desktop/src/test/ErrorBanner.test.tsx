import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ErrorBanner from "../components/ErrorBanner";
import { parseEngineError } from "../api/engine";

describe("parseEngineError", () => {
  it("parses engine error code and message", () => {
    const parsed = parseEngineError("engine error -32603: MoneyPrinterTurbo is offline");
    expect(parsed.code).toBe(-32603);
    expect(parsed.message).toBe("MoneyPrinterTurbo is offline");
    expect(parsed.category).toBe("engine");
    expect(parsed.actionHint).toContain("MoneyPrinterTurbo");
  });

  it("identifies timeout errors", () => {
    const parsed = parseEngineError(new Error("LLM generation timed out after 180s"));
    expect(parsed.category).toBe("timeout");
    expect(parsed.actionHint).toContain("Operation timed out");
  });

  it("identifies validation errors", () => {
    const parsed = parseEngineError("engine error -32602: Invalid params: 'topic' must be non-empty");
    expect(parsed.category).toBe("validation");
    expect(parsed.code).toBe(-32602);
  });
});

describe("ErrorBanner component", () => {
  it("renders structured message, hint and category badge", () => {
    render(
      <ErrorBanner
        error="engine error -32603: MoneyPrinterTurbo connection refused"
        title="Render Engine Failure"
      />
    );

    expect(screen.getByText("Render Engine Failure")).toBeDefined();
    expect(screen.getByText("MoneyPrinterTurbo connection refused")).toBeDefined();
    expect(screen.getByText("ENGINE")).toBeDefined();
    expect(screen.getByText(/Ensure MoneyPrinterTurbo service is running/)).toBeDefined();
  });

  it("toggles technical details when clicked", () => {
    render(
      <ErrorBanner
        error="engine error -32603: Internal database lock"
      />
    );

    expect(screen.queryByText(/Error Code: -32603/)).toBeNull();
    const toggleBtn = screen.getByText("Show technical details ▼");
    fireEvent.click(toggleBtn);
    expect(screen.getByText(/Error Code: -32603/)).toBeDefined();
  });

  it("invokes onRetry when retry button is clicked", () => {
    const onRetry = vi.fn();
    render(
      <ErrorBanner
        error="Timed out"
        onRetry={onRetry}
        retryLabel="Try Again"
      />
    );

    const btn = screen.getByText("Try Again");
    fireEvent.click(btn);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
