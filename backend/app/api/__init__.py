"""HTTP layer: routers, the shared error envelope, and the valuation helper.

Public surface for other agents:

    from app.api.errors import APIError, UnsupportedTickerError, validate_ticker, envelope
    from app.api.valuation import build_valuation, value_portfolio, prices_from_cache
    from app.api.deps import RepositoryDep, PriceCacheDep, MarketSourceDep

`chat_router` is deliberately **not** re-exported here, and importing `.chat`
below would be a bug rather than a tidy-up. `app/llm/` imports this package's
`valuation` and `errors` helpers, while `app/api/chat.py` imports `app.llm` to
run a turn -- a genuine cycle between the two packages. Anything imported at this
package's top level is executed before `app.api.valuation` can resolve, so
listing `.chat` here makes `import app.llm` fail outright whenever it is the
first of the two to be imported (a script, a worker, a test that touches only the
LLM layer). Nothing catches that: the app and the test suite both happen to
import `app.api` first, so every route works and coverage stays at 100%.

`app/main.py` imports the chat router from its own module for this reason.
"""

from .health import router as health_router
from .portfolio import router as portfolio_router
from .watchlist import router as watchlist_router

__all__ = ["health_router", "portfolio_router", "watchlist_router"]
