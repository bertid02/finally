import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TradeChip, WatchlistChip } from "./ActionChip";

describe("action chips", () => {
  it("confirms an executed trade with its fill price and notional", () => {
    render(
      <TradeChip
        action={{
          ticker: "AAPL",
          side: "buy",
          quantity: 10,
          status: "executed",
          price: 190.5,
          total: 1905,
        }}
      />,
    );
    expect(screen.getByTestId("trade-chip")).toHaveTextContent(
      "Bought 10 AAPL at 190.50 · $1,905.00",
    );
  });

  it("shows a failed trade with the backend's message verbatim", () => {
    render(
      <TradeChip
        action={{
          ticker: "NVDA",
          side: "buy",
          quantity: 100,
          status: "failed",
          error_code: "INSUFFICIENT_CASH",
          error: "Insufficient cash: need $80,000.00, have $8,095.00",
        }}
      />,
    );
    const chip = screen.getByTestId("trade-chip");
    expect(chip).toHaveTextContent("Buy rejected 100 NVDA");
    expect(chip).toHaveTextContent("Insufficient cash: need $80,000.00, have $8,095.00");
    expect(chip.className).toContain("border-down");
  });

  it("confirms a watchlist change", () => {
    render(<WatchlistChip action={{ ticker: "PYPL", action: "add", status: "executed" }} />);
    expect(screen.getByTestId("watchlist-chip")).toHaveTextContent(
      "Added PYPL to the watchlist",
    );
  });
});
