"""Watchlist endpoints -- membership only.

Prices never appear here. PLAN.md section 8 (S6, adopted) splits the two so there
is one source of price truth: REST owns membership, SSE owns prices. The
corollary is that all three endpoints return the **complete new list**, because
the stream carries no membership event and this return value is the frontend's
only refresh signal.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.db import MAX_WATCHLIST_SIZE, WatchlistFullError

from .deps import MarketSourceDep, RepositoryDep
from .errors import UnsupportedTickerError, validate_ticker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


class WatchlistRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ticker: str


@router.get("")
async def get_watchlist(repo: RepositoryDep) -> dict:
    """Current membership, in insertion order."""
    return {"tickers": await repo.get_watchlist()}


@router.post("")
async def add_ticker(
    body: WatchlistRequest,
    repo: RepositoryDep,
    source: MarketSourceDep,
) -> dict:
    """Add a ticker. Idempotent; returns the full new list.

    The order of these checks is the contract (TEAM_LOG.md, db-engineer):

      1. format            -> INVALID_TICKER (400)
      2. already present   -> 200, list unchanged. Not an error: a double-click
                              must not surface as one.
      3. list at capacity  -> WATCHLIST_FULL (409). Checked *before* the support
                              lookup so a full list fails fast rather than paying
                              a network round trip to Massive to say no anyway.
      4. supports_ticker   -> UNSUPPORTED_TICKER (422). Without this a typo like
                              APPL streams an invented price under the simulator
                              and sits permanently priceless under Massive.
      5. insert, then tell the source to start tracking it.
    """
    ticker = validate_ticker(body.ticker)
    current = await repo.get_watchlist()

    if ticker in current:
        return {"tickers": current}

    if len(current) >= MAX_WATCHLIST_SIZE:
        # The repository's own exception, so the message the user reads is
        # identical whichever layer noticed the ceiling.
        raise WatchlistFullError(
            f"Watchlist is full: {MAX_WATCHLIST_SIZE} tickers is the maximum. "
            "Remove one before adding another."
        )

    if not await source.supports_ticker(ticker):
        raise UnsupportedTickerError(
            f"'{ticker}' is not available from the {source.name} data source."
        )

    tickers = await repo.add_to_watchlist(ticker)
    # After the insert, never before: if add_ticker() succeeded but the insert had
    # failed, the source would stream a ticker the watchlist does not contain.
    await source.add_ticker(ticker)
    logger.info("Watchlist: added %s", ticker)
    return {"tickers": tickers}


@router.delete("/{ticker}")
async def remove_ticker(
    ticker: str,
    repo: RepositoryDep,
    source: MarketSourceDep,
) -> dict:
    """Remove a ticker. Idempotent; returns the full new list.

    `source.remove_ticker` also evicts the symbol from the price cache -- the
    repository deliberately does not touch the cache -- so the next SSE tick stops
    carrying a ticker the user has dropped.
    """
    symbol = validate_ticker(ticker)
    tickers = await repo.remove_from_watchlist(symbol)
    await source.remove_ticker(symbol)
    logger.info("Watchlist: removed %s", symbol)
    return {"tickers": tickers}
