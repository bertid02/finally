/**
 * Wire types. Every shape here mirrors a contract defined outside the frontend:
 * PriceUpdate comes from `backend/app/market/models.py::PriceUpdate.to_dict`,
 * the rest from PLAN.md §8.
 */

/** One ticker's price at a point in time, exactly as the SSE stream sends it. */
export interface PriceUpdate {
  ticker: string;
  price: number;
  previous_price: number;
  session_open: number;
  timestamp: number; // unix seconds
  /** Tick-over-tick. Drives the flash animation. NEVER render as a number. */
  change: number;
  /** Tick-over-tick percent. NEVER render as a number — it is ±0.02% noise. */
  change_percent: number;
  /** Versus session_open. */
  change_session: number;
  /** Versus session_open. THIS is the displayed daily change %. */
  change_percent_session: number;
  direction: "up" | "down" | "flat";
}

/** One SSE event carries every tracked ticker, keyed by symbol. */
export type PriceTick = Record<string, PriceUpdate>;

export interface Position {
  ticker: string;
  quantity: number;
  avg_cost: number;
}

export interface Portfolio {
  cash_balance: number;
  positions: Position[];
}

export interface Trade {
  id: string;
  ticker: string;
  side: TradeSide;
  quantity: number;
  price: number;
  total: number;
  executed_at: string;
}

export type TradeSide = "buy" | "sell";

export interface TradeResult {
  trade: Trade;
  cash_balance: number;
  /** null when a sell closed the position entirely. */
  position: Position | null;
}

export interface Watchlist {
  tickers: string[];
}

export interface PortfolioSnapshot {
  total_value: number;
  recorded_at: string;
}

export interface PortfolioHistory {
  snapshots: PortfolioSnapshot[];
}

export type ErrorCode =
  | "INVALID_QUANTITY"
  | "INVALID_SIDE"
  | "INVALID_TICKER"
  | "UNKNOWN_TICKER"
  | "UNSUPPORTED_TICKER"
  | "INSUFFICIENT_CASH"
  | "INSUFFICIENT_SHARES"
  | "WATCHLIST_FULL";

/** The one error envelope every non-2xx response uses (PLAN.md §8). */
export interface ApiErrorBody {
  error: { code: ErrorCode | string; message: string };
}

/** `message` is user-facing prose and is shown verbatim. */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

// --- chat -------------------------------------------------------------

export type ActionStatus = "executed" | "failed";

export interface TradeAction {
  ticker: string;
  side: TradeSide;
  quantity: number;
  status: ActionStatus;
  price?: number;
  total?: number;
  error_code?: string;
  error?: string;
}

export interface WatchlistAction {
  ticker: string;
  action: "add" | "remove";
  status: ActionStatus;
  error_code?: string;
  error?: string;
}

/** Shape of `chat_messages.actions` (PLAN.md §7). */
export interface ChatActions {
  trades?: TradeAction[];
  watchlist_changes?: WatchlistAction[];
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  actions?: ChatActions | null;
  created_at: string;
}

/**
 * The chat turn's response. `watchlist` is the echo required by PLAN.md §8 —
 * the AI can change membership mid-turn and the SSE stream never carries it.
 * The client treats every field but `message` as optional and refetches
 * portfolio and watchlist after each turn regardless, so a backend that omits
 * the echoes is still correct, just one round-trip slower.
 */
export interface ChatResponse {
  message: string;
  actions?: ChatActions | null;
  watchlist?: string[];
  cash_balance?: number;
  positions?: Position[];
}
