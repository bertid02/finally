"""Domain exceptions for the database layer.

Every exception here carries the `code` and `message` that PLAN.md section 8
puts on the wire:

    {"error": {"code": "INSUFFICIENT_CASH", "message": "..."}}

The codes live *here*, not in the API layer, for one reason: the same failure has
to reach the user through two different doors. A manual trade returns it as an
HTTP error envelope; an LLM-initiated trade is caught mid-chat-turn and written
into `chat_messages.actions` as a failed entry. PLAN.md section 7 requires those
two to use the same vocabulary verbatim, and the only way to guarantee that is to
raise it once from the layer both paths call.

The `message` is user-facing prose, not a log line. It is rendered unmodified in
the chat panel.
"""

from __future__ import annotations


class DatabaseError(Exception):
    """Base for every failure the repository raises deliberately.

    Anything that is *not* a DatabaseError escaping the repository is a bug, and
    the API layer should let it become a 500 rather than dressing it up.
    """

    code: str = "DATABASE_ERROR"
    http_status: int = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def to_envelope(self) -> dict[str, dict[str, str]]:
        """The exact JSON body PLAN.md section 8 specifies for a non-2xx response."""
        return {"error": {"code": self.code, "message": self.message}}


class InvalidQuantityError(DatabaseError):
    """Quantity is zero, negative, NaN, or infinite."""

    code = "INVALID_QUANTITY"
    http_status = 400


class InvalidSideError(DatabaseError):
    """`side` is neither 'buy' nor 'sell'."""

    code = "INVALID_SIDE"
    http_status = 400


class InvalidTickerError(DatabaseError):
    """Symbol fails the ^[A-Z]{1,5}$ format rule."""

    code = "INVALID_TICKER"
    http_status = 400


class UnknownTickerError(DatabaseError):
    """No price in the cache for this symbol, so a fill price cannot be determined.

    Reachable in normal operation: under Massive's 15s poll a just-added ticker
    sits priceless until the next snapshot. PLAN.md section 8 tells the frontend to
    disable the trade button until a price arrives, but the LLM can still ask for
    the trade, so the repository has to refuse it rather than fill at zero.
    """

    code = "UNKNOWN_TICKER"
    http_status = 404


class InsufficientCashError(DatabaseError):
    """Buy cost exceeds the cash balance."""

    code = "INSUFFICIENT_CASH"
    http_status = 409


class InsufficientSharesError(DatabaseError):
    """Sell quantity exceeds the held quantity."""

    code = "INSUFFICIENT_SHARES"
    http_status = 409


class WatchlistFullError(DatabaseError):
    """The watchlist already holds MAX_WATCHLIST_SIZE tickers."""

    code = "WATCHLIST_FULL"
    http_status = 409
