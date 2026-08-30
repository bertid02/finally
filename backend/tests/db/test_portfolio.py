"""Portfolio reads."""

from __future__ import annotations

import pytest

from app.db import Database, DatabaseError, InvalidTickerError, Portfolio, Repository


class TestGetPortfolio:
    async def test_fresh_portfolio(self, repo: Repository) -> None:
        portfolio = await repo.get_portfolio()
        assert isinstance(portfolio, Portfolio)
        assert portfolio.cash_balance == 10000.0
        assert portfolio.positions == []

    async def test_shape_matches_plan(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 10, 190.50)
        payload = (await repo.get_portfolio()).to_dict()
        assert payload == {
            "cash_balance": 8095.00,
            "positions": [{"ticker": "AAPL", "quantity": 10.0, "avg_cost": 190.5}],
        }

    async def test_no_valuation_fields(self, repo: Repository) -> None:
        # PLAN.md 13.3 S5: valuation lives in the API helper and the client, not here.
        await repo.execute_trade("AAPL", "buy", 1, 100.0)
        payload = (await repo.get_portfolio()).to_dict()
        assert "total_value" not in payload
        assert "unrealized_pnl" not in payload["positions"][0]

    async def test_positions_sorted_alphabetically(self, repo: Repository) -> None:
        for ticker in ("TSLA", "AAPL", "MSFT"):
            await repo.execute_trade(ticker, "buy", 1, 100.0)
        assert [p.ticker for p in (await repo.get_portfolio()).positions] == [
            "AAPL",
            "MSFT",
            "TSLA",
        ]

    async def test_cash_and_positions_are_read_together(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 10, 100.0)
        portfolio = await repo.get_portfolio()
        assert portfolio.cash_balance == 9000.0
        assert portfolio.positions[0].quantity == 10


class TestGetPosition:
    async def test_absent_position_is_none(self, repo: Repository) -> None:
        assert await repo.get_position("AAPL") is None

    async def test_held_position_is_returned(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 3, 50.0)
        position = await repo.get_position("aapl")
        assert position is not None
        assert position.ticker == "AAPL"
        assert position.quantity == 3

    async def test_malformed_ticker_refused(self, repo: Repository) -> None:
        with pytest.raises(InvalidTickerError):
            await repo.get_position("NOT-A-TICKER")


class TestMissingProfile:
    async def test_missing_profile_is_a_loud_failure(self, repo: Repository) -> None:
        # Inventing a default balance here would silently paper over a database
        # that was never initialized, and the user would trade against fiction.
        repo.db.run_sync(lambda c: c.execute("DELETE FROM users_profile"))
        with pytest.raises(DatabaseError) as exc:
            await repo.get_cash_balance()
        assert "not initialized" in exc.value.message.lower()

    async def test_missing_profile_blocks_trades(self, repo: Repository) -> None:
        repo.db.run_sync(lambda c: c.execute("DELETE FROM users_profile"))
        with pytest.raises(DatabaseError):
            await repo.execute_trade("AAPL", "buy", 1, 100.0)


class TestUserScoping:
    async def test_another_user_sees_their_own_empty_portfolio(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 1, 100.0)
        repo.db.run_sync(
            lambda c: c.execute(
                "INSERT INTO users_profile (id, cash_balance, created_at)"
                " VALUES ('other', 500.0, '2026-01-01T00:00:00.000000Z')"
            )
        )
        other = await repo.get_portfolio(user_id="other")
        assert other.cash_balance == 500.0
        assert other.positions == []

    async def test_repository_user_id_is_configurable(self, db: Database) -> None:
        repo = Repository(db, user_id="alice")
        assert repo.user_id == "alice"
