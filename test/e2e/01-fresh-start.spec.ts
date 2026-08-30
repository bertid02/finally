import { expect, test } from "@playwright/test";

import { DEFAULT_WATCHLIST, STARTING_CASH, Terminal, parseFigure } from "../fixtures/terminal";

/**
 * PLAN.md §12: "Fresh start: default watchlist appears, $10k balance shown,
 * prices are streaming."
 *
 * This is the only spec in the suite that asserts an absolute cash figure, and
 * the only one that depends on running first. `docker-compose.test.yml` mounts a
 * tmpfs over `/app/db`, so every harness run starts from a database the backend
 * has just created and seeded — but a human pointing the suite at a container
 * they have been trading in would see a legitimate-looking failure here for an
 * illegitimate reason. The guard below says so out loud instead.
 */
test.describe("fresh start", () => {
  test("seeds the watchlist, the cash balance, and a live price stream", async ({
    page,
    request,
  }) => {
    // Sold separately from the UI: if anything has traded, the seeded balance is
    // gone and this spec is being run against the wrong container, not failing.
    const history = await request.get("/api/portfolio/history");
    expect(history.ok()).toBeTruthy();
    const { snapshots } = (await history.json()) as { snapshots: unknown[] };
    test.skip(
      snapshots.length > 0,
      "portfolio has already traded — this spec needs a freshly seeded container",
    );

    const terminal = new Terminal(page);
    await terminal.open();

    // --- the default watchlist, in seed order -------------------------
    await expect(terminal.watchlistRows).toHaveCount(DEFAULT_WATCHLIST.length);
    expect(await terminal.watchlistTickers()).toEqual([...DEFAULT_WATCHLIST]);
    await expect(terminal.watchlist.getByText("10/30")).toBeVisible();

    // --- $10,000, and nothing held ------------------------------------
    await expect(terminal.cash).toHaveText("$10,000.00");
    await expect(terminal.netLiquidation).toHaveText("$10,000.00");
    await expect(terminal.positionCount).toHaveText("0");
    await expect(
      terminal.positions.getByText("No open positions.", { exact: false }),
    ).toBeVisible();

    // --- prices are streaming -----------------------------------------
    await expect(terminal.connectionStatus).toHaveText("Live");

    // Every row carries a real price, not the em-dash placeholder.
    for (const ticker of DEFAULT_WATCHLIST) {
      await terminal.waitForPrice(ticker);
      expect(await terminal.priceValue(ticker)).toBeGreaterThan(0);
    }

    // The day % column is the session-based figure, never the tick-over-tick
    // one. Both are signed percentages, so the distinction cannot be asserted
    // from the text alone — what *can* be asserted is that it is rendered as a
    // signed percentage at all, and that it uses U+2212 for negatives the way
    // formatPercent does.
    await expect(terminal.dayPercent("AAPL")).toHaveText(/^[+−]\d+\.\d{2}%$/);

    // Streaming means moving: wait for a second, different tick to be painted.
    await terminal.waitForTick("AAPL");

    // --- the detail chart follows the lead symbol ---------------------
    // TerminalProvider selects tickers[0] on load.
    await expect(
      terminal.detailChart.getByRole("heading", { name: "AAPL", exact: true }),
    ).toBeVisible();
    await expect(terminal.detailChart.getByText(/^Open [\d,]+\.\d{2}$/)).toBeVisible();
    // Two accumulated points are enough to draw; at 500ms ticks that is ~1s.
    await expect(terminal.detailChart.locator(".recharts-area")).toBeVisible();
  });

  test("the health endpoint reports the simulator and mock LLM the suite assumes", async ({
    request,
  }) => {
    // Not decoration. Every chat spec asserts against `app/llm/mock.py`'s canned
    // replies, and every price assertion assumes the in-process simulator. If the
    // harness leaked a real MASSIVE_API_KEY or LLM_MOCK=false from a developer's
    // .env, the failures downstream would look like application bugs.
    const response = await request.get("/api/health");
    expect(response.ok()).toBeTruthy();
    const health = (await response.json()) as Record<string, unknown>;

    expect(health.status).toBe("ok");
    expect(health.market_source).toBe("simulator");
    expect(health.requested_source).toBe("simulator");
    expect(health.fallback).toBe(false);
    expect(health.llm_mock).toBe(true);
    // The static export is mounted — otherwise `/` returns FastAPI's JSON note
    // and every UI spec fails with a blank page.
    expect(health.static).toBe(true);
    expect(health.tickers).toBeGreaterThanOrEqual(DEFAULT_WATCHLIST.length);
  });

  test("the header's net liquidation equals cash while nothing is held", async ({
    page,
    request,
  }) => {
    const portfolio = (await (await request.get("/api/portfolio")).json()) as {
      cash_balance: number;
      positions: unknown[];
    };
    test.skip(portfolio.positions.length > 0, "already holding positions");

    const terminal = new Terminal(page);
    await terminal.open();

    const cash = await terminal.cashValue();
    const total = await terminal.netLiquidationValue();
    expect(cash).toBeCloseTo(portfolio.cash_balance, 2);
    expect(total).toBeCloseTo(cash, 2);
    expect(parseFigure(await terminal.positionCount.textContent())).toBe(0);
    expect(cash).toBeLessThanOrEqual(STARTING_CASH);
  });
});
