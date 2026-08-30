import { expect, test } from "@playwright/test";

import { Terminal } from "../fixtures/terminal";

/**
 * PLAN.md §12: "Buy shares: cash decreases, position appears, portfolio updates."
 *
 * **Nothing here is asserted against a frozen price literal.** The trade request
 * body carries no price — `POST /api/portfolio/trade` fills at the server's
 * cached price at the moment of execution, and under a 500ms simulator that
 * price has already moved by the time the click lands. Every figure below is
 * either a relationship (cash went down by the notional that actually filled) or
 * a tolerance.
 */
test.describe("buying", () => {
  test("a market buy debits cash, opens a position, and repaints the header", async ({ page }) => {
    const terminal = new Terminal(page);
    await terminal.open();

    const ticker = "MSFT";
    const quantity = 3;
    await terminal.waitForPrice(ticker);

    const cashBefore = await terminal.cashValue();
    const heldBefore = (await terminal.positionRow(ticker).count())
      ? await terminal.heldQuantity(ticker)
      : 0;
    const positionsBefore = await terminal.positionRows.count();

    // The fill price comes back from the confirmation, which is the only place
    // the client learns what it actually paid.
    const fill = await terminal.placeOrder(ticker, quantity, "buy");
    expect(fill.quantity).toBe(quantity);
    expect(fill.price).toBeGreaterThan(0);

    // --- cash fell by exactly the notional that filled -----------------
    // Two cents of tolerance: the server rounds money to 2dp, and the notice
    // reports a price already rounded to 2dp, so quantity x price can differ
    // from the booked total in the last cent.
    await expect
      .poll(() => terminal.cashValue())
      .toBeCloseTo(cashBefore - fill.total, 1);
    expect(await terminal.cashValue()).toBeLessThan(cashBefore);

    // --- the position exists -------------------------------------------
    await expect(terminal.positionRow(ticker)).toBeVisible();
    expect(await terminal.heldQuantity(ticker)).toBeCloseTo(heldBefore + quantity, 6);
    if (heldBefore === 0) {
      await expect(terminal.positionRows).toHaveCount(positionsBefore + 1);
      await expect(terminal.positionCount).toHaveText(String(positionsBefore + 1));
    }

    // Average cost is the fill price on a first buy; on a top-up it is the
    // weighted average, which sits between the two fills either way.
    const cells = terminal.positionRow(ticker).locator("td");
    const avgCost = Number((await cells.nth(2).textContent())?.replace(/,/g, ""));
    expect(avgCost).toBeGreaterThan(0);
    if (heldBefore === 0) expect(avgCost).toBeCloseTo(fill.price, 2);

    // --- net liquidation is roughly conserved across the trade ---------
    // A market order with no fees moves value between cash and stock; the only
    // drift is the price ticking between the fill and the repaint.
    const total = await terminal.netLiquidationValue();
    expect(Math.abs(total - cashBefore)).toBeLessThan(Math.max(fill.total * 0.05, 1));
  });

  test("the buy button stays disabled until a price for the symbol arrives", async ({ page }) => {
    // PLAN.md §8: a ticker with no cached price cannot be traded
    // (UNKNOWN_TICKER), so the frontend must not offer the button. `ZZZZ` is a
    // well-formed symbol that is not on the watchlist, so nothing streams it.
    const terminal = new Terminal(page);
    await terminal.open();

    await page.locator("#trade-ticker").fill("ZZZZ");
    await page.locator("#trade-quantity").fill("1");

    await expect(terminal.buyButton).toBeDisabled();
    await expect(terminal.sellButton).toBeDisabled();
    await expect(terminal.orderBar.getByText("Waiting for a price on ZZZZ…")).toBeVisible();
  });

  test("a quantity of zero disables both sides", async ({ page }) => {
    const terminal = new Terminal(page);
    await terminal.open();
    await terminal.waitForPrice("AAPL");

    await page.locator("#trade-ticker").fill("AAPL");
    await page.locator("#trade-quantity").fill("0");
    await expect(terminal.buyButton).toBeDisabled();

    await page.locator("#trade-quantity").fill("-5");
    await expect(terminal.buyButton).toBeDisabled();

    await page.locator("#trade-quantity").fill("2");
    await expect(terminal.buyButton).toBeEnabled();
  });

  test("a buy larger than the cash balance is rejected with the server's message", async ({
    page,
  }) => {
    const terminal = new Terminal(page);
    await terminal.open();
    await terminal.waitForPrice("NVDA");

    const cashBefore = await terminal.cashValue();

    await page.locator("#trade-ticker").fill("NVDA");
    await page.locator("#trade-quantity").fill("100000");
    await expect(terminal.buyButton).toBeEnabled();
    await terminal.buyButton.click();

    // INSUFFICIENT_CASH (409). The message is user-facing prose and is rendered
    // verbatim — the same words the chat panel shows for the same failure.
    await expect(terminal.orderError).toBeVisible();
    await expect(terminal.orderError).toHaveText(/^Insufficient cash: need \$[\d,]+\.\d{2}, have \$/);

    // Nothing partially applied.
    expect(await terminal.cashValue()).toBeCloseTo(cashBefore, 2);
  });

  test("selecting a watchlist symbol loads it into the order bar", async ({ page }) => {
    const terminal = new Terminal(page);
    await terminal.open();

    await terminal.selectTicker("TSLA");
    await expect(page.locator("#trade-ticker")).toHaveValue("TSLA");
    await expect(
      terminal.detailChart.getByRole("heading", { name: "TSLA", exact: true }),
    ).toBeVisible();

    // "Est. Notional" is quantity x the live price, so it must be a figure
    // rather than the em-dash placeholder once a price is in.
    await page.locator("#trade-quantity").fill("2");
    await expect(terminal.orderBar.getByText(/^\$[\d,]+\.\d{2}$/).first()).toBeVisible();
  });
});
