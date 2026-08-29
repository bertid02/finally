"""Tests for MassiveDataSource.

Fixtures are built from the REAL SDK dataclasses, not bare MagicMocks. A bare
MagicMock auto-creates any attribute read from it, which is exactly how
`snap.last_trade.timestamp` — a field that does not exist on LastTrade — passed
13 green tests while dropping every snapshot in production.
"""

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest
from massive.rest.models import (
    Agg,
    LastTrade,
    MinuteSnapshot,
    SnapshotMarketType,
    TickerSnapshot,
)

from app.market.cache import PriceCache
from app.market.massive_client import (
    NS_PER_SEC,
    MassiveDataSource,
    resolve_price,
    resolve_session_change,
)

NOW_NS = int(time.time() * NS_PER_SEC)


def _snapshot(
    ticker="AAPL",
    last_trade_price=190.50,
    min_close=None,
    day_close=None,
    prev_close=189.00,
    todays_change_percent=0.79,
    updated=None,
) -> TickerSnapshot:
    """A TickerSnapshot built from real SDK model classes."""
    return TickerSnapshot(
        ticker=ticker,
        last_trade=(
            LastTrade(ticker=ticker, price=last_trade_price, sip_timestamp=NOW_NS)
            if last_trade_price is not None
            else None
        ),
        min=MinuteSnapshot(close=min_close) if min_close is not None else None,
        day=Agg(close=day_close) if day_close is not None else None,
        prev_day=Agg(close=prev_close) if prev_close is not None else None,
        todays_change=1.5,
        todays_change_percent=todays_change_percent,
        updated=NOW_NS if updated is None else updated,
    )


def _source(cache=None, **kwargs) -> MassiveDataSource:
    source = MassiveDataSource(
        api_key="test-key",
        price_cache=cache if cache is not None else PriceCache(),
        **kwargs,
    )
    source._client = MagicMock()  # Satisfy the _poll_once guard
    return source


class TestResolvePrice:
    """The fallback chain. last_trade and last_quote are plan-gated."""

    def test_prefers_last_trade(self):
        assert resolve_price(_snapshot(last_trade_price=190.5, min_close=188.0)) == 190.5

    def test_falls_back_to_min_close(self):
        """Starter and Basic plans return last_trade=None — not an error."""
        snap = _snapshot(last_trade_price=None, min_close=188.0, day_close=187.0)
        assert resolve_price(snap) == 188.0

    def test_falls_back_to_day_close(self):
        snap = _snapshot(last_trade_price=None, min_close=None, day_close=187.0)
        assert resolve_price(snap) == 187.0

    def test_falls_back_to_prev_close(self):
        """Pre-market: day and min are absent or zero."""
        snap = _snapshot(last_trade_price=None, min_close=None, day_close=None)
        assert resolve_price(snap) == 189.0

    def test_zero_bars_are_skipped_not_returned(self):
        """Massive returns 0, not null, for a ticker that has not traded today."""
        snap = _snapshot(last_trade_price=None, min_close=0.0, day_close=0.0)
        assert resolve_price(snap) == 189.0

    def test_returns_none_when_nothing_is_usable(self):
        snap = _snapshot(last_trade_price=None, min_close=None, day_close=None, prev_close=None)
        assert resolve_price(snap) is None

    def test_zero_last_trade_price_falls_through(self):
        snap = _snapshot(last_trade_price=0.0, min_close=188.0)
        assert resolve_price(snap) == 188.0


class TestResolveSessionChange:
    def test_anchors_on_previous_close(self):
        anchor, pct = resolve_session_change(_snapshot(prev_close=189.0))
        assert anchor == 189.0

    def test_prefers_provider_percent(self):
        """todaysChangePerc is split- and dividend-adjusted; local arithmetic is not."""
        _, pct = resolve_session_change(_snapshot(todays_change_percent=0.79))
        assert pct == 0.79

    def test_none_percent_defers_to_derivation(self):
        _, pct = resolve_session_change(_snapshot(todays_change_percent=None))
        assert pct is None

    def test_missing_prev_day_yields_no_anchor(self):
        anchor, _ = resolve_session_change(_snapshot(prev_close=None))
        assert anchor is None


class TestSafeTimestamp:
    def test_converts_nanoseconds_not_milliseconds(self):
        """sip_timestamp and `updated` are NANOseconds. Dividing by 1e3 lands
        roughly 31,000 years in the future."""
        ts = MassiveDataSource._safe_timestamp(NOW_NS)
        assert abs(ts - time.time()) < 5

    def test_absurd_timestamp_degrades_to_now(self):
        """A wrong-by-a-millennium value must not poison the chart's x-axis."""
        ts = MassiveDataSource._safe_timestamp(NOW_NS * 1000)
        assert abs(ts - time.time()) < 5

    def test_stale_timestamp_degrades_to_now(self):
        ts = MassiveDataSource._safe_timestamp(NOW_NS - 30 * 24 * 3600 * NS_PER_SEC)
        assert abs(ts - time.time()) < 5

    def test_zero_and_none_degrade_to_now(self):
        assert abs(MassiveDataSource._safe_timestamp(0) - time.time()) < 5
        assert abs(MassiveDataSource._safe_timestamp(None) - time.time()) < 5


@pytest.mark.asyncio
class TestPolling:
    async def test_poll_updates_cache(self):
        cache = PriceCache()
        source = _source(cache, poll_interval=60.0)
        source._tickers = ["AAPL", "GOOGL"]

        snapshots = [_snapshot("AAPL", 190.50), _snapshot("GOOGL", 175.25)]
        with patch.object(source, "_fetch_snapshots", return_value=snapshots):
            await source._poll_once()

        assert cache.get_price("AAPL") == 190.50
        assert cache.get_price("GOOGL") == 175.25

    async def test_poll_populates_session_fields(self):
        cache = PriceCache()
        source = _source(cache, poll_interval=60.0)
        source._tickers = ["AAPL"]

        snapshot = _snapshot("AAPL", 190.50, prev_close=189.00, todays_change_percent=0.79)
        with patch.object(source, "_fetch_snapshots", return_value=[snapshot]):
            await source._poll_once()

        update = cache.get("AAPL")
        assert update.session_open == 189.00
        assert update.change_percent_session == 0.79

    async def test_starter_plan_snapshot_still_produces_a_price(self):
        """last_trade=None is the normal shape on Starter/Basic — not malformed
        data, and certainly not a reason to drop the ticker."""
        cache = PriceCache()
        source = _source(cache, poll_interval=60.0)
        source._tickers = ["AAPL"]

        snapshot = _snapshot("AAPL", last_trade_price=None, min_close=188.25)
        with patch.object(source, "_fetch_snapshots", return_value=[snapshot]):
            await source._poll_once()

        assert cache.get_price("AAPL") == 188.25

    async def test_timestamp_is_converted_from_nanoseconds(self):
        cache = PriceCache()
        source = _source(cache, poll_interval=60.0)
        source._tickers = ["AAPL"]

        with patch.object(source, "_fetch_snapshots", return_value=[_snapshot("AAPL")]):
            await source._poll_once()

        assert abs(cache.get("AAPL").timestamp - time.time()) < 5

    async def test_unexpected_snapshot_shape_is_skipped(self):
        """A shape the SDK never produces must not take down the whole poll."""
        cache = PriceCache()
        source = _source(cache, poll_interval=60.0)
        source._tickers = ["AAPL", "WEIRD"]

        class _Weird:
            ticker = "WEIRD"

            def __getattr__(self, item):
                raise AttributeError(item)

        with patch.object(
            source, "_fetch_snapshots", return_value=[_Weird(), _snapshot("AAPL", 190.5)]
        ):
            await source._poll_once()

        assert cache.get_price("AAPL") == 190.50
        assert cache.get_price("WEIRD") is None

    async def test_fetch_snapshots_calls_the_grouped_endpoint(self):
        """One call covers every watched ticker — the reason the budget works."""
        source = _source()
        source._client.get_snapshot_all.return_value = []

        source._fetch_snapshots(["AAPL", "GOOGL"])

        kwargs = source._client.get_snapshot_all.call_args.kwargs
        assert kwargs["tickers"] == ["AAPL", "GOOGL"]
        assert kwargs["market_type"] == SnapshotMarketType.STOCKS

    async def test_unpriceable_snapshot_leaves_cache_stale(self):
        cache = PriceCache()
        source = _source(cache, poll_interval=60.0)
        source._tickers = ["AAPL", "BAD"]

        good = _snapshot("AAPL", 190.50)
        bad = _snapshot(
            "BAD", last_trade_price=None, min_close=None, day_close=None, prev_close=None
        )
        with patch.object(source, "_fetch_snapshots", return_value=[good, bad]):
            await source._poll_once()

        assert cache.get_price("AAPL") == 190.50
        assert cache.get_price("BAD") is None

    async def test_api_error_does_not_crash_or_clear_the_cache(self):
        """A network blip shows a briefly stale price, not an empty terminal."""
        cache = PriceCache()
        source = _source(cache, poll_interval=60.0)
        source._tickers = ["AAPL"]

        with patch.object(source, "_fetch_snapshots", return_value=[_snapshot("AAPL", 190.5)]):
            await source._poll_once()

        with patch.object(source, "_fetch_snapshots", side_effect=Exception("network error")):
            await source._poll_once()  # Should not raise

        assert cache.get_price("AAPL") == 190.50  # previous price preserved
        assert source._fatal is False

    async def test_empty_snapshot_list_is_not_an_error(self):
        """Snapshot data is cleared daily ~3:30 AM ET and repopulates from ~4 AM."""
        cache = PriceCache()
        source = _source(cache, poll_interval=60.0)
        source._tickers = ["AAPL"]

        with patch.object(source, "_fetch_snapshots", return_value=[]):
            await source._poll_once()

        assert source._fatal is False

    async def test_empty_tickers_skips_poll(self):
        source = _source()
        source._tickers = []

        with patch.object(source, "_fetch_snapshots") as mock_fetch:
            await source._poll_once()
            mock_fetch.assert_not_called()

    async def test_poll_passes_a_copy_of_the_ticker_list(self):
        """_fetch_snapshots runs in a worker thread while add_ticker mutates
        self._tickers from the event loop."""
        source = _source()
        source._tickers = ["AAPL"]
        captured = []

        def _fetch(tickers):
            captured.append(tickers)
            return []

        with patch.object(source, "_fetch_snapshots", side_effect=_fetch):
            await source._poll_once()

        assert captured == [["AAPL"]]
        assert captured[0] is not source._tickers


@pytest.mark.asyncio
class TestFatalErrors:
    @pytest.mark.parametrize("status", [401, 403])
    async def test_auth_errors_latch_fatal(self, status):
        """401/403 fail identically forever. Retrying every 15s produces an
        infinite error log and never recovers."""
        source = _source()
        source._tickers = ["AAPL"]

        error = Exception("denied")
        error.status = status
        with patch.object(source, "_fetch_snapshots", side_effect=error):
            await source._poll_once()

        assert source._fatal is True

    async def test_transient_error_does_not_latch(self):
        source = _source()
        source._tickers = ["AAPL"]

        error = Exception("rate limited")
        error.status = 429
        with patch.object(source, "_fetch_snapshots", side_effect=error):
            await source._poll_once()

        assert source._fatal is False

    async def test_start_raises_on_fatal_first_poll(self):
        """Gives the caller a clean place to fall back to the simulator instead of
        starting a source that will never produce data."""
        source = MassiveDataSource(api_key="bad", price_cache=PriceCache())

        error = Exception("forbidden")
        error.status = 403
        with patch("app.market.massive_client.RESTClient"):
            with patch.object(source, "_fetch_snapshots", side_effect=error):
                with pytest.raises(RuntimeError, match="Starter plan"):
                    await source.start(["AAPL"])

        assert source._task is None

    async def test_poll_loop_stops_after_a_fatal_poll(self):
        """The latch must actually break the loop, not just be set."""
        source = _source(poll_interval=0.01)
        source._tickers = ["AAPL"]

        error = Exception("forbidden")
        error.status = 403
        with patch.object(source, "_fetch_snapshots", side_effect=error):
            await asyncio.wait_for(source._poll_loop(), timeout=2)

        assert source._fatal is True

    async def test_poll_loop_exits_when_fatal(self):
        source = _source(poll_interval=0.01)
        source._tickers = ["AAPL"]
        source._fatal = True

        await source._poll_loop()  # returns immediately rather than looping


@pytest.mark.asyncio
class TestTickerManagement:
    async def test_add_ticker(self):
        source = _source()
        await source.add_ticker("AAPL")
        assert "AAPL" in source.get_tickers()

    async def test_add_ticker_normalizes(self):
        source = _source()
        await source.add_ticker("  aapl  ")
        assert source.get_tickers() == ["AAPL"]

    async def test_add_duplicate_is_noop(self):
        source = _source()
        await source.add_ticker("AAPL")
        await source.add_ticker("aapl")
        assert source.get_tickers() == ["AAPL"]

    async def test_remove_ticker(self):
        cache = PriceCache()
        source = _source(cache)
        source._tickers = ["AAPL", "GOOGL"]
        cache.update("AAPL", 190.00)

        await source.remove_ticker("aapl")
        assert "AAPL" not in source.get_tickers()
        assert cache.get("AAPL") is None

    async def test_get_tickers_returns_a_copy(self):
        source = _source()
        source._tickers = ["AAPL", "GOOGL"]
        tickers = source.get_tickers()
        tickers.append("MSFT")
        assert source.get_tickers() == ["AAPL", "GOOGL"]

    async def test_supports_ticker_true_when_snapshot_returned(self):
        source = _source()
        with patch.object(source, "_fetch_snapshots", return_value=[_snapshot("PYPL")]):
            assert await source.supports_ticker("pypl") is True

    async def test_supports_ticker_false_when_absent(self):
        source = _source()
        with patch.object(source, "_fetch_snapshots", return_value=[]):
            assert await source.supports_ticker("BANANA") is False

    async def test_supports_ticker_false_on_error(self):
        """Must not raise: PLAN.md maps False to a 422, an exception to a 500."""
        source = _source()
        with patch.object(source, "_fetch_snapshots", side_effect=Exception("boom")):
            assert await source.supports_ticker("AAPL") is False

    async def test_supports_ticker_false_without_client(self):
        source = MassiveDataSource(api_key="k", price_cache=PriceCache())
        assert await source.supports_ticker("AAPL") is False


@pytest.mark.asyncio
class TestLifecycle:
    async def test_name(self):
        assert _source().name == "massive"

    async def test_stop_is_idempotent(self):
        source = MassiveDataSource(api_key="test-key", price_cache=PriceCache())
        await source.stop()
        await source.stop()  # Should not raise

    async def test_stop_cancels_task_and_releases_client(self):
        source = MassiveDataSource(api_key="k", price_cache=PriceCache(), poll_interval=10.0)

        with patch("app.market.massive_client.RESTClient"):
            with patch.object(source, "_fetch_snapshots", return_value=[]):
                await source.start(["AAPL"])

        assert source._task is not None and not source._task.done()

        await source.stop()
        assert source._task is None
        assert source._client is None

    async def test_start_does_an_immediate_poll(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="k", price_cache=cache, poll_interval=60.0)

        with patch("app.market.massive_client.RESTClient"):
            with patch.object(source, "_fetch_snapshots", return_value=[_snapshot("AAPL", 190.5)]):
                await source.start(["AAPL"])

        assert cache.get_price("AAPL") == 190.50
        await source.stop()

    async def test_start_normalizes_tickers(self):
        source = MassiveDataSource(api_key="k", price_cache=PriceCache(), poll_interval=60.0)

        with patch("app.market.massive_client.RESTClient"):
            with patch.object(source, "_fetch_snapshots", return_value=[]):
                await source.start([" aapl ", "googl"])

        assert source.get_tickers() == ["AAPL", "GOOGL"]
        await source.stop()

    async def test_second_start_raises(self):
        """A leaked poller keeps writing to the cache after stop()."""
        source = MassiveDataSource(api_key="k", price_cache=PriceCache(), poll_interval=60.0)

        with patch("app.market.massive_client.RESTClient"):
            with patch.object(source, "_fetch_snapshots", return_value=[]):
                await source.start(["AAPL"])
                with pytest.raises(RuntimeError, match="called twice"):
                    await source.start(["AAPL"])

        await source.stop()
