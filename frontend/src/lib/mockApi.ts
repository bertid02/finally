/**
 * In-memory stand-in for the FastAPI backend, used until Stage 2 lands.
 *
 * It implements the same `Api` interface, the same error envelope codes, and
 * the same SSE payload shape (one tick object keyed by every tracked symbol,
 * emitted only when a price actually moves). Nothing outside `api.ts` imports
 * it, so removing it is a one-line change there.
 */

import type {
  Api,
  StreamHandlers,
  Unsubscribe,
} from "./api";
import { ApiError } from "./types";
import type {
  ChatActions,
  ChatResponse,
  Portfolio,
  PortfolioHistory,
  PortfolioSnapshot,
  Position,
  PriceTick,
  PriceUpdate,
  TradeResult,
  TradeSide,
  Watchlist,
} from "./types";

const TICKER_RE = /^[A-Z]{1,5}$/;
const MAX_WATCHLIST = 30;
const TICK_MS = 500;

/** Seed prices mirror `backend/app/market/seed_prices.py`. */
const SEEDS: Record<string, { price: number; sigma: number }> = {
  AAPL: { price: 190.0, sigma: 0.24 },
  GOOGL: { price: 175.0, sigma: 0.28 },
  MSFT: { price: 415.0, sigma: 0.22 },
  AMZN: { price: 182.0, sigma: 0.3 },
  TSLA: { price: 248.0, sigma: 0.55 },
  NVDA: { price: 122.0, sigma: 0.5 },
  META: { price: 505.0, sigma: 0.32 },
  JPM: { price: 208.0, sigma: 0.2 },
  V: { price: 275.0, sigma: 0.18 },
  NFLX: { price: 680.0, sigma: 0.35 },
};

interface Tracked {
  price: number;
  previous: number;
  sessionOpen: number;
  sigma: number;
}

const state = {
  watchlist: Object.keys(SEEDS),
  cash: 10000.0,
  positions: new Map<string, Position>(),
  snapshots: [] as PortfolioSnapshot[],
  tracked: new Map<string, Tracked>(),
};

function track(ticker: string): Tracked {
  const existing = state.tracked.get(ticker);
  if (existing) return existing;
  const seed = SEEDS[ticker] ?? { price: 20 + (hash(ticker) % 380), sigma: 0.25 };
  const entry: Tracked = {
    price: seed.price,
    previous: seed.price,
    sessionOpen: seed.price,
    sigma: seed.sigma,
  };
  state.tracked.set(ticker, entry);
  return entry;
}

function hash(text: string): number {
  let h = 0;
  for (const char of text) h = (h * 31 + char.charCodeAt(0)) >>> 0;
  return h;
}

function toUpdate(ticker: string, entry: Tracked): PriceUpdate {
  const change = round(entry.price - entry.previous, 4);
  const changeSession = round(entry.price - entry.sessionOpen, 4);
  return {
    ticker,
    price: round(entry.price, 2),
    previous_price: round(entry.previous, 2),
    session_open: round(entry.sessionOpen, 2),
    timestamp: Date.now() / 1000,
    change,
    change_percent: entry.previous ? round((change / entry.previous) * 100, 4) : 0,
    change_session: changeSession,
    change_percent_session: entry.sessionOpen
      ? round((changeSession / entry.sessionOpen) * 100, 4)
      : 0,
    direction: entry.price > entry.previous ? "up" : entry.price < entry.previous ? "down" : "flat",
  };
}

function round(value: number, places: number): number {
  const factor = 10 ** places;
  return Math.round(value * factor) / factor;
}

/** Geometric Brownian motion over one 500ms step, plus rare 2–5% shocks. */
function step(entry: Tracked): void {
  const dt = TICK_MS / 1000 / (252 * 6.5 * 3600);
  const drift = (0.05 - (entry.sigma * entry.sigma) / 2) * dt;
  const shock = entry.sigma * Math.sqrt(dt) * gaussian();
  let next = entry.price * Math.exp(drift + shock);
  if (Math.random() < 0.0008) {
    next *= 1 + (Math.random() < 0.5 ? -1 : 1) * (0.02 + Math.random() * 0.03);
  }
  entry.previous = entry.price;
  entry.price = Math.max(0.01, next);
}

function gaussian(): number {
  const u = Math.random() || 1e-9;
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * Math.random());
}

function totalValue(): number {
  let total = state.cash;
  for (const position of state.positions.values()) {
    const price = state.tracked.get(position.ticker)?.price ?? position.avg_cost;
    total += position.quantity * price;
  }
  return round(total, 2);
}

function latency<T>(value: T): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), 60 + Math.random() * 90));
}

function watchlistResponse(): Watchlist {
  return { tickers: [...state.watchlist] };
}

// --- streaming --------------------------------------------------------

let timer: ReturnType<typeof setInterval> | null = null;
const subscribers = new Set<StreamHandlers>();

function ensureTimer(): void {
  if (timer) return;
  timer = setInterval(() => {
    const tick: PriceTick = {};
    for (const ticker of state.watchlist) {
      const entry = track(ticker);
      step(entry);
      tick[ticker] = toUpdate(ticker, entry);
    }
    for (const subscriber of subscribers) subscriber.onTick(tick);
  }, TICK_MS);
}

function openMockPriceStream(handlers: StreamHandlers): Unsubscribe {
  subscribers.add(handlers);
  ensureTimer();
  const opened = setTimeout(() => handlers.onStatus("connected"), 120);
  return () => {
    clearTimeout(opened);
    subscribers.delete(handlers);
    if (subscribers.size === 0 && timer) {
      clearInterval(timer);
      timer = null;
    }
  };
}

// --- trading ----------------------------------------------------------

function executeTrade(ticker: string, quantity: number, side: TradeSide): TradeResult {
  const symbol = ticker.trim().toUpperCase();
  if (!TICKER_RE.test(symbol)) {
    throw new ApiError("INVALID_TICKER", `"${ticker}" is not a valid ticker symbol.`, 400);
  }
  if (side !== "buy" && side !== "sell") {
    throw new ApiError("INVALID_SIDE", "Side must be buy or sell.", 400);
  }
  if (!Number.isFinite(quantity) || quantity <= 0) {
    throw new ApiError("INVALID_QUANTITY", "Quantity must be greater than zero.", 400);
  }

  const entry = state.tracked.get(symbol);
  if (!entry) {
    throw new ApiError("UNKNOWN_TICKER", `No price available for ${symbol} yet.`, 404);
  }

  const price = round(entry.price, 2);
  const total = round(price * quantity, 2);
  const held = state.positions.get(symbol);

  if (side === "buy") {
    if (total > state.cash) {
      throw new ApiError(
        "INSUFFICIENT_CASH",
        `Insufficient cash: need ${money(total)}, have ${money(state.cash)}`,
        409,
      );
    }
    state.cash = round(state.cash - total, 2);
    const oldQty = held?.quantity ?? 0;
    const oldAvg = held?.avg_cost ?? 0;
    state.positions.set(symbol, {
      ticker: symbol,
      quantity: oldQty + quantity,
      avg_cost: round((oldQty * oldAvg + quantity * price) / (oldQty + quantity), 4),
    });
  } else {
    if (!held || held.quantity < quantity) {
      throw new ApiError(
        "INSUFFICIENT_SHARES",
        `Insufficient shares: tried to sell ${quantity}, hold ${held?.quantity ?? 0}`,
        409,
      );
    }
    state.cash = round(state.cash + total, 2);
    const remaining = held.quantity - quantity;
    // Selling never changes cost basis; a sell to zero deletes the row.
    if (remaining < 1e-9) state.positions.delete(symbol);
    else state.positions.set(symbol, { ...held, quantity: remaining });
  }

  state.snapshots.push({ total_value: totalValue(), recorded_at: new Date().toISOString() });

  return {
    trade: {
      id: crypto.randomUUID(),
      ticker: symbol,
      side,
      quantity,
      price,
      total,
      executed_at: new Date().toISOString(),
    },
    cash_balance: state.cash,
    position: state.positions.get(symbol) ?? null,
  };
}

function money(value: number): string {
  return `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

// --- chat -------------------------------------------------------------

/**
 * Deterministic canned responses, keyed on the first matching keyword. Mirrors
 * the intent of the backend's LLM_MOCK mode so the chat panel can be exercised
 * with real action chips, including a failing one.
 */
function mockChat(message: string): ChatResponse {
  const text = message.toLowerCase();
  const actions: ChatActions = {};

  if (text.includes("buy")) {
    const result = attempt(() => executeTrade("AAPL", 5, "buy"));
    actions.trades = [result];
    return {
      message:
        result.status === "executed"
          ? `Bought ${result.quantity} AAPL at ${money(result.price!)}. That is ${money(result.total!)} of your cash, leaving ${money(state.cash)}.`
          : `I couldn't place that order. ${result.error}`,
      actions,
      watchlist: [...state.watchlist],
      cash_balance: state.cash,
      positions: [...state.positions.values()],
    };
  }

  if (text.includes("watch") || text.includes("add ")) {
    // Read the symbol from the ORIGINAL message: people type tickers in caps,
    // and lowercasing first turns "Add PYPL to my watchlist" into "MY".
    const ticker = message.match(/\b[A-Z]{1,5}\b/)?.[0] ?? "PYPL";
    const before = state.watchlist.length;
    if (!state.watchlist.includes(ticker) && before < MAX_WATCHLIST) state.watchlist.push(ticker);
    actions.watchlist_changes = [{ ticker, action: "add", status: "executed" }];
    return {
      message: `Added ${ticker} to the watchlist. It will start streaming on the next tick.`,
      actions,
      watchlist: [...state.watchlist],
    };
  }

  if (text.includes("risk") || text.includes("concentr")) {
    return {
      message:
        state.positions.size === 0
          ? "You hold no positions, so all $10,000 sits in cash. That is zero market risk and zero expected return — pick a first name to build around."
          : `You hold ${state.positions.size} position${state.positions.size === 1 ? "" : "s"} worth ${money(totalValue() - state.cash)} against ${money(state.cash)} cash. The heatmap shows where the weight actually sits.`,
      watchlist: [...state.watchlist],
    };
  }

  return {
    message: `Portfolio stands at ${money(totalValue())} — ${money(state.cash)} cash across ${state.positions.size} position${state.positions.size === 1 ? "" : "s"}. Ask me to analyse concentration, or tell me what to buy.`,
    watchlist: [...state.watchlist],
  };
}

function attempt(run: () => TradeResult): NonNullable<ChatActions["trades"]>[number] {
  try {
    const { trade } = run();
    return {
      ticker: trade.ticker,
      side: trade.side,
      quantity: trade.quantity,
      status: "executed",
      price: trade.price,
      total: trade.total,
    };
  } catch (error) {
    const apiError = error as ApiError;
    return {
      ticker: "AAPL",
      side: "buy",
      quantity: 5,
      status: "failed",
      error_code: apiError.code,
      error: apiError.message,
    };
  }
}

// --- the exported facade ----------------------------------------------

export const mockApi: Api = {
  getWatchlist: () => latency(watchlistResponse()),

  addTicker: (ticker) => {
    const symbol = ticker.trim().toUpperCase();
    if (!TICKER_RE.test(symbol)) {
      return Promise.reject(
        new ApiError("INVALID_TICKER", `"${ticker}" is not a valid ticker symbol.`, 400),
      );
    }
    if (state.watchlist.includes(symbol)) return latency(watchlistResponse()); // idempotent
    if (state.watchlist.length >= MAX_WATCHLIST) {
      return Promise.reject(
        new ApiError("WATCHLIST_FULL", `The watchlist holds the maximum ${MAX_WATCHLIST} tickers.`, 409),
      );
    }
    state.watchlist.push(symbol);
    return latency(watchlistResponse());
  },

  removeTicker: (ticker) => {
    const symbol = ticker.trim().toUpperCase();
    state.watchlist = state.watchlist.filter((entry) => entry !== symbol);
    state.tracked.delete(symbol);
    return latency(watchlistResponse());
  },

  getPortfolio: () =>
    latency<Portfolio>({ cash_balance: state.cash, positions: [...state.positions.values()] }),

  getPortfolioHistory: (params) =>
    latency<PortfolioHistory>({ snapshots: state.snapshots.slice(-(params?.limit ?? 500)) }),

  trade: (input) => {
    try {
      return latency(executeTrade(input.ticker, input.quantity, input.side));
    } catch (error) {
      return Promise.reject(error);
    }
  },

  sendChat: (message) =>
    new Promise((resolve) => setTimeout(() => resolve(mockChat(message)), 500)),

  openPriceStream: openMockPriceStream,
};
