"""Integration tests for SimulatorDataSource."""

import asyncio

import pytest

from app.market.cache import PriceCache
from app.market.seed_prices import SEED_PRICES
from app.market.simulator import SimulatorDataSource


def _source(cache, **kwargs) -> SimulatorDataSource:
    kwargs.setdefault("update_interval", 0.05)
    kwargs.setdefault("seed", 11)
    return SimulatorDataSource(price_cache=cache, **kwargs)


@pytest.mark.asyncio
class TestSimulatorDataSource:
    """Integration tests for the SimulatorDataSource."""

    async def test_name(self):
        assert _source(PriceCache()).name == "simulator"

    async def test_start_populates_cache_before_returning(self):
        """A client connecting immediately after startup must see prices."""
        cache = PriceCache()
        source = _source(cache)
        await source.start(["AAPL", "GOOGL"])

        assert cache.get("AAPL") is not None
        assert cache.get("GOOGL") is not None

        await source.stop()

    async def test_prices_update_over_time(self):
        cache = PriceCache()
        source = _source(cache)
        await source.start(["AAPL"])

        initial_version = cache.version
        await asyncio.sleep(0.3)
        assert cache.version > initial_version

        await source.stop()

    async def test_custom_update_interval(self):
        cache = PriceCache()
        source = _source(cache, update_interval=0.01)
        await source.start(["AAPL"])

        initial_version = cache.version
        await asyncio.sleep(0.05)
        assert cache.version > initial_version + 2

        await source.stop()

    async def test_get_tickers(self):
        cache = PriceCache()
        source = _source(cache)
        await source.start(["AAPL", "GOOGL"])

        assert set(source.get_tickers()) == {"AAPL", "GOOGL"}

        await source.stop()

    async def test_empty_start(self):
        cache = PriceCache()
        source = _source(cache)
        await source.start([])

        assert len(cache) == 0
        assert source.get_tickers() == []

        await source.stop()

    async def test_loop_survives_a_failing_step(self):
        """An unhandled exception here would silently kill the task and leave the
        app serving a frozen price grid with no error anywhere."""
        cache = PriceCache()
        source = _source(cache)
        await source.start(["AAPL"])

        calls = {"n": 0}
        real_step = source._sim.step

        def flaky_step():
            calls["n"] += 1
            if calls["n"] <= 2:
                raise RuntimeError("boom")
            return real_step()

        source._sim.step = flaky_step
        await asyncio.sleep(0.3)

        assert calls["n"] > 2  # kept ticking past the failures
        assert not source._task.done()

        await source.stop()


@pytest.mark.asyncio
class TestSessionAnchoring:
    async def test_start_anchors_on_the_seed_price(self):
        cache = PriceCache()
        source = _source(cache)
        await source.start(["AAPL"])

        update = cache.get("AAPL")
        assert update.session_open == SEED_PRICES["AAPL"]
        assert update.change_percent_session == 0.0

        await source.stop()

    async def test_anchor_survives_many_ticks(self):
        """PLAN.md section 6: fixed for the process lifetime."""
        cache = PriceCache()
        source = _source(cache, update_interval=0.01)
        await source.start(["AAPL"])
        await asyncio.sleep(0.2)

        assert cache.get("AAPL").session_open == SEED_PRICES["AAPL"]

        await source.stop()

    async def test_added_ticker_anchors_at_its_own_seed(self):
        """A new row shows 0.00%, not a percentage against a price it never traded at."""
        cache = PriceCache()
        source = _source(cache)
        await source.start(["AAPL"])

        await source.add_ticker("TSLA")
        update = cache.get("TSLA")
        assert update.session_open == SEED_PRICES["TSLA"]
        assert update.change_percent_session == 0.0

        await source.stop()

    async def test_provider_percent_is_never_set_by_the_simulator(self):
        """No corporate actions here, so the derived path must be correct."""
        cache = PriceCache()
        source = _source(cache)
        await source.start(["AAPL"])

        assert cache.get("AAPL").provider_change_percent is None

        await source.stop()


@pytest.mark.asyncio
class TestTickerManagement:
    async def test_add_ticker(self):
        cache = PriceCache()
        source = _source(cache)
        await source.start(["AAPL"])

        await source.add_ticker("TSLA")
        assert "TSLA" in source.get_tickers()
        assert cache.get("TSLA") is not None

        await source.stop()

    async def test_add_ticker_normalizes(self):
        cache = PriceCache()
        source = _source(cache)
        await source.start(["AAPL"])

        await source.add_ticker(" tsla ")
        assert source.get_tickers() == ["AAPL", "TSLA"]
        assert cache.get("TSLA") is not None

        await source.stop()

    async def test_remove_ticker(self):
        cache = PriceCache()
        source = _source(cache)
        await source.start(["AAPL", "TSLA"])

        await source.remove_ticker("tsla")
        assert "TSLA" not in source.get_tickers()
        assert cache.get("TSLA") is None

        await source.stop()

    async def test_removed_ticker_stops_being_republished(self):
        cache = PriceCache()
        source = _source(cache, update_interval=0.01)
        await source.start(["AAPL", "TSLA"])

        await source.remove_ticker("TSLA")
        await asyncio.sleep(0.1)

        assert cache.get("TSLA") is None

        await source.stop()


@pytest.mark.asyncio
class TestSupportsTicker:
    @pytest.mark.parametrize("ticker", ["AAPL", "aapl", "  msft  ", "V", "GOOGL"])
    async def test_accepts_well_formed_symbols(self, ticker):
        assert await _source(PriceCache()).supports_ticker(ticker) is True

    @pytest.mark.parametrize("ticker", ["BANANA", "", "   ", "AA-PL", "123", "AAPL1"])
    async def test_rejects_malformed_symbols(self, ticker):
        """Without this, POST /api/watchlist {"ticker": "BANANA"} silently succeeds
        and streams an invented price under a name that does not exist."""
        assert await _source(PriceCache()).supports_ticker(ticker) is False

    async def test_works_before_start(self):
        """The watchlist endpoint may check a symbol at any time."""
        assert await _source(PriceCache()).supports_ticker("AAPL") is True


@pytest.mark.asyncio
class TestLifecycle:
    async def test_stop_is_idempotent(self):
        cache = PriceCache()
        source = _source(cache)
        await source.start(["AAPL"])
        await source.stop()
        await source.stop()  # Should not raise

    async def test_stop_before_start_is_safe(self):
        await _source(PriceCache()).stop()

    async def test_no_writes_after_stop(self):
        """The interface contract: a stopped source never writes again."""
        cache = PriceCache()
        source = _source(cache, update_interval=0.01)
        await source.start(["AAPL"])
        await source.stop()

        version = cache.version
        await source.add_ticker("TSLA")
        await asyncio.sleep(0.1)

        assert cache.get("TSLA") is None
        assert cache.version == version

    async def test_second_start_raises(self):
        """A second background task would keep writing after stop() cancels the
        handle it knows about."""
        cache = PriceCache()
        source = _source(cache)
        await source.start(["AAPL"])

        with pytest.raises(RuntimeError, match="called twice"):
            await source.start(["AAPL"])

        await source.stop()

    async def test_stop_actually_halts_the_cache(self):
        cache = PriceCache()
        source = _source(cache, update_interval=0.01)
        await source.start(["AAPL"])
        await asyncio.sleep(0.05)
        await source.stop()

        version = cache.version
        await asyncio.sleep(0.1)
        assert cache.version == version
