"use client";

/**
 * One store for the whole console.
 *
 * It owns the price stream, the accumulated price series (sparklines and the
 * detail chart are built here, not fetched — the backend has no history
 * endpoint for prices), the watchlist, the portfolio, and the chat transcript.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { api, type StreamStatus } from "@/lib/api";
import { ApiError } from "@/lib/types";
import type {
  ChatMessage,
  PortfolioSnapshot,
  Position,
  PriceTick,
  PriceUpdate,
  TradeResult,
  TradeSide,
} from "@/lib/types";

/** One accumulated price observation. */
export interface Point {
  t: number; // unix seconds
  p: number;
}

/** Enough for ~5 minutes of 500ms ticks in the detail chart. */
const SERIES_CAP = 600;
const EQUITY_CAP = 900;
/** The equity curve samples once a second; 500ms ticks would be noise. */
const EQUITY_SAMPLE_MS = 1000;

export interface TerminalState {
  prices: Record<string, PriceUpdate>;
  series: Record<string, Point[]>;
  status: StreamStatus;
  watchlist: string[];
  cash: number;
  positions: Position[];
  snapshots: PortfolioSnapshot[];
  liveEquity: Point[];
  selected: string | null;
  messages: ChatMessage[];
  chatPending: boolean;
  notice: { tone: "error" | "info"; text: string } | null;
  totalValue: number;
  select(ticker: string): void;
  addTicker(ticker: string): Promise<void>;
  removeTicker(ticker: string): Promise<void>;
  trade(input: { ticker: string; quantity: number; side: TradeSide }): Promise<TradeResult | null>;
  sendChat(message: string): Promise<void>;
  dismissNotice(): void;
}

const TerminalContext = createContext<TerminalState | null>(null);

export function useTerminal(): TerminalState {
  const context = useContext(TerminalContext);
  if (!context) throw new Error("useTerminal must be used inside <TerminalProvider>");
  return context;
}

export function TerminalProvider({ children }: { children: ReactNode }) {
  const [prices, setPrices] = useState<Record<string, PriceUpdate>>({});
  const [series, setSeries] = useState<Record<string, Point[]>>({});
  const [status, setStatus] = useState<StreamStatus>("reconnecting");
  const [watchlist, setWatchlist] = useState<string[]>([]);
  const [cash, setCash] = useState(0);
  const [positions, setPositions] = useState<Position[]>([]);
  const [snapshots, setSnapshots] = useState<PortfolioSnapshot[]>([]);
  const [liveEquity, setLiveEquity] = useState<Point[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [chatPending, setChatPending] = useState(false);
  const [notice, setNotice] = useState<TerminalState["notice"]>(null);

  // Read by the stream callback, which must not re-subscribe when they change.
  const holdings = useRef<{ cash: number; positions: Position[] }>({ cash: 0, positions: [] });
  holdings.current = { cash, positions };
  const priceRef = useRef<Record<string, PriceUpdate>>({});
  priceRef.current = prices;
  const lastSample = useRef(0);

  const fail = useCallback((error: unknown) => {
    const text =
      error instanceof ApiError ? error.message : "Something went wrong. Try that again.";
    setNotice({ tone: "error", text });
  }, []);

  // --- initial load ---------------------------------------------------

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [list, portfolio, history] = await Promise.all([
          api.getWatchlist(),
          api.getPortfolio(),
          api.getPortfolioHistory({ limit: 500 }),
        ]);
        if (cancelled) return;
        setWatchlist(list.tickers);
        setSelected((current) => current ?? list.tickers[0] ?? null);
        setCash(portfolio.cash_balance);
        setPositions(portfolio.positions);
        setSnapshots(history.snapshots ?? []);
      } catch (error) {
        if (!cancelled) fail(error);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [fail]);

  // --- price stream ---------------------------------------------------

  useEffect(() => {
    const onTick = (tick: PriceTick) => {
      setPrices((current) => ({ ...current, ...tick }));

      setSeries((current) => {
        const next = { ...current };
        for (const [ticker, update] of Object.entries(tick)) {
          const existing = next[ticker] ?? [];
          const appended = [...existing, { t: update.timestamp, p: update.price }];
          next[ticker] = appended.length > SERIES_CAP ? appended.slice(-SERIES_CAP) : appended;
        }
        return next;
      });

      const now = Date.now();
      if (now - lastSample.current >= EQUITY_SAMPLE_MS) {
        lastSample.current = now;
        const { cash: heldCash, positions: held } = holdings.current;
        const marked = held.reduce((sum, position) => {
          const price = tick[position.ticker]?.price ?? position.avg_cost;
          return sum + position.quantity * price;
        }, 0);
        setLiveEquity((current) => {
          const appended = [...current, { t: now / 1000, p: heldCash + marked }];
          return appended.length > EQUITY_CAP ? appended.slice(-EQUITY_CAP) : appended;
        });
      }
    };

    // No inactivity timer: the stream emits only when the cache version
    // changes, so silence means "nothing moved", never "disconnected".
    return api.openPriceStream({ onTick, onStatus: setStatus });
  }, []);

  // --- derived --------------------------------------------------------

  const totalValue = useMemo(
    () =>
      positions.reduce(
        (sum, position) => sum + position.quantity * (prices[position.ticker]?.price ?? position.avg_cost),
        cash,
      ),
    [positions, prices, cash],
  );

  // --- actions --------------------------------------------------------

  const applyPortfolio = useCallback((nextCash: number, nextPositions: Position[]) => {
    setCash(nextCash);
    setPositions(nextPositions);
  }, []);

  const addTicker = useCallback(
    async (ticker: string) => {
      try {
        const list = await api.addTicker(ticker);
        setWatchlist(list.tickers);
      } catch (error) {
        fail(error);
      }
    },
    [fail],
  );

  const removeTicker = useCallback(
    async (ticker: string) => {
      try {
        const list = await api.removeTicker(ticker);
        setWatchlist(list.tickers);
        setSelected((current) =>
          current === ticker ? (list.tickers[0] ?? null) : current,
        );
      } catch (error) {
        fail(error);
      }
    },
    [fail],
  );

  const trade = useCallback<TerminalState["trade"]>(
    async (input) => {
      try {
        const result = await api.trade(input);
        setCash(result.cash_balance);
        setPositions((current) => {
          const others = current.filter((position) => position.ticker !== result.trade.ticker);
          return result.position ? [...others, result.position] : others;
        });
        // A snapshot records TOTAL value — cash plus every position marked to
        // the latest price — not the cash balance.
        const after = result.position
          ? [
              ...holdings.current.positions.filter((p) => p.ticker !== result.trade.ticker),
              result.position,
            ]
          : holdings.current.positions.filter((p) => p.ticker !== result.trade.ticker);
        const marked = after.reduce(
          (sum, p) => sum + p.quantity * (priceRef.current[p.ticker]?.price ?? p.avg_cost),
          result.cash_balance,
        );
        setSnapshots((current) => [
          ...current,
          { total_value: marked, recorded_at: result.trade.executed_at },
        ]);
        setNotice({
          tone: "info",
          text: `${result.trade.side === "buy" ? "Bought" : "Sold"} ${result.trade.quantity} ${result.trade.ticker} at ${result.trade.price.toFixed(2)}`,
        });
        return result;
      } catch (error) {
        fail(error);
        return null;
      }
    },
    [fail],
  );

  const sendChat = useCallback(
    async (text: string) => {
      const outgoing: ChatMessage = {
        id: `local-${Date.now()}`,
        role: "user",
        content: text,
        created_at: new Date().toISOString(),
      };
      setMessages((current) => [...current, outgoing]);
      setChatPending(true);
      try {
        const response = await api.sendChat(text);
        setMessages((current) => [
          ...current,
          {
            id: `assistant-${Date.now()}`,
            role: "assistant",
            content: response.message,
            actions: response.actions ?? null,
            created_at: new Date().toISOString(),
          },
        ]);

        // The AI can trade and change the watchlist mid-turn, and neither
        // reaches us over the stream. Prefer the echoed values; refetch what
        // the response did not carry.
        if (response.watchlist) setWatchlist(response.watchlist);
        if (response.cash_balance !== undefined && response.positions) {
          applyPortfolio(response.cash_balance, response.positions);
        }

        const needsWatchlist = !response.watchlist;
        const needsPortfolio = response.cash_balance === undefined || !response.positions;
        const [list, portfolio] = await Promise.all([
          needsWatchlist ? api.getWatchlist() : null,
          needsPortfolio ? api.getPortfolio() : null,
        ]);
        if (list) setWatchlist(list.tickers);
        if (portfolio) applyPortfolio(portfolio.cash_balance, portfolio.positions);
      } catch (error) {
        fail(error);
      } finally {
        setChatPending(false);
      }
    },
    [applyPortfolio, fail],
  );

  const value = useMemo<TerminalState>(
    () => ({
      prices,
      series,
      status,
      watchlist,
      cash,
      positions,
      snapshots,
      liveEquity,
      selected,
      messages,
      chatPending,
      notice,
      totalValue,
      select: setSelected,
      addTicker,
      removeTicker,
      trade,
      sendChat,
      dismissNotice: () => setNotice(null),
    }),
    [
      prices,
      series,
      status,
      watchlist,
      cash,
      positions,
      snapshots,
      liveEquity,
      selected,
      messages,
      chatPending,
      notice,
      totalValue,
      addTicker,
      removeTicker,
      trade,
      sendChat,
    ],
  );

  return <TerminalContext.Provider value={value}>{children}</TerminalContext.Provider>;
}
