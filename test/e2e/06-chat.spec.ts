import { expect, test } from "@playwright/test";

import { Terminal } from "../fixtures/terminal";

/**
 * PLAN.md §12: "AI chat (mocked): send a message, receive a response, trade
 * execution appears inline."
 *
 * Every expectation below is pinned to `backend/app/llm/mock.py`, which is the
 * published `LLM_MOCK` contract. Matching there is case-insensitive substring,
 * **first rule wins**, in this order:
 *
 *   1. "malformed"        -> not JSON at all; the parse-failure path
 *   2. "unavailable"      -> raises, as a provider outage would
 *   3. "yolo" / "all in"  -> buy 100000 NVDA -> fails INSUFFICIENT_CASH
 *   4. "unwatch"/"remove" -> watchlist remove (ticker from the message, else NFLX)
 *   5. "watch" / "add"    -> watchlist add (ticker from the message, else PYPL)
 *   6. "sell"             -> sell (ticker else AAPL, quantity else 1)
 *   7. "buy"              -> buy (ticker else AAPL, quantity else 1)
 *   -  anything else      -> conversational reply, no actions
 *
 * The ordering is a trap worth naming: "add NVDA to my watchlist and sell AAPL"
 * is a watchlist change, not a sale. Every prompt below was checked against the
 * whole ladder, not just the rule it means to hit.
 *
 * NOTE (assumption): as of writing, `## Contract: LLM_MOCK mapping` had not yet
 * been appended to `planning/TEAM_LOG.md`. These expectations are taken from the
 * module docstring and the code in `app/llm/mock.py`, which the docstring names
 * as the source the log entry must stay in step with. If the published entry
 * disagrees, the log wins and these specs need updating.
 */
test.describe("AI assistant", () => {
  test("a conversational message gets a reply and takes no action", async ({ page }) => {
    const terminal = new Terminal(page);
    await terminal.open();

    // Checked against the ladder: no "malformed", "unavailable", "yolo",
    // "all in", "unwatch", "remove", "watch", "add", "sell" or "buy".
    const reply = await terminal.sendChat("How concentrated is my portfolio?");

    // The user's turn is echoed above it.
    await expect(terminal.assistant.getByText("How concentrated is my portfolio?")).toBeVisible();
    await expect(reply).toContainText("Mock mode is on, so this is a canned reply.");
    await expect(terminal.tradeChips).toHaveCount(0);
    await expect(terminal.watchlistChips).toHaveCount(0);

    // The pending indicator is gone once the turn lands.
    await expect(terminal.assistant.getByText("Thinking", { exact: true })).toHaveCount(0);
    await expect(page.locator("#chat-input")).toBeEnabled();
  });

  test("a trade the assistant places executes and appears inline as a chip", async ({ page }) => {
    const terminal = new Terminal(page);
    await terminal.open();
    await terminal.waitForPrice("TSLA");

    const cashBefore = await terminal.cashValue();
    const heldBefore = (await terminal.positionRow("TSLA").count())
      ? await terminal.heldQuantity("TSLA")
      : 0;

    const reply = await terminal.sendChat("Buy 3 TSLA");
    await expect(reply).toContainText("Buying 3 TSLA at the market.");

    // --- the chip ------------------------------------------------------
    await expect(terminal.tradeChips).toHaveCount(1);
    const chip = terminal.tradeChips.first();
    await expect(chip).toHaveText(/^Bought 3 TSLA at [\d,]+\.\d{2} · \$[\d,]+\.\d{2}$/);

    // --- the trade really happened --------------------------------------
    // Auto-execution reuses the manual path, so this is the proof that
    // `actions.trades` is not merely cosmetic.
    await expect(terminal.positionRow("TSLA")).toBeVisible();
    await expect.poll(() => terminal.heldQuantity("TSLA")).toBeCloseTo(heldBefore + 3, 6);
    await expect.poll(() => terminal.cashValue()).toBeLessThan(cashBefore);

    // The chip's own numbers reconcile with the cash that left the account.
    const text = (await chip.textContent()) ?? "";
    const total = Number((text.match(/\$([\d,]+\.\d{2})$/)?.[1] ?? "").replace(/,/g, ""));
    expect(total).toBeGreaterThan(0);
    await expect.poll(() => terminal.cashValue()).toBeCloseTo(cashBefore - total, 1);
  });

  test("a trade the account cannot afford renders as a failed chip, verbatim", async ({ page }) => {
    const terminal = new Terminal(page);
    await terminal.open();
    await terminal.waitForPrice("NVDA");

    const cashBefore = await terminal.cashValue();
    const positionsBefore = await terminal.positionRows.count();
    const reply = await terminal.sendChat("yolo");

    await expect(reply).toContainText("Going all in on NVDA");
    await expect(terminal.tradeChips).toHaveCount(1);

    const chip = terminal.tradeChips.first();
    // A failed chip prints no price and no total — PLAN.md §7's failed entry
    // carries `error_code` and `error` instead.
    await expect(chip).toContainText("Buy rejected 100000 NVDA");
    // The same words the order bar shows for the same failure: one error
    // vocabulary through both doors.
    await expect(chip).toContainText(/Insufficient cash: need \$[\d,]+\.\d{2}, have \$/);

    // Nothing moved.
    expect(await terminal.cashValue()).toBeCloseTo(cashBefore, 2);
    await expect(terminal.positionRows).toHaveCount(positionsBefore);
  });

  test("a watchlist change the assistant makes reaches the watchlist panel", async ({ page }) => {
    // This is the contract TEAM.md flags as expensive to get wrong: the SSE
    // stream carries no membership, so the *only* way PYPL can appear in the
    // panel is the `watchlist` echo on the chat response.
    const terminal = new Terminal(page);
    await terminal.open();

    const before = await terminal.watchlistTickers();
    expect(before).not.toContain("PYPL");

    const reply = await terminal.sendChat("Add PYPL to my watchlist");
    await expect(reply).toContainText("Added PYPL to your watchlist");

    await expect(terminal.watchlistChips).toHaveCount(1);
    await expect(terminal.watchlistChips.first()).toHaveText("Added PYPL to the watchlist");

    await expect(terminal.price("PYPL")).toBeVisible();
    await expect(terminal.watchlistRows).toHaveCount(before.length + 1);
    await terminal.waitForPrice("PYPL");

    // And back out again through the same door.
    const removal = await terminal.sendChat("unwatch PYPL");
    await expect(removal).toContainText("Removed PYPL from your watchlist");
    await expect(terminal.watchlistChips).toHaveCount(2);
    await expect(terminal.watchlistChips.last()).toHaveText("Removed PYPL from the watchlist");
    await expect(terminal.price("PYPL")).toHaveCount(0);
    expect(await terminal.watchlistTickers()).toEqual(before);
  });

  test("a provider outage degrades to prose rather than an error", async ({ page }) => {
    // PLAN.md §9 via `app/llm/service.py`: nothing in the chat turn returns
    // non-200, because a 500 in a chat box is a dead end for the user.
    const terminal = new Terminal(page);
    await terminal.open();

    const reply = await terminal.sendChat("Is the AI service unavailable right now?");
    await expect(reply).toContainText("I couldn't reach the AI service just now");
    await expect(terminal.tradeChips).toHaveCount(0);
    await expect(terminal.watchlistChips).toHaveCount(0);
    await expect(page.locator("#chat-input")).toBeEnabled();
  });

  test("an unparseable model response degrades to prose rather than an error", async ({ page }) => {
    const terminal = new Terminal(page);
    await terminal.open();

    const reply = await terminal.sendChat("Return something malformed please");
    await expect(reply).toContainText("I got a garbled response from the AI service");
    await expect(terminal.tradeChips).toHaveCount(0);
    await expect(terminal.watchlistChips).toHaveCount(0);
  });

  test("every turn returns all five contract fields", async ({ page }) => {
    // Team-lead's ruling in TEAM_LOG.md. The three state echoes are not
    // decoration: the assistant can change the watchlist mid-turn and the SSE
    // stream carries no membership, so a turn that omitted them would cost the
    // frontend a round trip on every message.
    const terminal = new Terminal(page);
    await terminal.open();
    await terminal.sendChat("How concentrated is my portfolio?");

    const response = await page.request.post("/api/chat", {
      data: { message: "And what is my cash position?" },
    });
    expect(response.status()).toBe(200);
    const body = (await response.json()) as Record<string, unknown>;

    // All five fields, every turn — team-lead's ruling in TEAM_LOG.md.
    expect(typeof body.message).toBe("string");
    expect(body.actions).toEqual({ trades: [], watchlist_changes: [] });
    expect(Array.isArray(body.watchlist)).toBeTruthy();
    expect(typeof body.cash_balance).toBe("number");
    expect(Array.isArray(body.positions)).toBeTruthy();
  });

  test("an empty message is the endpoint's only non-200", async ({ request }) => {
    const response = await request.post("/api/chat", { data: { message: "   " } });
    expect(response.status()).toBe(400);
    const body = (await response.json()) as { error: { code: string; message: string } };
    expect(body.error.code).toBe("INVALID_MESSAGE");
    expect(body.error.message).toBeTruthy();
  });
});
