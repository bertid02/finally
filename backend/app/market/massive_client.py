"""Massive (Polygon.io) API client for real market data."""

from __future__ import annotations

import asyncio
import logging
import time

from massive import RESTClient
from massive.rest.models import SnapshotMarketType

from .cache import PriceCache
from .interface import MarketDataSource, normalize_ticker

logger = logging.getLogger(__name__)

NS_PER_SEC = 1_000_000_000
MAX_TIMESTAMP_SKEW = 7 * 24 * 3600  # 7 days


def resolve_price(snap) -> float | None:
    """Best available current price from a TickerSnapshot, freshest first.

    Ordered by staleness, not preference:
      last_trade     - real-time, Developer/Advanced/Business only
      min.close      - up to 60s stale, available on Starter+
      day.close      - today's running close; 0/None before the open
      prev_day.close - always present once the ticker has ever traded

    The truthiness checks (`if ... .price`, not `is not None`) are deliberate:
    Massive returns 0 — not null — for bar fields on a ticker that has not traded
    yet today. 0 is not a tradeable price, and 0.0 is falsy, so one check handles
    both cases.
    """
    if snap.last_trade is not None and snap.last_trade.price:
        return snap.last_trade.price
    if snap.min is not None and snap.min.close:
        return snap.min.close
    if snap.day is not None and snap.day.close:
        return snap.day.close
    if snap.prev_day is not None and snap.prev_day.close:
        return snap.prev_day.close
    return None


def resolve_session_change(snap) -> tuple[float | None, float | None]:
    """(session_open_anchor, change_percent_session) — prefer the provider's numbers.

    The anchor is the previous day's close, not day.open: day.open is 0 before the
    market opens, and Massive's own todaysChangePerc is computed against the
    previous close, so anchoring anywhere else makes our number disagree with the
    provider's on the same screen.

    todays_change_percent is split- and dividend-adjusted by Massive. A locally
    computed (price - prev_close) / prev_close is not, so it prints a spurious
    -50% on the morning of a 2-for-1 split.
    """
    anchor = snap.prev_day.close if snap.prev_day and snap.prev_day.close else None
    if snap.todays_change_percent is not None:
        return anchor, snap.todays_change_percent  # authoritative, split-adjusted
    return anchor, None  # caller derives from anchor


class MassiveDataSource(MarketDataSource):
    """MarketDataSource backed by the Massive (Polygon.io) REST API.

    Polls GET /v2/snapshot/locale/us/markets/stocks/tickers for all watched
    tickers in a single API call, then writes results to the PriceCache.

    Rate limits:
      - Basic (free): no snapshot access at all — start() raises so the caller
        can fall back to the simulator
      - Starter / Developer (15-min delayed): poll every 15s (default)
      - Advanced / Business (real-time): poll every 2s
    """

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float = 15.0,
    ) -> None:
        self._api_key = api_key
        self._cache = price_cache
        self._interval = poll_interval
        self._tickers: list[str] = []
        self._task: asyncio.Task | None = None
        self._client: RESTClient | None = None
        self._fatal = False  # 401/403 latch — stop retrying what will never work

    @property
    def name(self) -> str:
        return "massive"

    async def start(self, tickers: list[str]) -> None:
        if self._task is not None:
            raise RuntimeError("MassiveDataSource.start() called twice")

        self._client = RESTClient(api_key=self._api_key)
        self._tickers = [normalize_ticker(t) for t in tickers]

        # Fail fast, before the app claims to be live.
        await self._poll_once()
        if self._fatal:
            self._close_client()
            raise RuntimeError(
                "Massive is unavailable for this API key: the snapshot endpoint "
                "requires a Starter plan or above. Unset MASSIVE_API_KEY to use "
                "the built-in simulator."
            )

        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info(
            "Massive poller started: %d tickers, %.1fs interval",
            len(self._tickers),
            self._interval,
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._close_client()
        logger.info("Massive poller stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if ticker not in self._tickers:
            self._tickers.append(ticker)
            logger.info("Massive: added ticker %s (will appear on next poll)", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        self._tickers = [t for t in self._tickers if t != ticker]
        self._cache.remove(ticker)
        logger.info("Massive: removed ticker %s", ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    async def supports_ticker(self, ticker: str) -> bool:
        """True if Massive returns a snapshot for this symbol.

        Costs one extra API call, on a user-initiated action only (watchlist add),
        so it does not compete with the polling budget in any meaningful way.

        Returns False on any error: PLAN.md maps False to a 422 UNSUPPORTED_TICKER,
        a recoverable and explicable outcome. Admitting a symbol we cannot price
        produces a permanently blank watchlist row instead.
        """
        ticker = normalize_ticker(ticker)
        if not self._client:
            return False
        try:
            snaps = await asyncio.to_thread(self._fetch_snapshots, [ticker])
        except Exception:
            logger.warning("supports_ticker(%s) failed; assuming unsupported", ticker)
            return False
        return any(getattr(s, "ticker", None) == ticker for s in snaps)

    # --- Internal ---

    def _close_client(self) -> None:
        """Release the underlying urllib3 connection pool, if the SDK exposes one."""
        client = self._client
        self._client = None
        if client is None:
            return
        closer = getattr(client, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:  # pragma: no cover - defensive
                logger.debug("Ignoring error closing Massive client", exc_info=True)

    async def _poll_loop(self) -> None:
        """Poll on interval. First poll already happened in start()."""
        while not self._fatal:
            await asyncio.sleep(self._interval)
            await self._poll_once()
        logger.error("Massive poller halted: unrecoverable error")

    async def _poll_once(self) -> None:
        """Execute one poll cycle: fetch snapshots, update cache.

        A failed poll leaves the previous prices in place — a network blip shows a
        briefly stale price rather than an empty terminal.
        """
        if not self._tickers or not self._client:
            return

        # Snapshot the list before handing it to a worker thread: add_ticker and
        # remove_ticker mutate it from the event loop.
        tickers = list(self._tickers)

        try:
            # The Massive RESTClient is synchronous — run in a thread to
            # avoid blocking the event loop.
            snapshots = await asyncio.to_thread(self._fetch_snapshots, tickers)
        except Exception as e:
            status = getattr(e, "status", None) or getattr(e, "code", None)
            if status in (401, 403):
                self._fatal = True
                logger.error(
                    "Massive rejected the API key (HTTP %s). Snapshots require a "
                    "Starter plan or above.",
                    status,
                )
            else:
                logger.error("Massive poll failed: %s", e)
            return

        if not snapshots:
            # Snapshot data is cleared daily at ~3:30 AM ET and repopulates from
            # ~4:00 AM ET. A poll in that window legitimately returns nothing.
            logger.debug("Massive returned no snapshots (pre-market or unknown symbols)")
            return

        processed = 0
        for snap in snapshots:
            ticker = getattr(snap, "ticker", None)
            try:
                price = resolve_price(snap)
            except AttributeError as e:
                logger.warning("Unexpected snapshot shape for %s: %s", ticker, e)
                continue
            if price is None:
                logger.debug("No usable price for %s; leaving cache entry stale", ticker)
                continue
            anchor, pct = resolve_session_change(snap)
            self._cache.update(
                ticker=ticker,
                price=price,
                timestamp=self._safe_timestamp(getattr(snap, "updated", None)),
                session_open=anchor,
                change_percent_session=pct,
            )
            processed += 1

        logger.debug("Massive poll: updated %d/%d tickers", processed, len(tickers))

    @staticmethod
    def _safe_timestamp(raw_ns) -> float:
        """Nanoseconds -> seconds, with a sanity guard.

        TickerSnapshot.updated and LastTrade.sip_timestamp are NANOseconds; the
        Agg bars (day, prev_day) are milliseconds. Mixing them up is the single
        most likely bug in this module, and a wrong-by-a-millennium timestamp
        should degrade to "now" rather than poison the chart's x-axis.
        """
        now = time.time()
        if not raw_ns:
            return now
        ts = raw_ns / NS_PER_SEC
        return ts if abs(ts - now) < MAX_TIMESTAMP_SKEW else now

    def _fetch_snapshots(self, tickers: list[str]) -> list:
        """Synchronous call to the Massive REST API. Runs in a thread."""
        return self._client.get_snapshot_all(
            market_type=SnapshotMarketType.STOCKS,
            tickers=tickers,
        )
