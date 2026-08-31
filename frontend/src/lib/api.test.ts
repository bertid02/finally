import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Api } from "./api";
import type { ApiError as ApiErrorType } from "./types";

/**
 * Exercises the REAL implementation (the mock branch is bypassed by stubbing
 * the env var before import), because this is the code that ships.
 */

class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static last: FakeEventSource | null = null;

  readyState = FakeEventSource.CONNECTING;
  closed = false;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(readonly url: string) {
    FakeEventSource.last = this;
  }

  open() {
    this.readyState = FakeEventSource.OPEN;
    this.onopen?.();
  }

  send(data: string) {
    this.onmessage?.({ data } as MessageEvent<string>);
  }

  fail(readyState: number) {
    this.readyState = readyState;
    this.onerror?.();
  }

  close() {
    this.closed = true;
    this.readyState = FakeEventSource.CLOSED;
  }
}

let api: Api;
// `vi.resetModules()` gives ./api a fresh copy of ./types, so the class the
// assertions compare against has to come from that same fresh graph.
let ApiError: typeof import("./types").ApiError;

beforeEach(async () => {
  vi.stubEnv("NEXT_PUBLIC_USE_MOCK_API", "false");
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.resetModules();
  api = (await import("./api")).api;
  ApiError = (await import("./types")).ApiError;
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("price stream", () => {
  it("parses a tick carrying every ticker in one event", () => {
    const onTick = vi.fn();
    const onStatus = vi.fn();
    api.openPriceStream({ onTick, onStatus });

    const source = FakeEventSource.last!;
    expect(source.url).toBe("/api/stream/prices");
    source.open();
    expect(onStatus).toHaveBeenCalledWith("connected");

    source.send(
      JSON.stringify({
        AAPL: { ticker: "AAPL", price: 190.5 },
        GOOGL: { ticker: "GOOGL", price: 175.1 },
      }),
    );

    expect(onTick).toHaveBeenCalledWith({
      AAPL: { ticker: "AAPL", price: 190.5 },
      GOOGL: { ticker: "GOOGL", price: 175.1 },
    });
  });

  it("treats silence as an idle cache, never as a disconnect", () => {
    vi.useFakeTimers();
    const onStatus = vi.fn();
    api.openPriceStream({ onTick: vi.fn(), onStatus });

    FakeEventSource.last!.open();
    onStatus.mockClear();

    // The stream only emits when the cache version changes. Five minutes of
    // nothing is a market that did not move, and must not change status.
    vi.advanceTimersByTime(300_000);
    expect(onStatus).not.toHaveBeenCalled();
  });

  it("reports reconnecting while EventSource retries and offline once closed", () => {
    const onStatus = vi.fn();
    api.openPriceStream({ onTick: vi.fn(), onStatus });
    const source = FakeEventSource.last!;

    source.fail(FakeEventSource.CONNECTING);
    expect(onStatus).toHaveBeenLastCalledWith("reconnecting");

    source.fail(FakeEventSource.CLOSED);
    expect(onStatus).toHaveBeenLastCalledWith("disconnected");
  });

  it("drops a malformed frame instead of throwing", () => {
    const onTick = vi.fn();
    api.openPriceStream({ onTick, onStatus: vi.fn() });
    expect(() => FakeEventSource.last!.send("{not json")).not.toThrow();
    expect(onTick).not.toHaveBeenCalled();
  });

  it("closes the connection when unsubscribed", () => {
    const unsubscribe = api.openPriceStream({ onTick: vi.fn(), onStatus: vi.fn() });
    unsubscribe();
    expect(FakeEventSource.last!.closed).toBe(true);
  });
});

describe("REST calls", () => {
  it("surfaces the server's user-facing message from the error envelope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            error: {
              code: "INSUFFICIENT_CASH",
              message: "Insufficient cash: need $80,000.00, have $8,095.00",
            },
          }),
          { status: 409 },
        ),
      ),
    );

    const failure = await api
      .trade({ ticker: "NVDA", quantity: 100, side: "buy" })
      .catch((error: unknown) => error as ApiErrorType);

    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiErrorType).code).toBe("INSUFFICIENT_CASH");
    expect((failure as ApiErrorType).message).toBe(
      "Insufficient cash: need $80,000.00, have $8,095.00",
    );
    expect((failure as ApiErrorType).status).toBe(409);
  });

  it("falls back to a readable message when the body is not the envelope", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>502</html>", { status: 502 })));
    await expect(api.getPortfolio()).rejects.toThrow("Request failed (502).");
  });

  it("reports an unreachable server rather than leaking the fetch error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("failed to fetch"); }));
    await expect(api.getWatchlist()).rejects.toThrow("Can't reach the server.");
  });

  it("builds the history query from since and limit", async () => {
    const fetchMock = vi.fn(async (_input: string, _init?: RequestInit) =>
      new Response(JSON.stringify({ snapshots: [] })),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.getPortfolioHistory({ since: "2026-08-24T04:11:00Z", limit: 250 });
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/portfolio/history?since=2026-08-24T04%3A11%3A00Z&limit=250",
    );
  });

  it("posts a trade to the documented endpoint", async () => {
    const fetchMock = vi.fn(async (_input: string, _init?: RequestInit) =>
      new Response(JSON.stringify({ trade: {}, cash_balance: 0, position: null })),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.trade({ ticker: "AAPL", quantity: 10, side: "buy" });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/portfolio/trade");
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      ticker: "AAPL",
      quantity: 10,
      side: "buy",
    });
  });
});
