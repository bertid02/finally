import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async () => ({ api: (await import("@/test/fakeApi")).fakeApi }));

import { ChatPanel } from "@/components/ChatPanel";
import type { ChatResponse } from "@/lib/types";
import { Watchlist } from "@/components/Watchlist";
import { emit, emitStatus, fakeApi, priceUpdate, resetFakeApi } from "@/test/fakeApi";
import { renderTerminal } from "@/test/renderTerminal";

import { useTerminal } from "./TerminalProvider";

beforeEach(resetFakeApi);

function Probe() {
  const { totalValue, cash, status, series } = useTerminal();
  return (
    <div>
      <span data-testid="total">{totalValue.toFixed(2)}</span>
      <span data-testid="cash">{cash.toFixed(2)}</span>
      <span data-testid="status">{status}</span>
      <span data-testid="points">{series.AAPL?.length ?? 0}</span>
    </div>
  );
}

describe("TerminalProvider", () => {
  it("accumulates a price series from the stream for the sparklines", async () => {
    await renderTerminal(<Probe />);
    expect(screen.getByTestId("points")).toHaveTextContent("0");

    act(() => emit({ AAPL: priceUpdate("AAPL", 190.1, 190.0, 190.0) }));
    act(() => emit({ AAPL: priceUpdate("AAPL", 190.2, 190.1, 190.0) }));

    expect(screen.getByTestId("points")).toHaveTextContent("2");
  });

  it("marks the portfolio to the live price", async () => {
    fakeApi.getPortfolio = vi.fn(async () => ({
      cash_balance: 1000,
      positions: [{ ticker: "AAPL", quantity: 10, avg_cost: 180 }],
    }));
    await renderTerminal(<Probe />);

    act(() => emit({ AAPL: priceUpdate("AAPL", 200, 199, 190) }));
    expect(screen.getByTestId("total")).toHaveTextContent("3000.00");
  });

  it("tracks the stream status without inventing a timeout", async () => {
    await renderTerminal(<Probe />);
    act(() => emitStatus("connected"));
    expect(screen.getByTestId("status")).toHaveTextContent("connected");

    act(() => emitStatus("reconnecting"));
    expect(screen.getByTestId("status")).toHaveTextContent("reconnecting");
  });

  it("refreshes watchlist membership from the chat response", async () => {
    // The stream never carries membership, so a watchlist change the assistant
    // makes mid-turn reaches the UI only through this echo.
    fakeApi.sendChat = vi.fn(async () => ({
      message: "Added PYPL for you.",
      actions: { watchlist_changes: [{ ticker: "PYPL", action: "add" as const, status: "executed" as const }] },
      watchlist: ["AAPL", "GOOGL", "PYPL"],
    }));

    await renderTerminal(
      <>
        <Watchlist />
        <ChatPanel onCollapse={() => {}} />
      </>,
    );
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Message FinAlly"), "add pypl");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "PYPL" })).toBeInTheDocument(),
    );
    expect(screen.getByTestId("watchlist-chip")).toHaveTextContent("Added PYPL to the watchlist");
    // The echo was complete, so no extra refetch was needed.
    expect(fakeApi.getWatchlist).toHaveBeenCalledTimes(1);
  });

  it("refetches membership when the chat response omits the echo", async () => {
    fakeApi.sendChat = vi.fn(async () => ({ message: "Done." }));
    await renderTerminal(<ChatPanel onCollapse={() => {}} />);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Message FinAlly"), "do something");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(fakeApi.getWatchlist).toHaveBeenCalledTimes(2));
    expect(fakeApi.getPortfolio).toHaveBeenCalledTimes(2);
  });

  it("applies cash and positions echoed by the chat turn", async () => {
    fakeApi.sendChat = vi.fn(async () => ({
      message: "Bought 5 AAPL.",
      actions: {
        trades: [
          { ticker: "AAPL", side: "buy" as const, quantity: 5, status: "executed" as const, price: 190, total: 950 },
        ],
      },
      watchlist: ["AAPL", "GOOGL"],
      cash_balance: 9050,
      positions: [{ ticker: "AAPL", quantity: 5, avg_cost: 190 }],
    }));

    await renderTerminal(
      <>
        <Probe />
        <ChatPanel onCollapse={() => {}} />
      </>,
    );
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("Message FinAlly"), "buy aapl");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(screen.getByTestId("cash")).toHaveTextContent("9050.00"));
    expect(screen.getByTestId("trade-chip")).toHaveTextContent("Bought 5 AAPL at 190.00");
  });

  it("shows a pending indicator while the assistant is thinking", async () => {
    let release: (value: ChatResponse) => void = () => {};
    fakeApi.sendChat = vi.fn(
      () => new Promise<ChatResponse>((resolve) => { release = resolve; }),
    );

    await renderTerminal(<ChatPanel onCollapse={() => {}} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Message FinAlly"), "hello");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(screen.getByRole("status")).toHaveTextContent("Thinking");
    await act(async () => { release({ message: "Hi." }); });
    await waitFor(() => expect(screen.queryByText("Thinking")).toBeNull());
  });
});
