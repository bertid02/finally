import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async () => ({ api: (await import("@/test/fakeApi")).fakeApi }));

import { emit, fakeApi, priceUpdate, resetFakeApi } from "@/test/fakeApi";
import { renderTerminal } from "@/test/renderTerminal";

import { TradeBar } from "./TradeBar";

beforeEach(resetFakeApi);

const tick = () => act(() => emit({ AAPL: priceUpdate("AAPL", 190.5, 190.4, 190.0) }));

/** The field already carries the watchlist selection, so replace rather than append. */
async function enterSymbol(user: ReturnType<typeof userEvent.setup>, symbol: string) {
  const field = screen.getByLabelText("Symbol");
  await user.clear(field);
  await user.type(field, symbol);
}

describe("TradeBar", () => {
  it("keeps both buttons disabled until a price for the symbol arrives", async () => {
    await renderTerminal(<TradeBar />);
    const user = userEvent.setup();
    await enterSymbol(user, "AAPL");

    // A just-added ticker under Massive's 15s poll really has no cached price,
    // and trading it would be rejected as UNKNOWN_TICKER.
    expect(screen.getByRole("button", { name: "Buy" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Sell" })).toBeDisabled();
    expect(screen.getByText(/Waiting for a price on AAPL/)).toBeInTheDocument();

    tick();
    await waitFor(() => expect(screen.getByRole("button", { name: "Buy" })).toBeEnabled());
  });

  it("rejects a non-positive quantity without calling the server", async () => {
    await renderTerminal(<TradeBar />);
    const user = userEvent.setup();
    await enterSymbol(user, "AAPL");
    tick();

    await user.clear(screen.getByLabelText("Quantity"));
    await user.type(screen.getByLabelText("Quantity"), "0");
    expect(screen.getByRole("button", { name: "Buy" })).toBeDisabled();
    expect(fakeApi.trade).not.toHaveBeenCalled();
  });

  it("shows the live price and the estimated notional", async () => {
    await renderTerminal(<TradeBar />);
    const user = userEvent.setup();
    await enterSymbol(user, "AAPL");
    tick();
    await waitFor(() => expect(screen.getByText("$1,905.00")).toBeInTheDocument());
  });

  it("submits a market buy and a market sell at the current quantity", async () => {
    fakeApi.trade = vi.fn(async () => ({
      trade: {
        id: "t1",
        ticker: "AAPL",
        side: "buy" as const,
        quantity: 10,
        price: 190.5,
        total: 1905,
        executed_at: "2026-08-24T04:11:00Z",
      },
      cash_balance: 8095,
      position: { ticker: "AAPL", quantity: 10, avg_cost: 190.5 },
    }));

    await renderTerminal(<TradeBar />);
    const user = userEvent.setup();
    await enterSymbol(user, "AAPL");
    tick();

    await user.click(screen.getByRole("button", { name: "Buy" }));
    expect(fakeApi.trade).toHaveBeenCalledWith({ ticker: "AAPL", quantity: 10, side: "buy" });

    await user.click(screen.getByRole("button", { name: "Sell" }));
    expect(fakeApi.trade).toHaveBeenLastCalledWith({ ticker: "AAPL", quantity: 10, side: "sell" });
  });

  it("surfaces the server's rejection message to the trader", async () => {
    const { ApiError } = await import("@/lib/types");
    fakeApi.trade = vi.fn(async () => {
      throw new ApiError("INSUFFICIENT_CASH", "Insufficient cash: need $80,000.00, have $8,095.00", 409);
    });

    await renderTerminal(<TradeBar />);
    const user = userEvent.setup();
    await enterSymbol(user, "AAPL");
    tick();
    await user.click(screen.getByRole("button", { name: "Buy" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Insufficient cash: need $80,000.00, have $8,095.00",
      ),
    );
  });
});
