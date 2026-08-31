import { expect, type Locator, type Page } from "@playwright/test";

/**
 * Page object for the FinAlly console.
 *
 * Every selector here was read out of `frontend/src/` rather than guessed. Where
 * the frontend exposes a `data-testid` this uses it; everywhere else it uses the
 * accessible name the component actually renders, because those are the two
 * things a redesign is least likely to break silently.
 *
 * The frontend's testids, in full:
 *   price-<TICKER>    the watchlist row's last price      WatchlistRow.tsx
 *   session-<TICKER>  the watchlist row's day %           WatchlistRow.tsx
 *   trade-chip        an AI trade confirmation chip       ActionChip.tsx
 *   watchlist-chip    an AI watchlist confirmation chip   ActionChip.tsx
 *
 * There are no others, so the panels are found by their headings. `Panel.tsx`
 * renders each panel as a bare `<section>` with an `<h2>` title — an unnamed
 * `<section>` has no `region` role, so `getByRole('region')` finds nothing and
 * filtering `section` by its heading is the correct approach, not a workaround.
 */

/** The seed watchlist, in insertion order — `backend/app/db/schema.py`. */
export const DEFAULT_WATCHLIST = [
  "AAPL",
  "GOOGL",
  "MSFT",
  "AMZN",
  "TSLA",
  "NVDA",
  "META",
  "JPM",
  "V",
  "NFLX",
] as const;

/** The seeded starting balance — PLAN.md §7. */
export const STARTING_CASH = 10_000;

/**
 * Parse any figure `frontend/src/lib/format.ts` produces.
 *
 * Note the minus sign: `formatPercent` and `formatSignedMoney` emit U+2212 MINUS
 * SIGN, not ASCII hyphen. A parser that only strips `-` silently returns a
 * positive number for every loss, which is exactly the kind of bug that makes a
 * P&L assertion pass while the UI shows the opposite.
 */
export function parseFigure(text: string | null): number {
  if (text === null) throw new Error("no text to parse");
  const trimmed = text.trim();
  if (trimmed === "—") return NaN;
  const negative = trimmed.includes("−") || trimmed.startsWith("-");
  const digits = trimmed.replace(/[^0-9.]/g, "");
  if (digits === "") throw new Error(`not a figure: ${JSON.stringify(text)}`);
  const value = Number(digits);
  return negative ? -value : value;
}

export class Terminal {
  constructor(readonly page: Page) {}

  // --- panels ---------------------------------------------------------

  /** A console panel, located by the `<h2>` title `Panel.tsx` renders. */
  panel(title: string): Locator {
    return this.page
      .locator("section")
      .filter({ has: this.page.getByRole("heading", { name: title, exact: true }) });
  }

  get watchlist(): Locator {
    return this.panel("Watchlist");
  }

  get positions(): Locator {
    return this.panel("Positions");
  }

  get allocation(): Locator {
    return this.panel("Allocation");
  }

  get equityCurve(): Locator {
    return this.panel("Equity Curve");
  }

  get assistant(): Locator {
    return this.panel("Assistant");
  }

  /**
   * The detail chart. Its heading is the *selected ticker* (or "No selection"),
   * so it cannot be found by a fixed title — it is identified instead by the
   * "Since page load" action label, which only this panel renders.
   */
  get detailChart(): Locator {
    return this.page
      .locator("section")
      .filter({ has: this.page.getByText("Since page load", { exact: true }) });
  }

  // --- header ---------------------------------------------------------

  /**
   * One of the header's readouts. `Header.tsx` renders each as a direct child
   * `<div>` of `<header>` holding a label span and a `.num` value span, so the
   * concatenated text is e.g. "Cash$10,000.00" — hence the anchored regex.
   */
  private headerReadout(label: string, valuePrefix: string): Locator {
    return this.page
      .locator("header > div")
      .filter({ hasText: new RegExp(`^${label}${valuePrefix}`) })
      .locator("span.num")
      .first();
  }

  get netLiquidation(): Locator {
    return this.headerReadout("Net Liquidation", "\\s*\\$");
  }

  get cash(): Locator {
    return this.headerReadout("Cash", "\\s*\\$");
  }

  get unrealized(): Locator {
    return this.headerReadout("Unrealized", "\\s*[+−]\\$");
  }

  get positionCount(): Locator {
    return this.headerReadout("Positions", "\\s*\\d");
  }

  /** The connection dot's label: "Live", "Reconnecting" or "Offline". */
  get connectionStatus(): Locator {
    return this.page.locator("header").getByRole("status");
  }

  async cashValue(): Promise<number> {
    return parseFigure(await this.cash.textContent());
  }

  async netLiquidationValue(): Promise<number> {
    return parseFigure(await this.netLiquidation.textContent());
  }

  // --- watchlist ------------------------------------------------------

  price(ticker: string): Locator {
    return this.page.getByTestId(`price-${ticker}`);
  }

  dayPercent(ticker: string): Locator {
    return this.page.getByTestId(`session-${ticker}`);
  }

  /** Every rendered watchlist row, identified by its price cell. */
  get watchlistRows(): Locator {
    return this.watchlist.locator('[data-testid^="price-"]');
  }

  async watchlistTickers(): Promise<string[]> {
    const ids = await this.watchlistRows.evaluateAll((nodes) =>
      nodes.map((node) => (node as HTMLElement).dataset.testid ?? ""),
    );
    return ids.map((id) => id.replace(/^price-/, ""));
  }

  async priceValue(ticker: string): Promise<number> {
    return parseFigure(await this.price(ticker).textContent());
  }

  async addTicker(ticker: string): Promise<void> {
    await this.watchlist.getByLabel("Add ticker").fill(ticker);
    await this.watchlist.getByRole("button", { name: "Add", exact: true }).click();
  }

  /**
   * Remove a ticker from the watchlist.
   *
   * The × button is `hidden … group-hover:flex`, so it is genuinely not visible
   * until the row is hovered — Playwright's actionability check would time out on
   * a bare click. The row is the price cell's parent element.
   */
  async removeTicker(ticker: string): Promise<void> {
    await this.price(ticker).locator("..").hover();
    await this.page
      .getByRole("button", { name: `Remove ${ticker} from watchlist`, exact: true })
      .click();
  }

  /** Click a symbol in the watchlist to load it into the detail chart. */
  async selectTicker(ticker: string): Promise<void> {
    await this.watchlist.getByRole("button", { name: ticker, exact: true }).click();
  }

  // --- order bar ------------------------------------------------------

  /** `TradeBar.tsx` is a `<form>`; the page has three, so identify it by its input. */
  get orderBar(): Locator {
    return this.page.locator("form").filter({ has: this.page.locator("#trade-ticker") });
  }

  get buyButton(): Locator {
    return this.orderBar.getByRole("button", { name: "Buy", exact: true });
  }

  get sellButton(): Locator {
    return this.orderBar.getByRole("button", { name: "Sell", exact: true });
  }

  /** The order bar's transient confirmation. Auto-dismisses after 5s. */
  get orderNotice(): Locator {
    return this.orderBar.getByRole("status");
  }

  /** The order bar's transient error. Same span, `role="alert"` when it failed. */
  get orderError(): Locator {
    return this.orderBar.getByRole("alert");
  }

  /**
   * Place a market order and return the fill the server reports.
   *
   * The fill price is *not* knowable in advance: `POST /api/portfolio/trade`
   * carries no price and fills at the server's cached price at execution time,
   * which has moved since whatever the UI last painted. Every cash assertion in
   * this suite is derived from the value returned here, never from a price
   * scraped before the click.
   *
   * The fill is read from the trade response, not from the notice. The notice
   * element is reused between orders and lingers for five seconds, and every
   * notice matches the same shape — "Bought 4 AMZN at 185.02" is a perfectly
   * valid confirmation of the *previous* order — so polling the DOM for a
   * confirmation resolves instantly against stale text whenever a second order
   * follows quickly. That raced: a sell placed straight after a buy read the
   * buy's notice. Waiting on `POST /api/portfolio/trade` cannot be stale, and it
   * carries the server's exact figures rather than the display's two decimals.
   *
   * The notice is still asserted afterwards, pinned to the price the server
   * actually filled at, so a fill the UI fails to confirm is still a failure.
   */
  async placeOrder(
    ticker: string,
    quantity: number,
    side: "buy" | "sell",
  ): Promise<{ price: number; quantity: number; total: number }> {
    await this.page.locator("#trade-ticker").fill(ticker);
    await this.page.locator("#trade-quantity").fill(String(quantity));

    const button = side === "buy" ? this.buyButton : this.sellButton;
    // Both buttons stay disabled until a price for the symbol has arrived over
    // the stream — TradeBar.tsx guards on `Boolean(update)`.
    await expect(button).toBeEnabled();

    const settled = this.page.waitForResponse(
      (r) => r.url().includes("/api/portfolio/trade") && r.request().method() === "POST",
    );
    await button.click();
    const response = await settled;
    const body = await response.json();
    if (!response.ok()) {
      throw new Error(
        `order rejected: ${body?.error?.code ?? response.status()} ${body?.error?.message ?? ""}`.trim(),
      );
    }
    const trade = body?.trade;
    if (!trade) {
      throw new Error(`trade response carried no fill: ${JSON.stringify(body)}`);
    }
    expect(trade.side).toBe(side);
    expect(trade.ticker).toBe(ticker);

    // The UI must confirm the fill the server reported — same price, to the two
    // decimals `format.ts` prints, so this cannot be satisfied by a lingering
    // notice from an earlier order at a different price.
    const shown = Number(trade.price).toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    await expect(this.orderNotice).toHaveText(
      new RegExp(
        `^${side === "buy" ? "Bought" : "Sold"}\\s+[\\d.,]+\\s+${ticker}\\s+at\\s+${shown.replace(".", "\\.")}$`,
      ),
    );

    const price = Number(trade.price);
    const filled = Number(trade.quantity);
    return { price, quantity: filled, total: Number(trade.total ?? price * filled) };
  }

  // --- positions table ------------------------------------------------

  get positionRows(): Locator {
    return this.positions.locator("tbody tr");
  }

  /**
   * One row of the positions table.
   *
   * Matched on the symbol cell by exact accessible name, not `hasText`: the seed
   * watchlist contains `V`, and a substring filter would select every row whose
   * ticker, quantity or price happens to contain the letter.
   */
  positionRow(ticker: string): Locator {
    return this.positionRows.filter({
      has: this.page.getByRole("cell", { name: ticker, exact: true }),
    });
  }

  async heldQuantity(ticker: string): Promise<number> {
    const cells = this.positionRow(ticker).locator("td");
    return parseFigure(await cells.nth(1).textContent());
  }

  // --- assistant ------------------------------------------------------

  /**
   * Send one chat message and wait for the reply.
   *
   * Waiting on the "Thinking" indicator to clear is not enough on its own — in
   * mock mode the turn can complete between two polls — so this waits for the
   * assistant bubble count to grow instead.
   */
  async sendChat(message: string): Promise<Locator> {
    const before = await this.assistantMessages.count();
    await this.page.locator("#chat-input").fill(message);
    await this.assistant.getByRole("button", { name: "Send", exact: true }).click();
    await expect(this.assistantMessages).toHaveCount(before + 1);
    return this.assistantMessages.last();
  }

  /**
   * The assistant's turns.
   *
   * `ChatBubble.tsx` renders exactly one `<span class="label">FinAlly</span>` per
   * assistant turn, and the bubble plus any action chips are its siblings inside
   * one flex column — so the label is what identifies a turn and its parent is
   * what carries the content. Filtering the column by class instead would also
   * match the panel body, which is itself `flex flex-col` and contains the word.
   */
  get assistantMessages(): Locator {
    return this.assistant.locator("span.label").filter({ hasText: /^FinAlly$/ }).locator("..");
  }

  get tradeChips(): Locator {
    return this.assistant.getByTestId("trade-chip");
  }

  get watchlistChips(): Locator {
    return this.assistant.getByTestId("watchlist-chip");
  }

  // --- lifecycle ------------------------------------------------------

  /**
   * Open the console and wait until it is genuinely live: the stream connected,
   * the watchlist loaded, and a real price painted for the lead symbol.
   *
   * "A price painted" matters. `formatPrice(undefined)` renders an em dash, so a
   * row can exist for a second before it means anything, and a spec that asserts
   * on the em dash tests nothing.
   */
  async open(): Promise<void> {
    await this.page.goto("/");
    await expect(this.connectionStatus).toHaveText("Live");
    await expect(this.watchlistRows.first()).toBeVisible();
    await this.waitForPrice(DEFAULT_WATCHLIST[0]);
  }

  /** Wait until a ticker's row shows a number rather than the em-dash placeholder. */
  async waitForPrice(ticker: string): Promise<void> {
    await expect(this.price(ticker)).toHaveText(/^[\d,]+\.\d{2}$/);
  }

  /**
   * Wait for a *new* tick to be painted for a ticker.
   *
   * The simulator moves prices every ~500ms, but two consecutive ticks can round
   * to the same two decimals, so this polls for a change rather than sampling
   * twice. The stream is silent whenever the cache version does not advance —
   * silence is not a disconnect (TEAM.md) — so the generous timeout is deliberate.
   */
  async waitForTick(ticker: string): Promise<void> {
    const cell = this.price(ticker);
    const start = (await cell.textContent()) ?? "";
    await expect
      .poll(async () => (await cell.textContent()) ?? "", { timeout: 20_000, intervals: [250] })
      .not.toBe(start);
  }
}
