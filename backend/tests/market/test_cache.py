"""Tests for PriceCache."""

from app.market.cache import PriceCache


class TestPriceCache:
    """Unit tests for the PriceCache."""

    def test_update_and_get(self):
        cache = PriceCache()
        update = cache.update("AAPL", 190.50)
        assert update.ticker == "AAPL"
        assert update.price == 190.50
        assert cache.get("AAPL") == update

    def test_first_update_is_flat(self):
        cache = PriceCache()
        update = cache.update("AAPL", 190.50)
        assert update.direction == "flat"
        assert update.previous_price == 190.50

    def test_direction_up(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        update = cache.update("AAPL", 191.00)
        assert update.direction == "up"
        assert update.change == 1.00

    def test_direction_down(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        update = cache.update("AAPL", 189.00)
        assert update.direction == "down"
        assert update.change == -1.00

    def test_remove(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.remove("AAPL")
        assert cache.get("AAPL") is None

    def test_remove_nonexistent(self):
        cache = PriceCache()
        cache.remove("AAPL")  # Should not raise

    def test_get_all(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.update("GOOGL", 175.00)
        assert set(cache.get_all().keys()) == {"AAPL", "GOOGL"}

    def test_version_increments(self):
        cache = PriceCache()
        v0 = cache.version
        cache.update("AAPL", 190.00)
        assert cache.version == v0 + 1
        cache.update("AAPL", 191.00)
        assert cache.version == v0 + 2

    def test_remove_bumps_version(self):
        """Without this, removing the last ticker leaves SSE believing nothing
        changed and the browser keeps showing a forgotten ticker."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        v = cache.version
        cache.remove("AAPL")
        assert cache.version > v

    def test_get_price_convenience(self):
        cache = PriceCache()
        cache.update("AAPL", 190.50)
        assert cache.get_price("AAPL") == 190.50
        assert cache.get_price("NOPE") is None

    def test_len(self):
        cache = PriceCache()
        assert len(cache) == 0
        cache.update("AAPL", 190.00)
        assert len(cache) == 1
        cache.update("GOOGL", 175.00)
        assert len(cache) == 2

    def test_contains(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        assert "AAPL" in cache
        assert "GOOGL" not in cache

    def test_custom_timestamp(self):
        cache = PriceCache()
        update = cache.update("AAPL", 190.50, timestamp=1234567890.0)
        assert update.timestamp == 1234567890.0

    def test_explicit_zero_timestamp_is_respected(self):
        """`timestamp or time.time()` would silently swallow an explicit 0.0."""
        cache = PriceCache()
        assert cache.update("AAPL", 190.50, timestamp=0.0).timestamp == 0.0

    def test_price_rounding(self):
        cache = PriceCache()
        assert cache.update("AAPL", 190.12345).price == 190.12


class TestSessionOpen:
    """The daily-change denominator: captured once, preserved thereafter."""

    def test_defaults_to_first_observed_price(self):
        cache = PriceCache()
        update = cache.update("AAPL", 190.00)
        assert update.session_open == 190.00
        assert update.change_percent_session == 0.0

    def test_explicit_anchor_is_used(self):
        cache = PriceCache()
        update = cache.update("AAPL", 190.00, session_open=180.00)
        assert update.session_open == 180.00

    def test_anchor_is_preserved_across_updates(self):
        """The denominator must not drift mid-session."""
        cache = PriceCache()
        cache.update("AAPL", 100.00, session_open=100.00)
        for price in (101.0, 102.0, 103.0):
            update = cache.update("AAPL", price)
            assert update.session_open == 100.00
        assert update.change_percent_session == 3.0

    def test_later_anchor_cannot_overwrite_the_first(self):
        cache = PriceCache()
        cache.update("AAPL", 100.00, session_open=100.00)
        update = cache.update("AAPL", 105.00, session_open=999.00)
        assert update.session_open == 100.00

    def test_remove_drops_the_anchor(self):
        """A re-added ticker re-anchors rather than resurrecting a stale one."""
        cache = PriceCache()
        cache.update("AAPL", 100.00, session_open=100.00)
        cache.remove("AAPL")
        update = cache.update("AAPL", 150.00)
        assert update.session_open == 150.00
        assert update.change_percent_session == 0.0

    def test_zero_anchor_falls_back_to_price(self):
        """Massive returns 0, not null, for bars on a ticker that has not traded."""
        cache = PriceCache()
        update = cache.update("AAPL", 190.00, session_open=0.0)
        assert update.session_open == 190.00

    def test_provider_percent_is_passed_through(self):
        cache = PriceCache()
        update = cache.update("AAPL", 190.00, session_open=180.00, change_percent_session=1.25)
        assert update.change_percent_session == 1.25

    def test_anchors_are_per_ticker(self):
        cache = PriceCache()
        cache.update("AAPL", 100.00, session_open=90.00)
        cache.update("GOOGL", 200.00, session_open=250.00)
        assert cache.get("AAPL").session_open == 90.00
        assert cache.get("GOOGL").session_open == 250.00
