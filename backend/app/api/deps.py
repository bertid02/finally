"""Request-scoped access to the three long-lived objects the app owns.

They live on `app.state`, created once in `create_app`/`lifespan`, because all
three are process-wide by nature: one SQLite connection, one price cache, one
background poller. Routes reach them through these dependencies rather than
module globals, so a test can build a second app with its own database and the
two do not fight over the same state.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.db import Repository
from app.market import MarketDataSource, PriceCache


def get_repository(request: Request) -> Repository:
    """The repository bound to this app's database."""
    return request.app.state.repository


def get_price_cache(request: Request) -> PriceCache:
    """The in-memory price cache the market source writes and the SSE stream reads."""
    return request.app.state.price_cache


def get_market_source(request: Request) -> MarketDataSource:
    """The running data source -- simulator or Massive, after any startup fallback."""
    return request.app.state.market_source


RepositoryDep = Annotated[Repository, Depends(get_repository)]
PriceCacheDep = Annotated[PriceCache, Depends(get_price_cache)]
MarketSourceDep = Annotated[MarketDataSource, Depends(get_market_source)]
