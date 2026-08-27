"""Abstract interface for market data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod


class MarketDataSource(ABC):
    """Contract for market data providers.

    Implementations run a background task that writes PriceUpdates into a shared
    PriceCache on their own schedule. Downstream code NEVER calls a data source to
    read a price — it reads the cache. The source is a writer, not a service.

    Lifecycle:
        source = create_market_data_source(cache)
        await source.start(["AAPL", "GOOGL"])       # once, at app startup
        ...
        if await source.supports_ticker("PYPL"):    # before a watchlist insert
            await source.add_ticker("PYPL")
        await source.remove_ticker("GOOGL")
        ...
        await source.stop()                          # at app shutdown
    """

    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Begin producing updates. Call exactly once.

        Must populate the cache for every supported ticker BEFORE returning, so a
        client connecting immediately after startup sees prices rather than an
        empty grid.

        Raises RuntimeError if the source cannot produce data at all (bad API key,
        endpoint not in plan). The caller may then fall back to another source.
        Raises RuntimeError if called a second time — a second background task
        would keep writing to the cache after stop() cancels the handle it knows.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Stop the background task and release resources. Idempotent.

        After stop(), the source must not write to the cache again.
        """

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Track a new ticker. No-op if already tracked.

        Normalizes the symbol (trim, uppercase) so both sources behave alike.
        Should populate the cache promptly. The simulator can do so synchronously;
        Massive cannot, and the frontend must tolerate a priceless row until the
        next poll (PLAN.md section 8: disable the trade button until a price arrives).
        """

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Stop tracking a ticker and evict it from the cache. No-op if absent."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Currently tracked tickers. Synchronous — reads local state only."""

    @abstractmethod
    async def supports_ticker(self, ticker: str) -> bool:
        """Can this source produce prices for this symbol?

        Called by POST /api/watchlist before inserting. Without it, a typo like
        'APPL' silently streams an invented price under the simulator and sits
        permanently priceless under Massive.

        Must not raise: return False on any error. PLAN.md maps False to a 422
        UNSUPPORTED_TICKER, which is explicable to the user; an unhandled exception
        is a 500, which is not.
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable source identity, e.g. 'simulator' or 'massive'.

        Surfaced at /api/health and in the UI, so a demo that silently fell back to
        the simulator is visibly doing so rather than quietly lying about being live.
        """


# Shared by both implementations so a symbol means the same thing either side of
# the interface. PLAN.md section 8 uses the same rule for INVALID_TICKER.
TICKER_PATTERN = r"[A-Z]{1,5}"


def normalize_ticker(ticker: str) -> str:
    """Trim and uppercase a symbol. The one place normalization happens."""
    return ticker.strip().upper()
