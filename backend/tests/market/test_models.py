"""Tests for PriceUpdate dataclass."""

import pytest

from app.market.models import PriceUpdate


class TestPriceUpdate:
    """Unit tests for the PriceUpdate model."""

    def test_price_update_creation(self):
        """Test basic PriceUpdate creation."""
        update = PriceUpdate(ticker="AAPL", price=190.50, previous_price=190.00, timestamp=1234567890.0)
        assert update.ticker == "AAPL"
        assert update.price == 190.50
        assert update.previous_price == 190.00
        assert update.timestamp == 1234567890.0

    def test_change_calculation(self):
        """Test price change calculation."""
        update = PriceUpdate(ticker="AAPL", price=190.50, previous_price=190.00, timestamp=1234567890.0)
        assert update.change == 0.50

    def test_change_negative(self):
        """Test negative price change."""
        update = PriceUpdate(ticker="AAPL", price=189.50, previous_price=190.00, timestamp=1234567890.0)
        assert update.change == -0.50

    def test_change_percent_up(self):
        """Test percentage change calculation (up)."""
        update = PriceUpdate(ticker="AAPL", price=190.00, previous_price=100.00, timestamp=1234567890.0)
        assert update.change_percent == 90.0

    def test_change_percent_down(self):
        """Test percentage change calculation (down)."""
        update = PriceUpdate(ticker="AAPL", price=100.00, previous_price=200.00, timestamp=1234567890.0)
        assert update.change_percent == -50.0

    def test_change_percent_zero_previous(self):
        """Test percentage change with zero previous price."""
        update = PriceUpdate(ticker="AAPL", price=100.00, previous_price=0.00, timestamp=1234567890.0)
        assert update.change_percent == 0.0

    def test_direction_up(self):
        """Test direction calculation (up)."""
        update = PriceUpdate(ticker="AAPL", price=191.00, previous_price=190.00, timestamp=1234567890.0)
        assert update.direction == "up"

    def test_direction_down(self):
        """Test direction calculation (down)."""
        update = PriceUpdate(ticker="AAPL", price=189.00, previous_price=190.00, timestamp=1234567890.0)
        assert update.direction == "down"

    def test_direction_flat(self):
        """Test direction calculation (flat)."""
        update = PriceUpdate(ticker="AAPL", price=190.00, previous_price=190.00, timestamp=1234567890.0)
        assert update.direction == "flat"

    def test_to_dict(self):
        """Test serialization to dictionary."""
        update = PriceUpdate(
            ticker="AAPL",
            price=190.50,
            previous_price=190.00,
            timestamp=1234567890.0,
            session_open=180.00,
        )
        result = update.to_dict()

        assert result["ticker"] == "AAPL"
        assert result["price"] == 190.50
        assert result["previous_price"] == 190.00
        assert result["session_open"] == 180.00
        assert result["timestamp"] == 1234567890.0
        assert result["change"] == 0.50
        assert result["change_percent"] == 0.2632  # (0.50 / 190.00) * 100
        assert result["change_percent_session"] == 5.8333  # (10.50 / 180.00) * 100
        assert result["direction"] == "up"

    def test_session_open_default(self):
        """Test that session_open defaults to 0.0 when not provided."""
        update = PriceUpdate(ticker="AAPL", price=190.50, previous_price=190.00)
        assert update.session_open == 0.0

    def test_change_percent_session_up(self):
        """Test daily change % calculation relative to session_open (up)."""
        update = PriceUpdate(
            ticker="AAPL", price=110.00, previous_price=109.00, session_open=100.00
        )
        assert update.change_percent_session == 10.0

    def test_change_percent_session_down(self):
        """Test daily change % calculation relative to session_open (down)."""
        update = PriceUpdate(
            ticker="AAPL", price=90.00, previous_price=91.00, session_open=100.00
        )
        assert update.change_percent_session == -10.0

    def test_change_percent_session_zero_open(self):
        """Test daily change % is 0 rather than raising when session_open is unset."""
        update = PriceUpdate(ticker="AAPL", price=100.00, previous_price=100.00)
        assert update.change_percent_session == 0.0

    def test_change_percent_session_independent_of_tick_change(self):
        """Test that change_percent (tick) and change_percent_session (daily) diverge."""
        update = PriceUpdate(
            ticker="AAPL", price=190.02, previous_price=190.00, session_open=180.00
        )
        # Tick-over-tick is tiny noise...
        assert update.change_percent < 0.1
        # ...but the daily figure reflects the real move since the open.
        assert update.change_percent_session > 5.0

    def test_immutability(self):
        """Test that PriceUpdate is immutable."""
        update = PriceUpdate(ticker="AAPL", price=190.50, previous_price=190.00, timestamp=1234567890.0)

        with pytest.raises(AttributeError):
            update.price = 200.00  # Should raise error
