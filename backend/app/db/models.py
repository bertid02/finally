"""Domain models returned by the repository.

These are the shapes the API layer serializes. Each `to_dict()` is the literal
JSON body PLAN.md section 8 specifies, so a route can return
`result.to_dict()` without reshaping -- one definition of the wire format rather
than one per caller.

Frozen dataclasses, matching `app/market/models.py`: a repository result is a
snapshot of what the database said at one moment, and letting a caller mutate it
would only ever create a lie.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Position:
    """A held position. Rows only exist while quantity is meaningfully non-zero.

    `avg_cost` is the weighted-average purchase price. Sells never change it
    (PLAN.md section 7), so it stays the cost basis of the shares still held.
    Unrealized P&L is deliberately absent: it needs a live price, which the
    database does not have, and PLAN.md section 10 makes the client authoritative
    for anything displayed.
    """

    ticker: str
    quantity: float
    avg_cost: float

    @property
    def cost_basis(self) -> float:
        """What the remaining shares cost. The denominator for unrealized P&L %."""
        return round(self.quantity * self.avg_cost, 2)

    def market_value(self, price: float) -> float:
        """Value at a caller-supplied price. The caller owns the price cache; we don't."""
        return round(self.quantity * price, 2)

    def unrealized_pnl(self, price: float) -> float:
        """Profit on the held shares at a caller-supplied price."""
        return round((price - self.avg_cost) * self.quantity, 2)

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "quantity": self.quantity,
            "avg_cost": self.avg_cost,
        }


@dataclass(frozen=True, slots=True)
class Trade:
    """One executed fill from the append-only trade log."""

    id: str
    ticker: str
    side: str  # 'buy' | 'sell'
    quantity: float
    price: float
    executed_at: str  # ISO-8601 UTC

    @property
    def total(self) -> float:
        """Cash moved by this fill. No fees, no partial fills (PLAN.md section 3)."""
        return round(self.quantity * self.price, 2)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ticker": self.ticker,
            "side": self.side,
            "quantity": self.quantity,
            "price": self.price,
            "total": self.total,
            "executed_at": self.executed_at,
        }


@dataclass(frozen=True, slots=True)
class TradeResult:
    """Everything that changed as a result of one trade.

    Returned whole so a caller never has to re-read the portfolio to find out
    what a trade did -- which is also what stops the chat panel and the header
    from briefly disagreeing after an LLM-executed trade.
    """

    trade: Trade
    cash_balance: float
    position: Position | None  # None when a sell closed the position entirely

    def to_dict(self) -> dict:
        return {
            "trade": self.trade.to_dict(),
            "cash_balance": self.cash_balance,
            "position": self.position.to_dict() if self.position else None,
        }


@dataclass(frozen=True, slots=True)
class Portfolio:
    """Cash and holdings. Deliberately unvalued.

    PLAN.md section 13.3 (S5, adopted): valuation happens in exactly two places --
    one server-side helper owned by the API layer, and the client's live
    calculation. The repository is not one of them.
    """

    cash_balance: float
    positions: list[Position]

    def to_dict(self) -> dict:
        return {
            "cash_balance": self.cash_balance,
            "positions": [p.to_dict() for p in self.positions],
        }


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    """A point on the P&L curve, written at trade execution only."""

    id: str
    total_value: float
    recorded_at: str  # ISO-8601 UTC

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "total_value": self.total_value,
            "recorded_at": self.recorded_at,
        }


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """One turn of the conversation.

    `actions` is the already-parsed dict from PLAN.md section 7 (it is stored as a
    JSON string, but no caller should have to know that). It is None for user
    messages and for assistant messages that did nothing.
    """

    id: str
    role: str  # 'user' | 'assistant'
    content: str
    actions: dict | None
    created_at: str  # ISO-8601 UTC

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "actions": self.actions,
            "created_at": self.created_at,
        }
