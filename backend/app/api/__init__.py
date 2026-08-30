"""HTTP layer: routers, the shared error envelope, and the valuation helper.

Public surface for other agents:

    from app.api.errors import APIError, UnsupportedTickerError, validate_ticker, envelope
    from app.api.valuation import build_valuation, value_portfolio, prices_from_cache
    from app.api.deps import RepositoryDep, PriceCacheDep, MarketSourceDep
"""

from .chat import router as chat_router
from .health import router as health_router
from .portfolio import router as portfolio_router
from .watchlist import router as watchlist_router

__all__ = ["chat_router", "health_router", "portfolio_router", "watchlist_router"]
