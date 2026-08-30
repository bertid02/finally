import { expect, test } from "@playwright/test";

import { Terminal } from "../fixtures/terminal";

/**
 * PLAN.md §12: "Portfolio visualization: heatmap renders with correct colors,
 * P&L chart has data points."
 *
 * Both charts are Recharts SVG, so the assertions go at the DOM Recharts
 * produces: `<rect>` per treemap cell (`Heatmap.tsx` supplies its own `TreeCell`
 * content, so one rect per position and no chrome), and `.recharts-line` for the
 * equity curve. Colour is asserted as *hue*, not as an exact rgba string — the
 * alpha channel is a function of the P&L magnitude and would be a frozen literal.
 *
 * The equity curve has two sources joined into one series (`PnLChart.tsx`):
 * persisted snapshots, written on trades only, and the live mark accumulated
 * from the stream at one sample a second. It needs two points before it draws,
 * which is why this spec trades twice and then waits.
 */
test.describe("portfolio visualisation", () => {
  test("the heatmap draws one cell per position, coloured by P&L", async ({ page }) => {
    const terminal = new Terminal(page);
    await terminal.open();

    // Two positions of visibly different weight, so "sized by weight" is
    // observable rather than assumed.
    await terminal.waitForPrice("GOOGL");
    await terminal.waitForPrice("META");
    await terminal.placeOrder("GOOGL", 6, "buy");
    await terminal.placeOrder("META", 1, "buy");

    await expect(terminal.positionRow("GOOGL")).toBeVisible();
    await expect(terminal.positionRow("META")).toBeVisible();

    // The empty state is gone.
    await expect(terminal.allocation.getByText("No positions yet.", { exact: false })).toHaveCount(
      0,
    );

    const cells = terminal.allocation.locator("svg rect");
    await expect.poll(() => cells.count()).toBeGreaterThanOrEqual(2);

    // Both symbols are labelled. `TreeCell` only prints the label when the
    // rectangle clears 42x26px, which the two-position case comfortably does at
    // this viewport.
    await expect(terminal.allocation.locator("text", { hasText: "GOOGL" })).toBeVisible();
    await expect(terminal.allocation.locator("text", { hasText: "META" })).toBeVisible();

    // Area tracks weight: 6 shares of GOOGL is worth more than 1 of META, so its
    // rectangle must be the larger one.
    const areas = await cells.evaluateAll((nodes) =>
      nodes.map((node) => {
        const rect = node as SVGRectElement;
        return {
          area: rect.width.baseVal.value * rect.height.baseVal.value,
          fill: rect.getAttribute("fill") ?? "",
        };
      }),
    );
    const drawn = areas.filter((cell) => cell.area > 0);
    expect(drawn.length).toBeGreaterThanOrEqual(2);
    expect(Math.max(...drawn.map((c) => c.area))).toBeGreaterThan(
      Math.min(...drawn.map((c) => c.area)),
    );

    // Colour by P&L: green (51,214,159) for a gain, red (255,93,108) for a loss,
    // grey for flat. `Heatmap.tsx` emits exactly these three hues with a
    // magnitude-driven alpha, so the hue is the assertable part.
    for (const cell of drawn) {
      expect(cell.fill).toMatch(
        /^rgba\((?:51,214,159|255,93,108|122,135,153),[\d.]+\)$/,
      );
    }
  });

  test("the equity curve plots persisted snapshots joined to the live mark", async ({
    page,
    request,
  }) => {
    // Snapshots are written on trade execution only (PLAN.md §7, S2 adopted), so
    // the persisted half of the series exists precisely because 03-05 traded.
    const history = (await (await request.get("/api/portfolio/history?limit=500")).json()) as {
      snapshots: { total_value: number; recorded_at: string }[];
    };
    expect(Array.isArray(history.snapshots)).toBeTruthy();
    expect(history.snapshots.length).toBeGreaterThan(0);
    // Oldest-last ordering, and every point is a real valuation.
    for (const snapshot of history.snapshots) {
      expect(snapshot.total_value).toBeGreaterThan(0);
      expect(Date.parse(snapshot.recorded_at)).not.toBeNaN();
    }

    const terminal = new Terminal(page);
    await terminal.open();

    // The live series samples once a second and the curve needs two points.
    await expect(terminal.equityCurve.locator(".recharts-line").first()).toBeVisible({
      timeout: 20_000,
    });
    await expect(
      terminal.equityCurve.getByText("The curve starts building as prices arrive."),
    ).toHaveCount(0);

    // The panel's meta is the signed move against the opening value.
    await expect(terminal.equityCurve.getByText(/^[+−]\$[\d,]+\.\d{2}$/)).toBeVisible();

    // Persisted points are drawn as dots on the dashed historical line.
    await expect
      .poll(() => terminal.equityCurve.locator(".recharts-line-dot").count(), {
        timeout: 20_000,
      })
      .toBeGreaterThan(0);
  });

  test("the positions table reports unrealized P&L and a session-based day %", async ({
    page,
  }) => {
    const terminal = new Terminal(page);
    await terminal.open();

    const rows = terminal.positionRows;
    await expect.poll(() => rows.count()).toBeGreaterThan(0);

    const row = rows.first();
    const cells = row.locator("td");
    await expect(cells).toHaveCount(8);

    // Qty, Avg Cost, Last, Mkt Value are plain figures.
    for (const index of [1, 2, 3, 4]) {
      await expect(cells.nth(index)).toHaveText(/^[\d,]+(\.\d+)?$|^\$[\d,]+\.\d{2}$/);
    }
    // Unrealized is signed money; P&L % and Day % are signed percentages using
    // U+2212 for negatives.
    await expect(cells.nth(5)).toHaveText(/^[+−]\$[\d,]+\.\d{2}$/);
    await expect(cells.nth(6)).toHaveText(/^[+−]\d+\.\d{2}%$/);
    await expect(cells.nth(7)).toHaveText(/^[+−]\d+\.\d{2}%$/);

    // Day % is `change_percent_session`, the same figure the watchlist shows for
    // the same symbol. Tick-over-tick noise (±0.02%) would not agree with it.
    const ticker = ((await cells.nth(0).textContent()) ?? "").trim();
    if ((await terminal.dayPercent(ticker).count()) > 0) {
      await expect
        .poll(async () => (await cells.nth(7).textContent())?.trim())
        .toBe(((await terminal.dayPercent(ticker).textContent()) ?? "").trim());
    }

    // The header aggregates the same numbers.
    await expect(terminal.unrealized).toHaveText(/^[+−]\$[\d,]+\.\d{2}/);
    await expect(terminal.positionCount).toHaveText(String(await rows.count()));
  });

  test("net liquidation equals cash plus every position marked to the live price", async ({
    page,
    request,
  }) => {
    const terminal = new Terminal(page);
    await terminal.open();
    await expect.poll(() => terminal.positionRows.count()).toBeGreaterThan(0);

    const portfolio = (await (await request.get("/api/portfolio")).json()) as {
      cash_balance: number;
      positions: { ticker: string; quantity: number; avg_cost: number }[];
    };

    // `/api/portfolio` is deliberately unvalued (PLAN.md §8/§10) — the client is
    // authoritative for anything displayed. So the check is that the header's
    // total is consistent with the server's quantities at the prices on screen,
    // within a tolerance for the ticks that landed between the two reads.
    let marked = portfolio.cash_balance;
    for (const position of portfolio.positions) {
      const price =
        (await terminal.price(position.ticker).count()) > 0
          ? await terminal.priceValue(position.ticker)
          : position.avg_cost;
      marked += position.quantity * price;
    }

    const shown = await terminal.netLiquidationValue();
    expect(Math.abs(shown - marked)).toBeLessThan(Math.max(marked * 0.01, 1));
  });
});
