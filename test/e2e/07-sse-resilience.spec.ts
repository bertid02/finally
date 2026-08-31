import { expect, test, type Route } from "@playwright/test";

import { Terminal } from "../fixtures/terminal";

/**
 * PLAN.md §12: "SSE resilience: disconnect and verify reconnection."
 *
 * Two invariants are being defended here, and the second one is the subtle one.
 *
 * 1. `EventSource` reconnects on its own using the server's `retry: 1000`
 *    directive, and the connection dot follows `readyState` — nothing else.
 * 2. **Silence is not a disconnect** (TEAM.md). `app/market/stream.py` emits
 *    only when `PriceCache.version` changes, so an idle cache sends no events at
 *    all. `api.ts` deliberately has no inactivity timer, and neither does this
 *    spec: no assertion below fails merely because prices stopped moving.
 *
 * The stream itself is never read with a plain HTTP client. `/api/stream/prices`
 * is endless, and a buffering client waits forever for a response that does not
 * end — the backend's own suite hit this. Only the browser's `EventSource`
 * drives it here.
 */
test.describe("price stream resilience", () => {
  test("shows Live and paints ticks while connected", async ({ page }) => {
    const terminal = new Terminal(page);
    await terminal.open();

    await expect(terminal.connectionStatus).toHaveText("Live");
    await terminal.waitForTick("AAPL");
    await terminal.waitForTick("MSFT");
  });

  test("a stream that ends mid-session goes non-live, keeps its prices, and reconnects", async ({
    page,
  }) => {
    // `context.setOffline(true)` is deliberately not used here, and the reason is
    // worth recording: Chromium's offline emulation refuses *new* requests but
    // leaves an already-established socket alone. Measured against this app, an
    // open `/api/stream/prices` kept delivering events for the whole offline
    // window while `fetch("/api/health")` threw — so an offline-based version of
    // this test asserts a transition the browser never makes, and fails on a
    // healthy app. A stream that ends is the real-world drop (a redeploy, a proxy
    // timeout, a container restart) and it is reproducible.
    const terminal = new Terminal(page);

    // One tick, then end the body. `EventSource` sees the close, fires onerror
    // with readyState CONNECTING, and schedules the retry the `retry: 1000`
    // directive asks for.
    const price = 187.65;
    const oneTickThenClose = async (route: Route) =>
      route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        headers: { "cache-control": "no-cache" },
        body:
          "retry: 1000\n\n" +
          `data: ${JSON.stringify({
            AAPL: {
              ticker: "AAPL",
              price,
              previous_price: price,
              session_open: 190,
              timestamp: Date.now() / 1000,
              change: 0,
              change_percent: 0,
              change_session: price - 190,
              change_percent_session: -1.2368,
              direction: "flat",
            },
          })}\n\n`,
      });
    await page.route("**/api/stream/prices", oneTickThenClose);

    await page.goto("/");
    // The tick landed and was painted.
    await expect(terminal.price("AAPL")).toHaveText(price.toFixed(2));
    // Then the stream ended: the dot follows readyState, nothing else.
    await expect(terminal.connectionStatus).toHaveText(/^(Reconnecting|Offline)$/);
    // And the last known price is still on screen — a dropped stream must not
    // blank the console.
    await expect(terminal.price("AAPL")).toHaveText(price.toFixed(2));

    // Let the retries through. Nothing in the app calls reconnect.
    await page.unroute("**/api/stream/prices", oneTickThenClose);
    await expect(terminal.connectionStatus).toHaveText("Live", { timeout: 30_000 });

    // A live stream again, not just a green dot: the real server's price for
    // AAPL replaces the injected one.
    await expect
      .poll(() => terminal.priceValue("AAPL"), { timeout: 15_000 })
      .not.toBeCloseTo(price, 2);
    await terminal.waitForTick("AAPL");
  });

  test("recovers when the stream endpoint itself fails and then returns", async ({ page }) => {
    // A server-side failure rather than a network one: the endpoint refuses the
    // connection while everything else keeps working. This is the case where a
    // client that treated silence as a disconnect and one that follows
    // readyState behave differently.
    const terminal = new Terminal(page);
    // The handler is removed rather than switched to `continue()`: routing an
    // endless response back through the interceptor is exactly the buffering
    // trap the backend suite hit. Once unrouted, the retry goes straight to the
    // network with no interception at all.
    const refuse = async (route: Route) => route.abort("connectionrefused");
    await page.route("**/api/stream/prices", refuse);

    await page.goto("/");
    // The REST calls still land, so the console renders with no prices.
    await expect.poll(() => terminal.watchlistRows.count()).toBeGreaterThan(0);
    await expect(terminal.connectionStatus).toHaveText(/^(Reconnecting|Offline)$/);
    await expect(terminal.price("AAPL")).toHaveText("—");
    // No price means no tradeable symbol.
    await page.locator("#trade-ticker").fill("AAPL");
    await expect(terminal.buyButton).toBeDisabled();

    // Let the retry through.
    await page.unroute("**/api/stream/prices", refuse);
    await expect(terminal.connectionStatus).toHaveText("Live", { timeout: 30_000 });
    await terminal.waitForPrice("AAPL");
    await expect(terminal.buyButton).toBeEnabled();
  });

  test("a reload re-opens the stream and rebuilds the sparkline series", async ({ page }) => {
    // Sparklines and the detail chart are accumulated client-side from the
    // stream since page load (PLAN.md §2), so a reload legitimately starts them
    // over. What must survive is the connection and the server-side state.
    const terminal = new Terminal(page);
    await terminal.open();
    await terminal.waitForTick("AAPL");

    await page.reload();
    await expect(terminal.connectionStatus).toHaveText("Live");
    await terminal.waitForPrice("AAPL");

    // The detail chart needs two accumulated points before it draws; that it
    // does so after a reload is the proof the stream re-opened.
    await expect(terminal.detailChart.locator(".recharts-area")).toBeVisible({
      timeout: 20_000,
    });
    // Every watchlist row has a sparkline once points accumulate.
    await expect
      .poll(() => terminal.watchlist.locator("svg").count(), { timeout: 20_000 })
      .toBeGreaterThan(0);
  });

  test("the stream carries every tracked ticker in one event", async ({ page }) => {
    // The payload shape is a shipped contract (`app/market/stream.py`): one event
    // per tick, keyed by symbol, carrying `session_open` and
    // `change_percent_session` alongside the tick-over-tick fields. Read here
    // through the page's own EventSource — never through a buffering HTTP client.
    const terminal = new Terminal(page);
    await terminal.open();

    const tick = await page.evaluate<Record<string, Record<string, unknown>>>(
      () =>
        new Promise((resolve, reject) => {
          const source = new EventSource("/api/stream/prices");
          const timer = setTimeout(() => {
            source.close();
            reject(new Error("no SSE event within 30s"));
          }, 30_000);
          source.onmessage = (event) => {
            clearTimeout(timer);
            source.close();
            resolve(JSON.parse(event.data));
          };
          source.onerror = () => {
            clearTimeout(timer);
            source.close();
            reject(new Error("SSE connection failed"));
          };
        }),
    );

    const symbols = Object.keys(tick);
    const watched = await terminal.watchlistTickers();
    expect(symbols.length).toBeGreaterThanOrEqual(watched.length);
    for (const ticker of watched) expect(symbols).toContain(ticker);

    const aapl = tick.AAPL;
    for (const field of [
      "ticker",
      "price",
      "previous_price",
      "session_open",
      "timestamp",
      "change",
      "change_percent",
      "change_percent_session",
      "direction",
    ]) {
      expect(aapl, `missing ${field}`).toHaveProperty(field);
    }
    expect(aapl.ticker).toBe("AAPL");
    expect(aapl.price as number).toBeGreaterThan(0);
    expect(aapl.session_open as number).toBeGreaterThan(0);
    expect(["up", "down", "flat"]).toContain(aapl.direction);
  });
});
