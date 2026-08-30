"""Domain model helpers and wire shapes."""

from __future__ import annotations

import pytest

from app.db import ChatMessage, Portfolio, PortfolioSnapshot, Position, Trade, TradeResult


class TestPosition:
    def test_cost_basis(self) -> None:
        assert Position("AAPL", 10, 190.5).cost_basis == 1905.0

    def test_market_value(self) -> None:
        assert Position("AAPL", 10, 190.5).market_value(200.0) == 2000.0

    def test_unrealized_pnl_profit(self) -> None:
        assert Position("AAPL", 10, 190.5).unrealized_pnl(200.0) == 95.0

    def test_unrealized_pnl_loss(self) -> None:
        assert Position("AAPL", 10, 190.5).unrealized_pnl(180.0) == -105.0

    def test_fractional_quantity(self) -> None:
        assert Position("AAPL", 0.5, 100.0).cost_basis == 50.0

    def test_to_dict_omits_valuation(self) -> None:
        assert Position("AAPL", 10, 190.5).to_dict() == {
            "ticker": "AAPL",
            "quantity": 10,
            "avg_cost": 190.5,
        }

    def test_is_frozen(self) -> None:
        with pytest.raises(AttributeError):
            Position("AAPL", 1, 1).quantity = 5  # type: ignore[misc]


class TestTrade:
    def test_total(self) -> None:
        assert Trade("i", "AAPL", "buy", 10, 190.5, "t").total == 1905.0

    def test_total_rounds_to_cents(self) -> None:
        assert Trade("i", "AAPL", "buy", 3, 33.333, "t").total == 100.0

    def test_to_dict_includes_total(self) -> None:
        payload = Trade("i", "AAPL", "sell", 2, 50.0, "t").to_dict()
        assert payload["total"] == 100.0
        assert payload["side"] == "sell"


class TestTradeResult:
    def test_to_dict_with_position(self) -> None:
        result = TradeResult(
            Trade("i", "AAPL", "buy", 10, 190.5, "t"), 8095.0, Position("AAPL", 10, 190.5)
        )
        assert result.to_dict()["position"] == {
            "ticker": "AAPL",
            "quantity": 10,
            "avg_cost": 190.5,
        }

    def test_to_dict_with_closed_position(self) -> None:
        result = TradeResult(Trade("i", "AAPL", "sell", 10, 200.0, "t"), 10095.0, None)
        assert result.to_dict()["position"] is None


class TestPortfolio:
    def test_to_dict(self) -> None:
        portfolio = Portfolio(8095.0, [Position("AAPL", 10, 190.5)])
        assert portfolio.to_dict() == {
            "cash_balance": 8095.0,
            "positions": [{"ticker": "AAPL", "quantity": 10, "avg_cost": 190.5}],
        }

    def test_empty_positions(self) -> None:
        assert Portfolio(10000.0, []).to_dict()["positions"] == []


class TestSnapshotAndMessage:
    def test_snapshot_to_dict(self) -> None:
        assert PortfolioSnapshot("i", 10000.0, "t").to_dict() == {
            "id": "i",
            "total_value": 10000.0,
            "recorded_at": "t",
        }

    def test_chat_message_to_dict(self) -> None:
        assert ChatMessage("i", "user", "hi", None, "t").to_dict() == {
            "id": "i",
            "role": "user",
            "content": "hi",
            "actions": None,
            "created_at": "t",
        }

    def test_chat_message_carries_actions(self) -> None:
        actions = {"trades": [], "watchlist_changes": []}
        assert ChatMessage("i", "assistant", "hi", actions, "t").to_dict()["actions"] == actions
