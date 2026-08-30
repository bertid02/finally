"""FinAlly FastAPI application: routes, lifespan, and the static frontend.

Wiring, not logic. Three subsystems already exist and are complete -- `app/db`
(schema, transactions, every SQL statement), `app/market` (price cache, GBM
simulator, Massive poller, SSE router) -- and this module is the seam between
them plus the HTTP surface PLAN.md section 8 specifies.

Route order matters and is deliberate: the API routers register first, the static
files mount last at `/`, so a request for `/api/anything` can never be answered
by the frontend's index.html.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from app.api import health_router, portfolio_router, watchlist_router
from app.api.errors import register_error_handlers
from app.config import Settings, load_settings
from app.db import Database, Repository, set_database
from app.market import PriceCache, create_market_data_source, create_stream_router
from app.market.simulator import SimulatorDataSource

logger = logging.getLogger(__name__)


class SPAStaticFiles(StaticFiles):
    """Static files with an index.html fallback for unknown paths.

    The Next.js export is built with `trailingSlash: true`, so real routes are
    directories containing index.html and `html=True` finds them. Anything else --
    a deep link the export did not pre-render, a reload on a client-side route --
    falls back to index.html so the SPA can route it itself.

    `/api/*` is excluded from the fallback. Without that exclusion a typo'd
    endpoint would return 200 and a page of HTML, and the frontend's fetch would
    fail on `JSON.parse` rather than on a status code it can report.
    """

    async def get_response(self, path: str, scope: Scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or path.startswith("api/"):
                raise
            return await super().get_response("index.html", scope)


async def _start_market_data(app: FastAPI, tickers: list[str]) -> None:
    """Start the configured data source, falling back to the simulator if it can't run.

    `MarketDataSource.start()` raises RuntimeError when a source cannot produce
    data at all -- the common case being a Massive key on the free tier, where the
    snapshot endpoint is not in the plan. Falling back keeps the app usable, and
    `/api/health` reports `fallback: true` so the demo is not quietly claiming to
    be live when it is simulated.
    """
    cache: PriceCache = app.state.price_cache
    source = create_market_data_source(cache)
    try:
        await source.start(tickers)
    except RuntimeError as exc:
        logger.warning("Market source '%s' unavailable (%s) -- falling back to simulator", source.name, exc)
        await source.stop()
        source = SimulatorDataSource(price_cache=cache)
        await source.start(tickers)
    app.state.market_source = source
    logger.info("Market data running: %s, %d tickers", source.name, len(tickers))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the database, seed it if new, then start streaming its watchlist.

    The order is not arbitrary: the market source is seeded from the watchlist the
    repository returns, so a user who removed TSLA last session does not find it
    streaming again on restart.
    """
    repo: Repository = app.state.repository
    await repo.initialize()

    tickers = await repo.get_watchlist()
    await _start_market_data(app, tickers)

    try:
        yield
    finally:
        source = getattr(app.state, "market_source", None)
        if source is not None:
            await source.stop()
        app.state.database.close()
        logger.info("FinAlly shut down")


def create_app(settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    """Build the application.

    Both arguments exist for tests: a second app with its own in-memory database
    must not disturb the first. In production both are None and the environment
    decides -- `FINALLY_DB_PATH` for the database (resolved inside `Database`,
    not re-read here), `MASSIVE_API_KEY` for the data source.
    """
    settings = settings or load_settings()
    db = database or Database()
    # Publish it process-wide so `Repository()` and `get_database()` -- which the
    # chat layer constructs without an explicit db -- reach the same connection.
    set_database(db)

    app = FastAPI(
        title="FinAlly",
        description="AI Trading Workstation",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.state.settings = settings
    app.state.database = db
    app.state.repository = Repository(db=db)
    app.state.price_cache = PriceCache()
    app.state.market_source = None

    register_error_handlers(app)

    app.include_router(health_router)
    app.include_router(portfolio_router)
    app.include_router(watchlist_router)
    # The SSE router is built by the market module against our cache. Do not
    # reimplement it -- it already handles versioning, keepalives and disconnects.
    app.include_router(create_stream_router(app.state.price_cache))

    _mount_frontend(app, settings)
    return app


def _mount_frontend(app: FastAPI, settings: Settings) -> None:
    """Serve the Next.js export at `/`, or explain its absence.

    Mounted last, after every router, so `/api/*` is resolved before the catch-all.
    A missing directory is normal -- `uv run uvicorn app.main:app` in a checkout
    with no frontend build -- and must not crash startup, so the mount is skipped
    and `/` returns a short JSON note instead.
    """
    if settings.has_static:
        app.mount("/", SPAStaticFiles(directory=str(settings.static_dir), html=True), name="static")
        logger.info("Serving frontend from %s", settings.static_dir)
        return

    logger.warning("No frontend build at %s -- serving API only", settings.static_dir)

    @app.get("/", include_in_schema=False)
    async def _no_frontend() -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "message": "FinAlly API is running. No frontend build is mounted.",
                "docs": "/docs",
                "static_dir": str(settings.static_dir),
            }
        )


app = create_app()
