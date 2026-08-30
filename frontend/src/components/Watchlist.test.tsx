import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async () => ({ api: (await import("@/test/fakeApi")).fakeApi }));

import { emit, fakeApi, priceUpdate, resetFakeApi } from "@/test/fakeApi";
import { renderTerminal } from "@/test/renderTerminal";

import { Watchlist } from "./Watchlist";

beforeEach(resetFakeApi);

describe("Watchlist", () => {
  it("renders the tickers the server returned", async () => {
    await renderTerminal(<Watchlist />);
    expect(screen.getByRole("button", { name: "AAPL" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "GOOGL" })).toBeInTheDocument();
    expect(screen.getByText("2/30")).toBeInTheDocument();
  });

  it("shows live prices and the session change from the stream", async () => {
    await renderTerminal(<Watchlist />);
    act(() => emit({ AAPL: priceUpdate("AAPL", 190.5, 190.4, 190.0) }));

    expect(screen.getByTestId("price-AAPL")).toHaveTextContent("190.50");
    expect(screen.getByTestId("session-AAPL")).toHaveTextContent("+0.26%");
  });

  it("adds a ticker, uppercasing it, and takes the returned list as truth", async () => {
    await renderTerminal(<Watchlist />);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Add ticker"), "pypl");
    await user.click(screen.getByRole("button", { name: "Add" }));

    expect(fakeApi.addTicker).toHaveBeenCalledWith("PYPL");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "PYPL" })).toBeInTheDocument(),
    );
  });

  it("removes a ticker and refreshes membership from the response", async () => {
    await renderTerminal(<Watchlist />);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Remove AAPL from watchlist" }));
    expect(fakeApi.removeTicker).toHaveBeenCalledWith("AAPL");
    await waitFor(() => expect(screen.queryByRole("button", { name: "AAPL" })).toBeNull());
  });

  it("selects a ticker when its symbol is clicked", async () => {
    await renderTerminal(<Watchlist />);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "GOOGL" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "GOOGL" })).toHaveAttribute("aria-pressed", "true"),
    );
  });

  it("invites the first symbol when the watchlist is empty", async () => {
    fakeApi.getWatchlist = vi.fn(async () => ({ tickers: [] }));
    await renderTerminal(<Watchlist />);
    expect(screen.getByText(/Nothing on the watchlist/)).toBeInTheDocument();
  });

  it("reports a rejected add without changing the list", async () => {
    const { ApiError } = await import("@/lib/types");
    fakeApi.getWatchlist = vi.fn(async () => ({ tickers: ["AAPL", "GOOGL"] }));
    fakeApi.addTicker = vi.fn(async () => {
      throw new ApiError("INVALID_TICKER", '"BANANA" is not a valid ticker symbol.', 400);
    });

    const view = await renderTerminal(<Watchlist />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Add ticker"), "BANAN");
    await user.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() => expect(fakeApi.addTicker).toHaveBeenCalled());
    expect(within(view.container).queryByRole("button", { name: "BANAN" })).toBeNull();
  });
});
