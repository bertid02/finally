"""The single server-side valuation helper (PLAN.md section 13.3, S5 -- adopted).

One formula, one implementation. Two callers use it:

  * the chat context builder (`app/llm/`), which needs total value, per-position
    P&L and concentration weights to put in front of the model;
  * `/api/health` and any future server-rendered view.

`GET /api/portfolio` deliberately does *not* call it: PLAN.md section 8 keeps
that endpoint unvalued and section 10 makes the client authoritative for anything
displayed, because the header updates live between fetches and a second
server-computed number would only ever disagree with it.

Pure functions over data already in hand -- no repository, no cache, no I/O -- so
the arithmetic can be tested without a database. `build_valuation()` at the
bottom is the convenience wrapper that does the two reads for you.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from app.db import Portfolio, Position, Repository
from app.market import PriceCache


def prices_from_cache(cache: PriceCache) -> dict[str, float]:
    """Flatten the cache to `{ticker: price}` -- what both the valuation helper and
    `Repository.execute_trade(market_prices=...)` want."""
    return {ticker: update.price for ticker, update in cache.get_all().items()}


@dataclass(frozen=True, slots=True)
class PositionValuation:
    """One position priced at a moment in time."""

    ticker: str
    quantity: float
    avg_cost: float
    price: float
    market_value: float
    cost_basis: float
    unrealized_pnl: float
    unrealized_pnl_percent: float
    weight: float  # share of total portfolio value, 0-100
    priced: bool  # False when the cache had nothing and avg_cost stood in

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "quantity": self.quantity,
            "avg_cost": self.avg_cost,
            "price": self.price,
            "market_value": self.market_value,
            "cost_basis": self.cost_basis,
            "unrealized_pnl": self.unrealized_pnl,
            "unrealized_pnl_percent": self.unrealized_pnl_percent,
            "weight": self.weight,
            "priced": self.priced,
        }


@dataclass(frozen=True, slots=True)
class PortfolioValuation:
    """Cash, holdings, and every derived number computed exactly once."""

    cash_balance: float
    positions: list[PositionValuation]
    positions_value: float
    total_value: float
    total_cost_basis: float
    total_unrealized_pnl: float
    total_unrealized_pnl_percent: float

    def to_dict(self) -> dict:
        return {
            "cash_balance": self.cash_balance,
            "positions": [p.to_dict() for p in self.positions],
            "positions_value": self.positions_value,
            "total_value": self.total_value,
            "total_cost_basis": self.total_cost_basis,
            "total_unrealized_pnl": self.total_unrealized_pnl,
            "total_unrealized_pnl_percent": self.total_unrealized_pnl_percent,
        }


def _price_for(position: Position, prices: Mapping[str, float]) -> tuple[float, bool]:
    """The price to value this position at, and whether it is a real one.

    A position with no cached price falls back to its own `avg_cost` -- the same
    rule `Repository.execute_trade` uses for the P&L snapshot. Valuing it at zero
    would put a cliff in the curve and tell the LLM the user just lost everything;
    valuing it at cost merely says "no news", which is true.
    """
    price = prices.get(position.ticker)
    if price is None or price <= 0:
        return position.avg_cost, False
    return price, True


def value_position(position: Position, prices: Mapping[str, float]) -> PositionValuation:
    """Value one position. `weight` is filled in by `value_portfolio`, which alone
    knows the denominator; standalone it is 0.0."""
    price, priced = _price_for(position, prices)
    market_value = round(position.quantity * price, 2)
    cost_basis = position.cost_basis
    pnl = round(market_value - cost_basis, 2)
    pnl_pct = round(pnl / cost_basis * 100, 4) if cost_basis else 0.0
    return PositionValuation(
        ticker=position.ticker,
        quantity=position.quantity,
        avg_cost=position.avg_cost,
        price=round(price, 2),
        market_value=market_value,
        cost_basis=cost_basis,
        unrealized_pnl=pnl,
        unrealized_pnl_percent=pnl_pct,
        weight=0.0,
        priced=priced,
    )


def value_portfolio(
    portfolio: Portfolio, prices: Mapping[str, float]
) -> PortfolioValuation:
    """Value a whole portfolio. This is *the* formula -- do not reimplement it.

    `total_value = cash + Σ(quantity × price)`. Weights are a share of
    `total_value`, cash included, so a mostly-cash portfolio reads as
    unconcentrated rather than 100% in its one small holding.
    """
    valued = [value_position(p, prices) for p in portfolio.positions]
    positions_value = round(sum(p.market_value for p in valued), 2)
    total_value = round(portfolio.cash_balance + positions_value, 2)
    total_cost = round(sum(p.cost_basis for p in valued), 2)
    total_pnl = round(positions_value - total_cost, 2)
    total_pnl_pct = round(total_pnl / total_cost * 100, 4) if total_cost else 0.0

    if total_value:
        valued = [
            replace(p, weight=round(p.market_value / total_value * 100, 4))
            for p in valued
        ]

    return PortfolioValuation(
        cash_balance=portfolio.cash_balance,
        positions=valued,
        positions_value=positions_value,
        total_value=total_value,
        total_cost_basis=total_cost,
        total_unrealized_pnl=total_pnl,
        total_unrealized_pnl_percent=total_pnl_pct,
    )


async def build_valuation(repo: Repository, cache: PriceCache) -> PortfolioValuation:
    """Read the portfolio and the cache, then value it. The one-liner for callers
    that hold a repository and a cache -- which is every real caller."""
    return value_portfolio(await repo.get_portfolio(), prices_from_cache(cache))
