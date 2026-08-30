"""Trade log, realized P&L, and the portfolio value series."""

from __future__ import annotations

import pytest

from app.db import Repository, utc_now_iso


class TestTradeLog:
    async def test_empty_initially(self, repo: Repository) -> None:
        assert await repo.get_trades() == []

    async def test_returns_oldest_first(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 1, 100.0)
        await repo.execute_trade("MSFT", "buy", 1, 200.0)
        assert [t.ticker for t in await repo.get_trades()] == ["AAPL", "MSFT"]

    async def test_filters_by_ticker(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 1, 100.0)
        await repo.execute_trade("MSFT", "buy", 1, 200.0)
        assert [t.ticker for t in await repo.get_trades(ticker="msft")] == ["MSFT"]

    async def test_limit_keeps_the_newest(self, repo: Repository) -> None:
        for price in (100.0, 200.0, 300.0):
            await repo.execute_trade("AAPL", "buy", 1, price)
        trades = await repo.get_trades(limit=2)
        assert [t.price for t in trades] == [200.0, 300.0]

    @pytest.mark.parametrize("limit,expected", [(0, 1), (-5, 1), (99999, 3)])
    async def test_limit_is_clamped(self, repo: Repository, limit: int, expected: int) -> None:
        for _ in range(3):
            await repo.execute_trade("AAPL", "buy", 1, 100.0)
        assert len(await repo.get_trades(limit=limit)) == expected

    async def test_trade_fields_round_trip(self, repo: Repository) -> None:
        written = (await repo.execute_trade("AAPL", "buy", 2.5, 40.0)).trade
        read = (await repo.get_trades())[0]
        assert read == written
        assert read.total == 100.0

    async def test_log_is_append_only_across_a_full_sell(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 1, 100.0)
        await repo.execute_trade("AAPL", "sell", 1, 150.0)
        assert [t.side for t in await repo.get_trades()] == ["buy", "sell"]


class TestRealizedPnl:
    async def test_zero_with_no_trades(self, repo: Repository) -> None:
        assert await repo.get_realized_pnl() == 0.0

    async def test_zero_after_a_buy_only(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 10, 100.0)
        assert await repo.get_realized_pnl() == 0.0

    async def test_profit_on_a_simple_round_trip(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 10, 100.0)
        await repo.execute_trade("AAPL", "sell", 10, 150.0)
        assert await repo.get_realized_pnl() == 500.0

    async def test_loss_is_negative(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 10, 100.0)
        await repo.execute_trade("AAPL", "sell", 10, 60.0)
        assert await repo.get_realized_pnl() == -400.0

    async def test_uses_the_basis_at_the_time_of_the_sale(self, repo: Repository) -> None:
        # Sell first, then buy higher. Folding against the *current* avg_cost
        # would report a loss; replaying the log reports the real +150.
        await repo.execute_trade("AAPL", "buy", 10, 100.0)
        await repo.execute_trade("AAPL", "sell", 5, 130.0)
        await repo.execute_trade("AAPL", "buy", 5, 300.0)
        assert await repo.get_realized_pnl() == 150.0

    async def test_weighted_basis_across_multiple_buys(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 10, 100.0)
        await repo.execute_trade("AAPL", "buy", 10, 200.0)  # avg 150
        await repo.execute_trade("AAPL", "sell", 5, 180.0)
        assert await repo.get_realized_pnl() == 150.0

    async def test_sums_across_tickers(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 10, 100.0)
        await repo.execute_trade("MSFT", "buy", 10, 100.0)
        await repo.execute_trade("AAPL", "sell", 10, 110.0)
        await repo.execute_trade("MSFT", "sell", 10, 90.0)
        assert await repo.get_realized_pnl() == 0.0

    async def test_full_sell_then_rebuy_resets_the_basis(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 10, 100.0)
        await repo.execute_trade("AAPL", "sell", 10, 120.0)  # +200
        await repo.execute_trade("AAPL", "buy", 10, 50.0)
        await repo.execute_trade("AAPL", "sell", 10, 60.0)  # +100
        assert await repo.get_realized_pnl() == 300.0


class TestPortfolioHistory:
    async def test_empty_initially(self, repo: Repository) -> None:
        assert await repo.get_portfolio_history() == []

    async def test_oldest_first(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 10, 100.0)
        await repo.execute_trade("AAPL", "sell", 10, 200.0)
        history = await repo.get_portfolio_history()
        assert [h.total_value for h in history] == [10000.0, 11000.0]

    async def test_limit_keeps_the_newest(self, repo: Repository) -> None:
        for _ in range(5):
            await repo.execute_trade("AAPL", "buy", 1, 100.0)
        assert len(await repo.get_portfolio_history(limit=2)) == 2

    @pytest.mark.parametrize("limit,expected", [(0, 1), (-1, 1), (10**9, 3)])
    async def test_limit_is_clamped(self, repo: Repository, limit: int, expected: int) -> None:
        for _ in range(3):
            await repo.execute_trade("AAPL", "buy", 1, 100.0)
        assert len(await repo.get_portfolio_history(limit=limit)) == expected

    async def test_since_filters_inclusively(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 1, 100.0)
        cutoff = utc_now_iso()
        await repo.execute_trade("AAPL", "buy", 1, 100.0)
        history = await repo.get_portfolio_history(since=cutoff)
        assert len(history) == 1
        assert history[0].recorded_at >= cutoff

    async def test_since_in_the_future_returns_nothing(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 1, 100.0)
        assert await repo.get_portfolio_history(since="2999-01-01T00:00:00.000000Z") == []

    async def test_since_before_everything_returns_all(self, repo: Repository) -> None:
        for _ in range(3):
            await repo.execute_trade("AAPL", "buy", 1, 100.0)
        assert len(await repo.get_portfolio_history(since="2000-01-01T00:00:00.000000Z")) == 3

    async def test_snapshot_shape(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 1, 100.0)
        payload = (await repo.get_portfolio_history())[0].to_dict()
        assert set(payload) == {"id", "total_value", "recorded_at"}

    async def test_record_snapshot_writes_a_point(self, repo: Repository) -> None:
        snapshot = await repo.record_snapshot(10000.0)
        history = await repo.get_portfolio_history()
        assert history == [snapshot]

    async def test_record_snapshot_rounds_to_cents(self, repo: Repository) -> None:
        snapshot = await repo.record_snapshot(1234.56789)
        assert snapshot.total_value == 1234.57
