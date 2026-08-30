import { expect, test } from "@playwright/test";

import { Terminal } from "../fixtures/terminal";

/**
 * PLAN.md §12: "Sell shares: cash increases, position updates or disappears."
 *
 * §13.2 flagged "updates or disappears" as a place two agents would pick two
 * answers; §7 settled it — a sell to within 1e-9 of zero **deletes** the row, and
 * `avg_cost` is never touched by a sell. Both halves are asserted here, because
 * a position left at `quantity = 0` would still render a plausible-looking table.
 *
 * Each test opens its own position rather than inheriting one, so the file does
 * not depend on 03 having run.
 */
test.describe("selling", () => {
  const TICKER = "AMZN";

  test("a partial sell credits cash and leaves the cost basis alone", async ({ page }) => {
    const terminal = new Terminal(page);
    await terminal.open();
    await terminal.waitForPrice(TICKER);

    // Open a known position.
    await terminal.placeOrder(TICKER, 4, "buy");
    await expect(terminal.positionRow(TICKER)).toBeVisible();

    const cells = terminal.positionRow(TICKER).locator("td");
    const avgCostBefore = Number((await cells.nth(2).textContent())?.replace(/,/g, ""));
    const quantityBefore = await terminal.heldQuantity(TICKER);
    const cashBefore = await terminal.cashValue();

    const fill = await terminal.placeOrder(TICKER, 1, "sell");

    // --- cash rose by the proceeds -------------------------------------
    await expect.poll(() => terminal.cashValue()).toBeCloseTo(cashBefore + fill.total, 1);
    expect(await terminal.cashValue()).toBeGreaterThan(cashBefore);

    // --- the position shrank but survived ------------------------------
    await expect(terminal.positionRow(TICKER)).toBeVisible();
    await expect
      .poll(() => terminal.heldQuantity(TICKER))
      .toBeCloseTo(quantityBefore - 1, 6);

    // --- selling never alters the cost basis (PLAN.md §7) --------------
    const avgCostAfter = Number((await cells.nth(2).textContent())?.replace(/,/g, ""));
    expect(avgCostAfter).toBeCloseTo(avgCostBefore, 4);
  });

  test("selling the whole position deletes the row rather than zeroing it", async ({ page }) => {
    const terminal = new Terminal(page);
    await terminal.open();
    await terminal.waitForPrice(TICKER);

    // Start from whatever is held, then flatten it completely.
    if ((await terminal.positionRow(TICKER).count()) === 0) {
      await terminal.placeOrder(TICKER, 2, "buy");
      await expect(terminal.positionRow(TICKER)).toBeVisible();
    }

    const held = await terminal.heldQuantity(TICKER);
    const rowsBefore = await terminal.positionRows.count();
    const cashBefore = await terminal.cashValue();

    const fill = await terminal.placeOrder(TICKER, held, "sell");

    await expect(terminal.positionRow(TICKER)).toHaveCount(0);
    await expect(terminal.positionRows).toHaveCount(rowsBefore - 1);
    await expect(terminal.positionCount).toHaveText(String(rowsBefore - 1));
    await expect.poll(() => terminal.cashValue()).toBeCloseTo(cashBefore + fill.total, 1);

    // The order bar's "Held" readout goes with it.
    await expect(terminal.orderBar.getByText("Held", { exact: true })).toHaveCount(0);
  });

  test("selling more than is held is rejected and changes nothing", async ({ page }) => {
    const terminal = new Terminal(page);
    await terminal.open();
    await terminal.waitForPrice(TICKER);

    await terminal.placeOrder(TICKER, 1, "buy");
    await expect(terminal.positionRow(TICKER)).toBeVisible();
    const cashBefore = await terminal.cashValue();
    const heldBefore = await terminal.heldQuantity(TICKER);

    await page.locator("#trade-ticker").fill(TICKER);
    await page.locator("#trade-quantity").fill(String(heldBefore + 50));
    await expect(terminal.sellButton).toBeEnabled();
    await terminal.sellButton.click();

    // INSUFFICIENT_SHARES (409), rendered verbatim.
    await expect(terminal.orderError).toBeVisible();
    await expect(terminal.orderError).toHaveText(/^Insufficient shares: tried to sell /);

    expect(await terminal.cashValue()).toBeCloseTo(cashBefore, 2);
    expect(await terminal.heldQuantity(TICKER)).toBeCloseTo(heldBefore, 6);

    // Tidy up so later specs start from a known-empty book for this symbol.
    await terminal.placeOrder(TICKER, heldBefore, "sell");
    await expect(terminal.positionRow(TICKER)).toHaveCount(0);
  });

  test("selling a symbol that is not held is rejected", async ({ page }) => {
    const terminal = new Terminal(page);
    await terminal.open();

    const ticker = "JPM";
    await terminal.waitForPrice(ticker);
    // Guard rather than assume: an earlier spec may have left a position.
    test.skip((await terminal.positionRow(ticker).count()) > 0, `already holding ${ticker}`);

    await page.locator("#trade-ticker").fill(ticker);
    await page.locator("#trade-quantity").fill("1");
    await expect(terminal.sellButton).toBeEnabled();
    await terminal.sellButton.click();

    await expect(terminal.orderError).toBeVisible();
    await expect(terminal.orderError).toHaveText(/Insufficient shares/);
    await expect(terminal.positionRow(ticker)).toHaveCount(0);
  });
});
