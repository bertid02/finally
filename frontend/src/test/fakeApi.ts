import { vi } from "vitest";

import type { Api, StreamHandlers, StreamStatus, Unsubscribe } from "@/lib/api";
import type { PriceTick, PriceUpdate } from "@/lib/types";

/**
 * A hand-driven stand-in for `api`, so component tests control exactly when a
 * tick arrives and what the backend returned. The price stream never fires on
 * its own — tests call `emit()`.
 */

const handlers = new Set<StreamHandlers>();

export const fakeApi = {
  getWatchlist: vi.fn(async () => ({ tickers: ["AAPL", "GOOGL"] })),
  addTicker: vi.fn(async (ticker: string) => ({ tickers: ["AAPL", "GOOGL", ticker] })),
  removeTicker: vi.fn(async () => ({ tickers: ["GOOGL"] })),
  getPortfolio: vi.fn(async () => ({ cash_balance: 10000, positions: [] })),
  getPortfolioHistory: vi.fn(async () => ({ snapshots: [] })),
  trade: vi.fn(),
  sendChat: vi.fn(async () => ({ message: "ok" })),
  openPriceStream: vi.fn((incoming: StreamHandlers): Unsubscribe => {
    handlers.add(incoming);
    return () => handlers.delete(incoming);
  }),
} as unknown as Api & Record<string, ReturnType<typeof vi.fn>>;

export function emit(tick: PriceTick): void {
  for (const handler of handlers) handler.onTick(tick);
}

export function emitStatus(status: StreamStatus): void {
  for (const handler of handlers) handler.onStatus(status);
}

export function resetFakeApi(): void {
  handlers.clear();
  for (const value of Object.values(fakeApi as Record<string, unknown>)) {
    if (typeof value === "function" && "mockClear" in value) {
      (value as ReturnType<typeof vi.fn>).mockClear();
    }
  }
}

/** Builds a PriceUpdate with the derived fields already consistent. */
export function priceUpdate(
  ticker: string,
  price: number,
  previous: number,
  sessionOpen: number,
): PriceUpdate {
  return {
    ticker,
    price,
    previous_price: previous,
    session_open: sessionOpen,
    timestamp: 1_755_993_600,
    change: price - previous,
    change_percent: previous ? ((price - previous) / previous) * 100 : 0,
    change_session: price - sessionOpen,
    change_percent_session: sessionOpen ? ((price - sessionOpen) / sessionOpen) * 100 : 0,
    direction: price > previous ? "up" : price < previous ? "down" : "flat",
  };
}
