"""Deterministic canned responses for `LLM_MOCK=true`.

This is a **test contract**, not a convenience. PLAN.md section 12's E2E suite
asserts that a trade chip appears inline in the chat panel and that a failed
action renders as an error chip, which is only writable if the mock's behaviour
is specified rather than discovered. The mapping is published in
`planning/TEAM_LOG.md` under "Contract: LLM_MOCK mapping"; keep the two in step.

Matching is case-insensitive substring, **first rule wins**, in the order below.
A rule that fires later in the list can therefore be shadowed -- "add NVDA to my
watchlist and sell AAPL" is a watchlist change, not a sale.

| # | Trigger                | Result                                                |
|---|------------------------|-------------------------------------------------------|
| 1 | `malformed`            | not JSON at all -- exercises the parse-failure path    |
| 2 | `unavailable`          | raises, as a provider outage would                    |
| 3 | `yolo` / `all in`      | buy 100000 NVDA -> fails INSUFFICIENT_CASH            |
| 4 | `unwatch` / `remove`   | watchlist remove (ticker from the message, else NFLX) |
| 5 | `watch` / `add`        | watchlist add (ticker from the message, else PYPL)    |
| 6 | `sell`                 | sell (ticker else AAPL, quantity else 1)              |
| 7 | `buy`                  | buy (ticker else AAPL, quantity else 1)               |
| - | anything else          | conversational reply, no actions                      |

Ticker and quantity are lifted from the message when present: the first bare
uppercase 1-5 letter token that is not a command word, and the first number.
So "buy 5 TSLA" buys 5 TSLA and "buy" buys 1 AAPL.

The responses are JSON *strings* on purpose -- the mock returns what the provider
would return, so mock and live traffic go through the same parser and the parser
is exercised in every E2E run.
"""

from __future__ import annotations

import json
import re

# Uppercase words that are English, not symbols. Without this "BUY 5 TSLA" would
# trade a company called BUY.
_NOT_TICKERS = {
    "A", "I", "ADD", "ALL", "AI", "AM", "AND", "ANY", "ARE", "BE", "BUY", "CAN", "DO",
    "FOR", "GET", "HOW", "IN", "IS", "IT", "ME", "MY", "NO", "NOT", "NOW", "OF", "OK",
    "ON", "OR", "OUT", "P", "PNL", "PUT", "SELL", "SO", "THE", "TO", "UP", "US", "WHAT",
    "WHY", "YES", "YOLO", "YOU",
}
_TICKER_RE = re.compile(r"\b[A-Z]{1,5}\b")
_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")

DEFAULT_BUY_TICKER = "AAPL"
DEFAULT_ADD_TICKER = "PYPL"
DEFAULT_REMOVE_TICKER = "NFLX"
FAILING_TICKER = "NVDA"
FAILING_QUANTITY = 100000


class MockUnavailableError(Exception):
    """The simulated provider outage behind trigger #2."""


def _ticker(message: str, default: str) -> str:
    for candidate in _TICKER_RE.findall(message):
        if candidate not in _NOT_TICKERS:
            return candidate
    return default


def _quantity(message: str, default: float = 1.0) -> float:
    match = _NUMBER_RE.search(message)
    return float(match.group()) if match else default


def _payload(message: str, trades: list[dict] | None = None,
             changes: list[dict] | None = None) -> str:
    return json.dumps(
        {
            "message": message,
            "trades": trades or [],
            "watchlist_changes": changes or [],
        }
    )


def mock_completion(user_message: str) -> str:
    """The canned provider response for one user message.

    Raises:
        MockUnavailableError -- for the `unavailable` trigger, so the outage path
        has E2E coverage without unplugging anything.
    """
    text = user_message.lower()

    if "malformed" in text:
        return "Sure! Here is your portfolio analysis, but not as JSON."

    if "unavailable" in text:
        raise MockUnavailableError("Mock provider is unavailable.")

    if "yolo" in text or "all in" in text:
        return _payload(
            f"Going all in on {FAILING_TICKER} -- placing the order now.",
            trades=[
                {"ticker": FAILING_TICKER, "side": "buy", "quantity": FAILING_QUANTITY}
            ],
        )

    if "unwatch" in text or "remove" in text:
        ticker = _ticker(user_message, DEFAULT_REMOVE_TICKER)
        return _payload(
            f"Removed {ticker} from your watchlist.",
            changes=[{"ticker": ticker, "action": "remove"}],
        )

    if "watch" in text or "add" in text:
        ticker = _ticker(user_message, DEFAULT_ADD_TICKER)
        return _payload(
            f"Added {ticker} to your watchlist so you can track it.",
            changes=[{"ticker": ticker, "action": "add"}],
        )

    if "sell" in text:
        ticker = _ticker(user_message, DEFAULT_BUY_TICKER)
        quantity = _quantity(user_message)
        return _payload(
            f"Selling {quantity:g} {ticker} at the market.",
            trades=[{"ticker": ticker, "side": "sell", "quantity": quantity}],
        )

    if "buy" in text:
        ticker = _ticker(user_message, DEFAULT_BUY_TICKER)
        quantity = _quantity(user_message)
        return _payload(
            f"Buying {quantity:g} {ticker} at the market.",
            trades=[{"ticker": ticker, "side": "buy", "quantity": quantity}],
        )

    return _payload(
        "Mock mode is on, so this is a canned reply. Your portfolio is loaded and "
        "I can buy, sell, or change the watchlist on request."
    )
