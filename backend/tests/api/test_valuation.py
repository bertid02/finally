"""The single server-side valuation helper (`app/api/valuation.py`).

Pure arithmetic over data in hand, so most of this needs no app and no database.
"""

from __future__ import annotations

import pytest

from app.api.valuation import (
    build_valuation,
    prices_from_cache,
    value_portfolio,
    value_position,
)
from app.db import Portfolio, Position
from app.market import PriceCache


def test_flattening_the_cache():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    cache.update("GOOGL", 175.0)
    assert prices_from_cache(cache) == {"AAPL": 190.0, "GOOGL": 175.0}


def test_position_profit():
    valued = value_position(Position("AAPL", 10, 100.0), {"AAPL": 150.0})
    assert valued.market_value == 1500.0
    assert valued.cost_basis == 1000.0
    assert valued.unrealized_pnl == 500.0
    assert valued.unrealized_pnl_percent == 50.0
    assert valued.priced is True


def test_position_loss():
    valued = value_position(Position("AAPL", 10, 100.0), {"AAPL": 90.0})
    assert valued.unrealized_pnl == -100.0
    assert valued.unrealized_pnl_percent == -10.0


@pytest.mark.parametrize("prices", [{}, {"AAPL": 0.0}, {"AAPL": -1.0}])
def test_an_unpriced_position_is_valued_at_cost_not_zero(prices):
    """Valuing at zero would put a cliff in the P&L curve and tell the LLM the user
    just lost everything. Valuing at cost says 'no news', which is true."""
    valued = value_position(Position("AAPL", 10, 100.0), prices)
    assert valued.price == 100.0
    assert valued.market_value == 1000.0
    assert valued.unrealized_pnl == 0.0
    assert valued.priced is False


def test_standalone_position_has_no_weight():
    assert value_position(Position("AAPL", 1, 1.0), {"AAPL": 1.0}).weight == 0.0


def test_portfolio_total_is_cash_plus_market_value():
    portfolio = Portfolio(
        cash_balance=5000.0,
        positions=[Position("AAPL", 10, 100.0), Position("MSFT", 5, 200.0)],
    )
    valued = value_portfolio(portfolio, {"AAPL": 150.0, "MSFT": 220.0})
    assert valued.positions_value == 2600.0
    assert valued.total_value == 7600.0
    assert valued.total_cost_basis == 2000.0
    assert valued.total_unrealized_pnl == 600.0
    assert valued.total_unrealized_pnl_percent == 30.0


def test_weights_include_cash_in_the_denominator():
    """A mostly-cash portfolio must read as unconcentrated, not 100% in one holding."""
    portfolio = Portfolio(cash_balance=9000.0, positions=[Position("AAPL", 10, 100.0)])
    valued = value_portfolio(portfolio, {"AAPL": 100.0})
    assert valued.total_value == 10000.0
    assert valued.positions[0].weight == 10.0


def test_all_cash_portfolio_has_no_division_by_zero():
    valued = value_portfolio(Portfolio(cash_balance=10000.0, positions=[]), {})
    assert valued.total_value == 10000.0
    assert valued.total_cost_basis == 0.0
    assert valued.total_unrealized_pnl_percent == 0.0
    assert valued.positions == []


def test_empty_portfolio_with_no_cash_has_no_division_by_zero():
    valued = value_portfolio(Portfolio(cash_balance=0.0, positions=[]), {})
    assert valued.total_value == 0.0
    assert valued.to_dict()["total_value"] == 0.0


def test_to_dict_is_json_shaped():
    valued = value_portfolio(
        Portfolio(cash_balance=1.0, positions=[Position("AAPL", 1, 1.0)]), {"AAPL": 2.0}
    )
    body = valued.to_dict()
    assert set(body) == {
        "cash_balance",
        "positions",
        "positions_value",
        "total_value",
        "total_cost_basis",
        "total_unrealized_pnl",
        "total_unrealized_pnl_percent",
    }
    assert set(body["positions"][0]) == {
        "ticker",
        "quantity",
        "avg_cost",
        "price",
        "market_value",
        "cost_basis",
        "unrealized_pnl",
        "unrealized_pnl_percent",
        "weight",
        "priced",
    }


async def test_build_valuation_against_the_live_app(client):
    """The one-liner the chat layer calls: read portfolio, read cache, value it."""
    await client.post(
        "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 10, "side": "buy"}
    )
    valued = await build_valuation(client.app.state.repository, client.app.state.price_cache)
    assert len(valued.positions) == 1
    assert valued.positions[0].ticker == "AAPL"
    assert valued.total_value == pytest.approx(10000.0, rel=0.05)
    assert 0 < valued.positions[0].weight <= 100
