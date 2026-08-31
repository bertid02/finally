/**
 * THE SWAP FILE.
 *
 * Everything the frontend knows about the backend enters through this module:
 * the REST calls and the price stream. Nothing else in the app calls `fetch`
 * or constructs an `EventSource`.
 *
 * Development runs against an in-process mock (`mockApi.ts`) so the UI can be
 * built before the API exists. Flip `NEXT_PUBLIC_USE_MOCK_API` to "false" — or
 * delete the mock branch below — and the app talks to the real server. No
 * other file changes.
 */

import { mockApi } from "./mockApi";
import type {
  ChatResponse,
  Portfolio,
  PortfolioHistory,
  PriceTick,
  TradeResult,
  TradeSide,
  Watchlist,
} from "./types";
import { ApiError } from "./types";

const USE_MOCK = process.env.NEXT_PUBLIC_USE_MOCK_API !== "false";

/** Cancels a stream subscription. */
export type Unsubscribe = () => void;

export type StreamStatus = "connected" | "reconnecting" | "disconnected";

export interface StreamHandlers {
  onTick: (tick: PriceTick) => void;
  onStatus: (status: StreamStatus) => void;
}

export interface Api {
  getWatchlist(): Promise<Watchlist>;
  addTicker(ticker: string): Promise<Watchlist>;
  removeTicker(ticker: string): Promise<Watchlist>;
  getPortfolio(): Promise<Portfolio>;
  getPortfolioHistory(params?: { since?: string; limit?: number }): Promise<PortfolioHistory>;
  trade(input: { ticker: string; quantity: number; side: TradeSide }): Promise<TradeResult>;
  sendChat(message: string): Promise<ChatResponse>;
  /** Opens the live price stream. Returns an unsubscribe. */
  openPriceStream(handlers: StreamHandlers): Unsubscribe;
}

// --- real implementation ----------------------------------------------

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError("NETWORK_ERROR", "Can't reach the server. Check the connection.", 0);
  }

  if (!response.ok) {
    // Every non-2xx uses the error envelope, but a proxy or a crash can still
    // return HTML — fall back to a readable message rather than a parse error.
    let code = "UNKNOWN_ERROR";
    let message = `Request failed (${response.status}).`;
    try {
      const body = await response.json();
      if (body?.error?.message) {
        code = body.error.code ?? code;
        message = body.error.message;
      }
    } catch {
      /* keep the fallback */
    }
    throw new ApiError(code, message, response.status);
  }

  return (await response.json()) as T;
}

/**
 * Live price stream.
 *
 * The stream emits only when the price cache version changes, so an idle cache
 * sends nothing. Silence is NOT a disconnect: there is deliberately no
 * inactivity timeout here. Only `EventSource`'s own readyState decides status,
 * and EventSource reconnects on its own using the server's `retry:` directive.
 */
function openRealPriceStream({ onTick, onStatus }: StreamHandlers): Unsubscribe {
  const source = new EventSource("/api/stream/prices");

  source.onopen = () => onStatus("connected");

  source.onmessage = (event: MessageEvent<string>) => {
    try {
      onTick(JSON.parse(event.data) as PriceTick);
    } catch {
      /* A malformed frame is dropped; the next tick supersedes it anyway. */
    }
  };

  source.onerror = () => {
    onStatus(source.readyState === EventSource.CLOSED ? "disconnected" : "reconnecting");
  };

  return () => source.close();
}

const realApi: Api = {
  getWatchlist: () => request<Watchlist>("/api/watchlist"),
  addTicker: (ticker) =>
    request<Watchlist>("/api/watchlist", { method: "POST", body: JSON.stringify({ ticker }) }),
  removeTicker: (ticker) =>
    request<Watchlist>(`/api/watchlist/${encodeURIComponent(ticker)}`, { method: "DELETE" }),
  getPortfolio: () => request<Portfolio>("/api/portfolio"),
  getPortfolioHistory: (params) => {
    const query = new URLSearchParams();
    if (params?.since) query.set("since", params.since);
    if (params?.limit) query.set("limit", String(params.limit));
    const suffix = query.size ? `?${query}` : "";
    return request<PortfolioHistory>(`/api/portfolio/history${suffix}`);
  },
  trade: (input) =>
    request<TradeResult>("/api/portfolio/trade", { method: "POST", body: JSON.stringify(input) }),
  sendChat: (message) =>
    request<ChatResponse>("/api/chat", { method: "POST", body: JSON.stringify({ message }) }),
  openPriceStream: openRealPriceStream,
};

export const api: Api = USE_MOCK ? mockApi : realApi;
