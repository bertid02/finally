"""Tests for the PriceUpdate dataclass."""

import pytest

from app.market.models import PriceUpdate


def _update(price=190.50, previous=190.00, session_open=185.00, provider=None):
    return PriceUpdate(
        ticker="AAPL",
        price=price,
        previous_price=previous,
        session_open=session_open,
        timestamp=1234567890.0,
        provider_change_percent=provider,
    )


class TestPriceUpdate:
    """Unit tests for the PriceUpdate model."""

    def test_price_update_creation(self):
        update = _update()
        assert update.ticker == "AAPL"
        assert update.price == 190.50
        assert update.previous_price == 190.00
        assert update.session_open == 185.00
        assert update.timestamp == 1234567890.0

    def test_session_open_is_required(self):
        """A default would let a source silently emit a 0% daily change forever."""
        with pytest.raises(TypeError):
            PriceUpdate(ticker="AAPL", price=190.0, previous_price=190.0)

    # --- tick-over-tick ---

    def test_change_calculation(self):
        assert _update(price=190.50, previous=190.00).change == 0.50

    def test_change_negative(self):
        assert _update(price=189.50, previous=190.00).change == -0.50

    def test_change_percent_up(self):
        assert _update(price=190.00, previous=100.00).change_percent == 90.0

    def test_change_percent_down(self):
        assert _update(price=100.00, previous=200.00).change_percent == -50.0

    def test_change_percent_zero_previous(self):
        assert _update(price=100.00, previous=0.00).change_percent == 0.0

    def test_direction_up(self):
        assert _update(price=191.00, previous=190.00).direction == "up"

    def test_direction_down(self):
        assert _update(price=189.00, previous=190.00).direction == "down"

    def test_direction_flat(self):
        assert _update(price=190.00, previous=190.00).direction == "flat"

    # --- versus session open ---

    def test_change_session(self):
        assert _update(price=190.00, session_open=180.00).change_session == 10.0

    def test_change_percent_session_derived(self):
        update = _update(price=110.00, session_open=100.00)
        assert update.change_percent_session == 10.0

    def test_change_percent_session_negative(self):
        update = _update(price=90.00, session_open=100.00)
        assert update.change_percent_session == -10.0

    def test_change_percent_session_zero_anchor(self):
        assert _update(price=100.00, session_open=0.0).change_percent_session == 0.0

    def test_provider_percent_wins_over_derived(self):
        """Massive's todaysChangePerc is split-adjusted; the local formula is not."""
        update = _update(price=100.00, session_open=200.00, provider=1.25)
        assert update.change_percent_session == 1.25  # not the -50% a split would fake

    def test_provider_percent_none_falls_back_to_derived(self):
        update = _update(price=110.00, session_open=100.00, provider=None)
        assert update.change_percent_session == 10.0

    def test_session_and_tick_percentages_are_independent(self):
        """The two notions of 'change' must not collapse into one another."""
        update = _update(price=190.10, previous=190.00, session_open=180.00)
        assert update.change_percent == pytest.approx(0.0526, abs=1e-4)
        assert update.change_percent_session == pytest.approx(5.6111, abs=1e-4)

    # --- serialization ---

    def test_to_dict(self):
        result = _update(price=190.50, previous=190.00, session_open=185.00).to_dict()

        assert result["ticker"] == "AAPL"
        assert result["price"] == 190.50
        assert result["previous_price"] == 190.00
        assert result["session_open"] == 185.00
        assert result["timestamp"] == 1234567890.0
        assert result["change"] == 0.50
        assert result["change_percent"] == 0.2632  # (0.50 / 190.00) * 100
        assert result["change_session"] == 5.50
        assert result["change_percent_session"] == 2.973
        assert result["direction"] == "up"

    def test_to_dict_keys_are_the_frontend_contract(self):
        """PLAN.md section 6 pins this payload shape. Additive changes only."""
        assert set(_update().to_dict()) == {
            "ticker",
            "price",
            "previous_price",
            "session_open",
            "timestamp",
            "change",
            "change_percent",
            "change_session",
            "change_percent_session",
            "direction",
        }

    def test_immutability(self):
        update = _update()
        with pytest.raises(AttributeError):
            update.price = 200.00
