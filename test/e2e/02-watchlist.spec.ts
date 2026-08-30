import { expect, test } from "@playwright/test";

import { Terminal } from "../fixtures/terminal";

/**
 * PLAN.md §12: "Add and remove a ticker from the watchlist."
 *
 * The contract being exercised here is the one TEAM.md calls out as a source of
 * silent bugs: **the watchlist has no push path.** The SSE stream carries prices
 * only, so membership refreshes exclusively from the return value of
 * `POST`/`DELETE /api/watchlist`. If the backend ever returned something other
 * than the complete new list, the UI would go stale in a way no unit test on
 * either side would catch.
 *
 * Each test restores what it changed, so the file can be re-run and so the
 * specs that follow see the seeded ten.
 */
test.describe("watchlist", () => {
  const ADDED = "PYPL";

  test("adds a ticker, streams it, and removes it again", async ({ page }) => {
    const terminal = new Terminal(page);
    await terminal.open();

    const before = await terminal.watchlistTickers();
    expect(before).not.toContain(ADDED);

    // --- add ----------------------------------------------------------
    await terminal.addTicker(ADDED);

    // The row appears only because the POST returned the new list.
    await expect(terminal.price(ADDED)).toBeVisible();
    await expect(terminal.watchlistRows).toHaveCount(before.length + 1);
    expect(await terminal.watchlistTickers()).toEqual([...before, ADDED]);
    await expect(terminal.watchlist.getByText(`${before.length + 1}/30`)).toBeVisible();

    // The source starts tracking it, so a price arrives on a subsequent tick.
    // Under the simulator this is immediate; the wait is what makes the same
    // spec honest under Massive's 15s poll.
    await terminal.waitForPrice(ADDED);
    expect(await terminal.priceValue(ADDED)).toBeGreaterThan(0);
    await expect(terminal.dayPercent(ADDED)).toHaveText(/^[+−]\d+\.\d{2}%$/);

    // --- remove -------------------------------------------------------
    await terminal.removeTicker(ADDED);
    await expect(terminal.price(ADDED)).toHaveCount(0);
    await expect(terminal.watchlistRows).toHaveCount(before.length);
    expect(await terminal.watchlistTickers()).toEqual(before);
  });

  test("adding a ticker already on the list is idempotent, not an error", async ({ page }) => {
    // PLAN.md §8 rule 3: a double-click must not surface as an error.
    const terminal = new Terminal(page);
    await terminal.open();

    const before = await terminal.watchlistTickers();
    await terminal.addTicker(before[0]);

    await expect(terminal.watchlistRows).toHaveCount(before.length);
    expect(await terminal.watchlistTickers()).toEqual(before);
    // No error notice in the order bar, which is where the provider routes
    // watchlist failures.
    await expect(terminal.orderError).toHaveCount(0);
  });

  test("removing a ticker that is not on the list is idempotent", async ({ page, request }) => {
    const terminal = new Terminal(page);
    await terminal.open();
    const before = await terminal.watchlistTickers();

    // No UI path removes an absent ticker, so this one goes at the endpoint.
    const response = await request.delete("/api/watchlist/ZZZZ");
    expect(response.status()).toBe(200);
    expect(((await response.json()) as { tickers: string[] }).tickers).toEqual(before);
  });

  test("a malformed symbol is rejected with the backend's own message", async ({ page }) => {
    // The simulator answers `supports_ticker` True for any well-formed symbol,
    // so UNSUPPORTED_TICKER (422) is unreachable here by design — the reachable
    // rejection is the format rule, INVALID_TICKER (400). The point of the
    // assertion is that `error.message` reaches the user verbatim.
    const terminal = new Terminal(page);
    await terminal.open();
    const before = await terminal.watchlistTickers();

    await terminal.addTicker("AB1");

    await expect(terminal.orderError).toBeVisible();
    await expect(terminal.orderError).toHaveText(/Invalid ticker symbol: 'AB1'/);
    expect(await terminal.watchlistTickers()).toEqual(before);
  });

  test("removing the selected ticker moves the detail chart to another symbol", async ({
    page,
  }) => {
    const terminal = new Terminal(page);
    await terminal.open();
    const before = await terminal.watchlistTickers();
    const selected = before[0];

    await expect(
      terminal.detailChart.getByRole("heading", { name: selected, exact: true }),
    ).toBeVisible();

    await terminal.removeTicker(selected);
    await expect(terminal.price(selected)).toHaveCount(0);

    // TerminalProvider re-selects tickers[0] of the returned list.
    const after = await terminal.watchlistTickers();
    await expect(
      terminal.detailChart.getByRole("heading", { name: after[0], exact: true }),
    ).toBeVisible();

    // Put it back where the seed had it so later specs see the same list. The
    // insertion order changes — the row moves to the end — which is expected and
    // is why no later spec asserts on seed ordering.
    await terminal.addTicker(selected);
    await expect(terminal.price(selected)).toBeVisible();
    await expect(terminal.watchlistRows).toHaveCount(before.length);
  });
});
