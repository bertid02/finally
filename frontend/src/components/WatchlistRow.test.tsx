import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { PriceUpdate } from "@/lib/types";
import { priceUpdate } from "@/test/fakeApi";

import { WatchlistRow } from "./WatchlistRow";

// Passing `undefined` explicitly must mean "no price yet", so the parameter
// cannot carry a default.
function renderRow(update: PriceUpdate | undefined, selected = false) {
  const onSelect = vi.fn();
  const onRemove = vi.fn();
  const view = render(
    <WatchlistRow
      ticker="AAPL"
      update={update}
      points={[]}
      selected={selected}
      onSelect={onSelect}
      onRemove={onRemove}
    />,
  );
  return { ...view, onSelect, onRemove };
}

describe("WatchlistRow", () => {
  it("displays the session change, never the tick-over-tick change", () => {
    // +0.26% versus the 190.00 session open; +0.05% versus the previous tick.
    // Only the first may ever appear on screen.
    renderRow(priceUpdate("AAPL", 190.5, 190.4, 190.0));
    expect(screen.getByTestId("session-AAPL")).toHaveTextContent("+0.26%");
    expect(screen.queryByText("+0.05%")).toBeNull();
  });

  it("shows a placeholder before the first price arrives", () => {
    renderRow(undefined);
    expect(screen.getByTestId("price-AAPL")).toHaveTextContent("—");
    expect(screen.getByTestId("session-AAPL")).toHaveTextContent("—");
  });

  it("flashes green on an uptick and clears the class afterwards", async () => {
    vi.useFakeTimers();
    const { rerender } = renderRow(priceUpdate("AAPL", 190.0, 190.0, 190.0));
    expect(screen.getByTestId("price-AAPL").className).not.toContain("animate-flash");

    rerender(
      <WatchlistRow
        ticker="AAPL"
        update={priceUpdate("AAPL", 191.0, 190.0, 190.0)}
        points={[]}
        selected={false}
        onSelect={vi.fn()}
        onRemove={vi.fn()}
      />,
    );
    expect(screen.getByTestId("price-AAPL").className).toContain("animate-flashUp");

    act(() => void vi.advanceTimersByTime(600));
    expect(screen.getByTestId("price-AAPL").className).not.toContain("animate-flash");
    vi.useRealTimers();
  });

  it("flashes red on a downtick", () => {
    const { rerender } = renderRow(priceUpdate("AAPL", 190.0, 190.0, 190.0));
    rerender(
      <WatchlistRow
        ticker="AAPL"
        update={priceUpdate("AAPL", 189.0, 190.0, 190.0)}
        points={[]}
        selected={false}
        onSelect={vi.fn()}
        onRemove={vi.fn()}
      />,
    );
    expect(screen.getByTestId("price-AAPL").className).toContain("animate-flashDown");
  });

  it("selects and removes the ticker", async () => {
    const user = userEvent.setup();
    const { onSelect, onRemove } = renderRow(priceUpdate("AAPL", 190.5, 190.4, 190.0));

    await user.click(screen.getByRole("button", { name: "AAPL" }));
    expect(onSelect).toHaveBeenCalledWith("AAPL");

    await user.click(screen.getByRole("button", { name: /Remove AAPL/ }));
    expect(onRemove).toHaveBeenCalledWith("AAPL");
  });
});
