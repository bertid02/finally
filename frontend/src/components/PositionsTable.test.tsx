import { act, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async () => ({ api: (await import("@/test/fakeApi")).fakeApi }));

import { emit, fakeApi, priceUpdate, resetFakeApi } from "@/test/fakeApi";
import { renderTerminal } from "@/test/renderTerminal";

import { PositionsTable } from "./PositionsTable";

beforeEach(() => {
  resetFakeApi();
  fakeApi.getPortfolio = vi.fn(async () => ({
    cash_balance: 8095,
    positions: [{ ticker: "AAPL", quantity: 10, avg_cost: 180 }],
  }));
});

describe("PositionsTable", () => {
  it("marks a position to the live price and shows unrealized P&L", async () => {
    await renderTerminal(<PositionsTable />);
    act(() => emit({ AAPL: priceUpdate("AAPL", 190.5, 190.4, 190.0) }));

    const row = screen.getByRole("row", { name: /AAPL/ });
    expect(row).toHaveTextContent("180.00"); // avg cost
    expect(row).toHaveTextContent("190.50"); // last
    expect(row).toHaveTextContent("$1,905.00"); // market value
    expect(row).toHaveTextContent("+$105.00"); // unrealized
  });

  it("separates return on cost from the session move", async () => {
    await renderTerminal(<PositionsTable />);
    act(() => emit({ AAPL: priceUpdate("AAPL", 190.5, 190.4, 190.0) }));

    const row = screen.getByRole("row", { name: /AAPL/ });
    expect(row).toHaveTextContent("+5.83%"); // P&L % versus 180.00 cost
    expect(row).toHaveTextContent("+0.26%"); // Day % versus the 190.00 session open
    // The tick-over-tick figure (+0.05%) has no place in this table.
    expect(row).not.toHaveTextContent("+0.05%");
  });

  it("falls back to avg cost before any price has arrived", async () => {
    await renderTerminal(<PositionsTable />);
    const row = screen.getByRole("row", { name: /AAPL/ });
    expect(row).toHaveTextContent("$1,800.00");
    expect(row).toHaveTextContent("+$0.00");
  });

  it("points at the order bar when nothing is held", async () => {
    fakeApi.getPortfolio = vi.fn(async () => ({ cash_balance: 10000, positions: [] }));
    await renderTerminal(<PositionsTable />);
    expect(screen.getByText(/No open positions/)).toBeInTheDocument();
  });
});
