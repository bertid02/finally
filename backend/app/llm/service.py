"""One chat turn, end to end.

The sequence PLAN.md section 9 specifies, in order:

    persist the user's message -> read the transcript back -> build live portfolio
    context -> call the model -> parse -> auto-execute -> persist the assistant
    turn with its action outcomes -> return the message plus the state echoes.

Two rules shape everything below.

**Auto-execution reuses the manual path.** A trade the model asked for goes
through `validate_ticker` and `Repository.execute_trade` -- the same two calls
`POST /api/portfolio/trade` makes, filling at the same cached price with the same
exceptions. There is no second trade implementation and no second error
vocabulary; the only difference is that a failure here is caught and written into
`chat_messages.actions` instead of becoming an HTTP status.

**A failed action is a result, not an exception.** It is persisted with
`status: "failed"` and the message copied verbatim from the exception, rendered
by the frontend as a red chip, and fed back to the model on the next turn by
`prompt.render_history`. Nothing is swallowed.

Nothing in here returns non-200. A malformed response, a missing `message`, a
provider outage -- each degrades to prose in the chat panel, because a 500 in a
chat box is a dead end for the user while an apology is not.
"""

from __future__ import annotations

import logging

from app.api.errors import APIError, UnsupportedTickerError, validate_ticker
from app.api.valuation import build_valuation, prices_from_cache
from app.db import MAX_WATCHLIST_SIZE, DatabaseError, Repository, WatchlistFullError
from app.market import MarketDataSource, PriceCache

from .client import LLMUnavailableError, complete
from .mock import MockUnavailableError, mock_completion
from .prompt import build_context, build_messages
from .schema import LLMResponse, LLMResponseError, TradeSpec, WatchlistChangeSpec, parse_response

logger = logging.getLogger(__name__)

# How much transcript the model sees. Enough for a multi-turn negotiation about a
# trade; short enough that the prompt does not grow without bound in a long demo.
HISTORY_LIMIT = 20

UNAVAILABLE_MESSAGE = (
    "I couldn't reach the AI service just now, so I haven't changed anything. "
    "Please try again in a moment."
)
UNPARSEABLE_MESSAGE = (
    "I got a garbled response from the AI service and haven't changed anything. "
    "Please try asking again."
)


class EmptyMessageError(APIError):
    """The request carried no message. The only non-200 this endpoint can produce.

    Not a section 8 code -- section 8 covers trading and watchlist failures -- but
    it wears the same envelope, so the frontend handles it like any other error.
    """

    code = "INVALID_MESSAGE"
    http_status = 400


async def _generate(user_message: str, messages: list[dict], llm_mock: bool) -> str:
    """The provider call, or its deterministic stand-in.

    The mock is chosen by `Settings.llm_mock`, read once at app construction --
    not by re-reading the environment here, so one object decides.
    """
    if llm_mock:
        try:
            return mock_completion(user_message)
        except MockUnavailableError as exc:
            raise LLMUnavailableError(str(exc)) from exc
    return await complete(messages)


class InvalidWatchlistActionError(APIError):
    """The model asked for a watchlist action that is neither add nor remove."""

    code = "INVALID_ACTION"
    http_status = 400


async def _execute_trade(spec: TradeSpec, repo: Repository, cache: PriceCache) -> dict:
    """Run one model-requested trade down the manual path, recording either outcome.

    `cache.get_price` returning None is passed straight through: the repository
    turns it into UNKNOWN_TICKER, which is the honest answer for a ticker added
    seconds ago that has not ticked yet.
    """
    try:
        ticker = validate_ticker(spec.ticker)
        result = await repo.execute_trade(
            ticker=ticker,
            side=spec.side,
            quantity=spec.quantity,
            price=cache.get_price(ticker),
            market_prices=prices_from_cache(cache),
        )
        logger.info("Chat trade: %s %s %s", result.trade.side, result.trade.quantity, ticker)
        return {
            "ticker": ticker,
            "side": result.trade.side,
            "quantity": result.trade.quantity,
            "status": "executed",
            "price": result.trade.price,
            "total": result.trade.total,
        }
    except DatabaseError as exc:
        logger.info("Chat trade rejected (%s): %s", exc.code, exc.message)
        return {
            "ticker": spec.ticker,
            "side": spec.side,
            "quantity": spec.quantity,
            "status": "failed",
            "error_code": exc.code,
            "error": exc.message,
        }


async def _apply_watchlist_change(
    spec: WatchlistChangeSpec,
    repo: Repository,
    source: MarketDataSource,
) -> dict:
    """Add or remove one ticker, mirroring `app/api/watchlist.py` check for check.

    The order there is the contract: format -> already present -> WATCHLIST_FULL ->
    supports_ticker -> insert -> tell the source. Reproducing it rather than
    calling the route keeps a rejection out of the HTTP layer, where it would
    abort the whole chat turn.
    """
    action = (spec.action or "").strip().lower()
    entry = {"ticker": spec.ticker, "action": action or spec.action}
    try:
        ticker = validate_ticker(spec.ticker)
        entry["ticker"] = ticker

        if action == "add":
            current = await repo.get_watchlist()
            if ticker not in current:
                if len(current) >= MAX_WATCHLIST_SIZE:
                    raise WatchlistFullError(
                        f"Watchlist is full: {MAX_WATCHLIST_SIZE} tickers is the maximum. "
                        "Remove one before adding another."
                    )
                if not await source.supports_ticker(ticker):
                    raise UnsupportedTickerError(
                        f"'{ticker}' is not available from the {source.name} data source."
                    )
                await repo.add_to_watchlist(ticker)
                await source.add_ticker(ticker)
        elif action == "remove":
            await repo.remove_from_watchlist(ticker)
            await source.remove_ticker(ticker)
        else:
            raise InvalidWatchlistActionError(
                f"'{spec.action}' is not a watchlist action. Expected 'add' or 'remove'."
            )

        logger.info("Chat watchlist: %s %s", action, entry["ticker"])
        return {**entry, "status": "executed"}
    except (DatabaseError, APIError) as exc:
        logger.info("Chat watchlist rejected (%s): %s", exc.code, exc.message)
        return {**entry, "status": "failed", "error_code": exc.code, "error": exc.message}


async def _execute(
    parsed: LLMResponse,
    repo: Repository,
    cache: PriceCache,
    source: MarketDataSource,
) -> dict:
    """Run every requested action, trades first.

    Trades lead because they are the ones that can fail on money and are what the
    user is waiting on. Both lists always appear in the result, empty or not: the
    frontend renders `actions.trades` without checking whether the key exists.
    """
    return {
        "trades": [await _execute_trade(t, repo, cache) for t in parsed.trades],
        "watchlist_changes": [
            await _apply_watchlist_change(c, repo, source) for c in parsed.watchlist_changes
        ],
    }


def _has_actions(actions: dict) -> bool:
    return bool(actions["trades"] or actions["watchlist_changes"])


async def run_chat_turn(
    *,
    message: str,
    repo: Repository,
    cache: PriceCache,
    source: MarketDataSource,
    llm_mock: bool,
) -> dict:
    """Handle one user message and return the `POST /api/chat` body.

    The response carries all five fields every time (team-lead's ruling in
    TEAM_LOG.md): the prose, the action outcomes, and the three state echoes.
    The echoes are read *after* execution, so a turn that traded or changed the
    watchlist returns the state that resulted -- which matters because the SSE
    stream carries no membership and the frontend would otherwise need a second
    round trip to notice.
    """
    user_message = (message or "").strip()
    if not user_message:
        raise EmptyMessageError("Message cannot be empty.")

    # Persist first, then read the transcript back, so the messages sent to the
    # model and the messages in the database are one list rather than two that
    # can disagree. The user's turn is the last entry.
    await repo.add_chat_message("user", user_message)
    history = await repo.get_chat_messages(limit=HISTORY_LIMIT)

    valuation = await build_valuation(repo, cache)
    context = build_context(valuation, await repo.get_realized_pnl(), await repo.get_watchlist(), cache)
    messages = build_messages(context, history)

    actions = {"trades": [], "watchlist_changes": []}
    try:
        parsed = parse_response(await _generate(user_message, messages, llm_mock))
        reply = parsed.message
        actions = await _execute(parsed, repo, cache, source)
    except LLMUnavailableError:
        reply = UNAVAILABLE_MESSAGE
    except LLMResponseError as exc:
        logger.warning("Unparseable LLM response: %s", exc)
        reply = UNPARSEABLE_MESSAGE

    # `actions=None` for a turn that did nothing, per the ChatMessage contract --
    # the response body still carries the empty arrays the frontend expects.
    await repo.add_chat_message("assistant", reply, actions if _has_actions(actions) else None)

    portfolio = await repo.get_portfolio()
    return {
        "message": reply,
        "actions": actions,
        "watchlist": await repo.get_watchlist(),
        "cash_balance": portfolio.cash_balance,
        "positions": [p.to_dict() for p in portfolio.positions],
    }
