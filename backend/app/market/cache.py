"""Thread-safe in-memory price cache."""

from __future__ import annotations

import time
from threading import Lock

from .models import PriceUpdate


class PriceCache:
    """Thread-safe in-memory store of the latest PriceUpdate per ticker.

    Writer: exactly one data source. Readers: SSE stream, trade execution,
    portfolio valuation, LLM context builder.

    Thread-safety is required, not decorative: the Massive poller runs its
    blocking REST call in a worker thread via asyncio.to_thread, so writes
    genuinely arrive off the event loop.
    """

    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self._session_opens: dict[str, float] = {}
        self._lock = Lock()
        self._version: int = 0  # Monotonically increasing; bumped on every write

    def update(
        self,
        ticker: str,
        price: float,
        timestamp: float | None = None,
        session_open: float | None = None,
        change_percent_session: float | None = None,
    ) -> PriceUpdate:
        """Record a new price for a ticker. Returns the created PriceUpdate.

        Computes direction and change from the previous price. If this is the
        first update for the ticker, previous_price == price (direction 'flat').

        session_open is captured on the FIRST update for a ticker and preserved
        thereafter, so the daily-change denominator never drifts mid-session. A
        source that knows the true anchor (Massive: prev_day.close) passes it; the
        simulator passes its seed price; a newly added ticker with neither anchors
        at its first observed price, per PLAN.md section 6.
        """
        with self._lock:
            ts = timestamp if timestamp is not None else time.time()
            prev = self._prices.get(ticker)
            previous_price = prev.price if prev else price

            if ticker not in self._session_opens:
                self._session_opens[ticker] = session_open if session_open else price
            anchor = self._session_opens[ticker]

            update = PriceUpdate(
                ticker=ticker,
                price=round(price, 2),
                previous_price=round(previous_price, 2),
                session_open=round(anchor, 2),
                timestamp=ts,
                provider_change_percent=change_percent_session,
            )
            self._prices[ticker] = update
            self._version += 1
            return update

    def get(self, ticker: str) -> PriceUpdate | None:
        """Get the latest price for a single ticker, or None if unknown."""
        with self._lock:
            return self._prices.get(ticker)

    def get_all(self) -> dict[str, PriceUpdate]:
        """Snapshot of all current prices. Shallow copy — safe to iterate."""
        with self._lock:
            return dict(self._prices)

    def get_price(self, ticker: str) -> float | None:
        """Just the float. This is what trade execution fills against."""
        update = self.get(ticker)
        return update.price if update else None

    def remove(self, ticker: str) -> None:
        """Evict on watchlist removal.

        Drops the session anchor too, so a re-added ticker re-anchors rather than
        resurrecting a stale denominator. Bumps the version: without it, removing
        the last ticker leaves the SSE stream believing nothing changed and the
        browser keeps showing a ticker the backend has forgotten.
        """
        with self._lock:
            self._prices.pop(ticker, None)
            self._session_opens.pop(ticker, None)
            self._version += 1

    @property
    def version(self) -> int:
        """Current version counter. Bumped on every write; drives SSE change detection."""
        with self._lock:
            return self._version

    def __len__(self) -> int:
        with self._lock:
            return len(self._prices)

    def __contains__(self, ticker: str) -> bool:
        with self._lock:
            return ticker in self._prices
