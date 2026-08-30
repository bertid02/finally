"""Portfolio endpoints: holdings, trade execution, and the P&L curve.

The interesting line in this module is `cache.get_price(...)`. PLAN.md section 8
is explicit that a trade fills at the *cached* price at the moment of execution
and never at a price supplied by the client -- so the request body carries no
price field at all, and there is nothing for a client to lie about.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from .deps import PriceCacheDep, RepositoryDep
from .errors import validate_ticker
from .valuation import prices_from_cache

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


class TradeRequest(BaseModel):
    """PLAN.md section 8: `{"ticker": "AAPL", "quantity": 10, "side": "buy"}`.

    The fields are typed loosely on purpose. Range and vocabulary checks belong to
    the repository, which raises INVALID_QUANTITY / INVALID_SIDE / INVALID_TICKER
    with the user-facing message the chat panel also renders; validating here as
    well would create a second set of words for the same failure.
    """

    model_config = ConfigDict(extra="ignore")

    ticker: str
    quantity: float
    side: str


@router.get("")
async def get_portfolio(repo: RepositoryDep) -> dict:
    """Positions and cash only -- no server-computed valuation.

    PLAN.md section 10 makes the client authoritative for displayed numbers: the
    header updates live from the SSE stream between fetches, so a total returned
    here would be stale the moment it arrived. `app/api/valuation.py` is where the
    server does the same arithmetic for the LLM's context.
    """
    return (await repo.get_portfolio()).to_dict()


@router.post("/trade")
async def execute_trade(
    body: TradeRequest,
    repo: RepositoryDep,
    cache: PriceCacheDep,
) -> dict:
    """Execute a market order. Instant fill, no fees, no confirmation.

    Validates the symbol format first so the cache lookup uses the normalized key
    -- `get_price("aapl")` would otherwise miss and report UNKNOWN_TICKER for a
    ticker that is streaming perfectly well.

    Passing `None` through when the cache is empty is deliberate: the repository
    turns it into UNKNOWN_TICKER (404). That is reachable in normal operation for
    a ticker added seconds ago under Massive's 15s poll.
    """
    ticker = validate_ticker(body.ticker)
    result = await repo.execute_trade(
        ticker=ticker,
        side=body.side,
        quantity=body.quantity,
        price=cache.get_price(ticker),
        market_prices=prices_from_cache(cache),
    )
    return result.to_dict()


@router.get("/history")
async def get_history(
    repo: RepositoryDep,
    since: str | None = Query(default=None, description="Inclusive ISO-8601 lower bound"),
    limit: int = Query(default=500, description="Most recent N points; clamped to 1-5000"),
) -> dict:
    """Portfolio value snapshots for the P&L chart, oldest-first.

    Sparse by design -- section 7 writes a snapshot on trade execution only, so the
    frontend joins this series with the live stream to draw the current segment.

    `limit` is passed through unclamped: the repository clamps to [1, 5000], and
    rejecting an out-of-range value here would turn a harmless query string into an
    error the chart has to handle.
    """
    snapshots = await repo.get_portfolio_history(since=since, limit=limit)
    return {"snapshots": [s.to_dict() for s in snapshots]}
