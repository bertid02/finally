"""The system prompt and the context the model sees each turn.

Three things go in front of the model: who it is (`SYSTEM_PROMPT`), what the
portfolio looks like *right now* (`build_context`), and what was said before
(`render_history`).

The context is rebuilt from the repository and the price cache on every turn
rather than carried in the conversation, because prices move between messages and
a stale number in the history would be indistinguishable from a current one.

History rendering is where failed actions come back. PLAN.md section 9 requires
that a trade the model asked for and the system refused is fed back, so the model
can tell the user what happened instead of repeating the request. That is what
`_render_actions` is for -- without it, "buy 100 NVDA" that failed on cash looks,
from the model's side of the conversation, exactly like one that succeeded.
"""

from __future__ import annotations

import json

from app.api.valuation import PortfolioValuation
from app.db import ChatMessage
from app.market import PriceCache

SYSTEM_PROMPT = """You are FinAlly, an AI trading assistant embedded in a simulated \
trading workstation. The user trades a virtual portfolio that started with $10,000 in \
cash. There is no real money at stake.

What you do:
- Analyze portfolio composition, concentration risk, and P&L using the CURRENT PORTFOLIO \
block below. Those numbers are live; never guess or recall them from earlier turns.
- Suggest trades with brief, concrete reasoning.
- Execute trades when the user asks or agrees, by putting them in the `trades` array. \
They execute immediately at the current market price -- there is no confirmation step, so \
only include a trade the user actually wants.
- Manage the watchlist with the `watchlist_changes` array.

How to answer:
- Be concise and data-driven. A few sentences, not an essay. No markdown tables.
- Market orders only. Quantities are shares and may be fractional. You never set the \
price; fills happen at the live price.
- `message` must always be present and must stand on its own: say what you did or what \
you recommend, including the reasoning, because the action chips show only the numbers.
- If an earlier action failed, the failure and its reason appear in the conversation \
history. Acknowledge it and adapt -- do not silently retry the same order.
- Never claim a trade happened unless you put it in `trades` this turn.

Respond only with JSON matching the required schema."""


def build_context(
    valuation: PortfolioValuation,
    realized_pnl: float,
    watchlist: list[str],
    cache: PriceCache,
) -> dict:
    """The live portfolio snapshot handed to the model each turn.

    Unrealized P&L comes from the valuation helper (`app/api/valuation.py`) --
    the one server-side implementation of the formula. Realized P&L is not in the
    valuation because it lives in the trade log, so the caller reads it separately
    and passes it in.
    """
    return {
        "cash_balance": valuation.cash_balance,
        "total_value": valuation.total_value,
        "positions_value": valuation.positions_value,
        "total_unrealized_pnl": valuation.total_unrealized_pnl,
        "total_unrealized_pnl_percent": valuation.total_unrealized_pnl_percent,
        "realized_pnl": round(realized_pnl, 2),
        "positions": [
            {
                "ticker": p.ticker,
                "quantity": p.quantity,
                "avg_cost": p.avg_cost,
                "price": p.price,
                "market_value": p.market_value,
                "unrealized_pnl": p.unrealized_pnl,
                "unrealized_pnl_percent": p.unrealized_pnl_percent,
                "weight_percent": p.weight,
                # False means the cache had no price and this row is valued at
                # cost. Telling the model is cheaper than letting it read a
                # flat P&L as a market observation.
                "priced": p.priced,
            }
            for p in valuation.positions
        ],
        "watchlist": [_watch_entry(ticker, cache) for ticker in watchlist],
    }


def _watch_entry(ticker: str, cache: PriceCache) -> dict:
    update = cache.get(ticker)
    if update is None:
        # Normal for a ticker added seconds ago under Massive's 15s poll.
        return {"ticker": ticker, "price": None, "change_percent_today": None}
    return {
        "ticker": ticker,
        "price": update.price,
        # The session figure, never `change_percent` -- that one is tick-over-tick
        # noise and would read to the model as a market that never moves.
        "change_percent_today": update.change_percent_session,
    }


def _render_actions(actions: dict | None) -> str:
    """Flatten a stored `chat_messages.actions` blob into one line of prose."""
    if not actions:
        return ""
    parts: list[str] = []
    for trade in actions.get("trades") or []:
        if trade.get("status") == "executed":
            parts.append(
                f"EXECUTED {trade.get('side')} {trade.get('quantity')} "
                f"{trade.get('ticker')} at {trade.get('price')}"
            )
        else:
            parts.append(
                f"FAILED {trade.get('side')} {trade.get('quantity')} "
                f"{trade.get('ticker')} ({trade.get('error_code')}): {trade.get('error')}"
            )
    for change in actions.get("watchlist_changes") or []:
        if change.get("status") == "executed":
            parts.append(f"EXECUTED watchlist {change.get('action')} {change.get('ticker')}")
        else:
            parts.append(
                f"FAILED watchlist {change.get('action')} {change.get('ticker')} "
                f"({change.get('error_code')}): {change.get('error')}"
            )
    return "\n[system: " + "; ".join(parts) + "]" if parts else ""


def render_history(messages: list[ChatMessage]) -> list[dict]:
    """Stored turns as chat-completion messages, oldest-first.

    Assistant turns carry their action outcomes appended to the content, so the
    model sees what actually happened rather than only what it asked for.
    """
    rendered: list[dict] = []
    for message in messages:
        content = message.content
        if message.role == "assistant":
            content = f"{content}{_render_actions(message.actions)}"
        rendered.append({"role": message.role, "content": content})
    return rendered


def build_messages(context: dict, history: list[ChatMessage]) -> list[dict]:
    """The full message list for one turn.

    The context rides in the system message rather than as a separate turn, so it
    cannot be mistaken for something the user said, and it sits ahead of the
    conversation where the model weighs it as instruction.

    `history` must already end with the user's new message -- the caller persists
    it first and reads it back, so the transcript sent to the model and the
    transcript in the database are the same object, not two things that can drift.
    """
    system = (
        f"{SYSTEM_PROMPT}\n\nCURRENT PORTFOLIO (live, as of this message):\n"
        f"{json.dumps(context, indent=2)}"
    )
    return [{"role": "system", "content": system}, *render_history(history)]
