"""Trade execution: cost-basis math, validation, and transaction atomicity."""

from __future__ import annotations

import pytest

from app.db import (
    InsufficientCashError,
    InsufficientSharesError,
    InvalidQuantityError,
    InvalidSideError,
    InvalidTickerError,
    Repository,
    UnknownTickerError,
)


class TestBuy:
    async def test_first_buy_creates_position(self, repo: Repository) -> None:
        result = await repo.execute_trade("AAPL", "buy", 10, 190.50)
        assert result.position is not None
        assert result.position.quantity == 10
        assert result.position.avg_cost == 190.50

    async def test_first_buy_debits_cash(self, repo: Repository) -> None:
        result = await repo.execute_trade("AAPL", "buy", 10, 190.50)
        assert result.cash_balance == 8095.00
        assert await repo.get_cash_balance() == 8095.00

    async def test_trade_result_matches_plan_shape(self, repo: Repository) -> None:
        payload = (await repo.execute_trade("AAPL", "buy", 10, 190.50)).to_dict()
        assert set(payload) == {"trade", "cash_balance", "position"}
        assert set(payload["trade"]) == {
            "id",
            "ticker",
            "side",
            "quantity",
            "price",
            "total",
            "executed_at",
        }
        assert payload["trade"]["total"] == 1905.00
        assert payload["position"] == {"ticker": "AAPL", "quantity": 10.0, "avg_cost": 190.5}

    async def test_second_buy_weights_the_average(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 10, 100.0)
        result = await repo.execute_trade("AAPL", "buy", 30, 200.0)
        # (10*100 + 30*200) / 40 = 175
        assert result.position is not None
        assert result.position.quantity == 40
        assert result.position.avg_cost == 175.0

    async def test_weighted_average_with_awkward_numbers(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 3, 33.33)
        await repo.execute_trade("AAPL", "buy", 7, 71.11)
        position = await repo.get_position("AAPL")
        expected = (3 * 33.33 + 7 * 71.11) / 10
        assert position is not None
        assert position.avg_cost == pytest.approx(expected, abs=1e-6)

    async def test_fractional_shares_supported(self, repo: Repository) -> None:
        result = await repo.execute_trade("AAPL", "buy", 0.5, 200.0)
        assert result.position is not None
        assert result.position.quantity == 0.5
        assert result.cash_balance == 9900.0

    async def test_buy_of_a_second_ticker_is_a_separate_position(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 1, 100.0)
        await repo.execute_trade("MSFT", "buy", 1, 200.0)
        assert [p.ticker for p in await repo.get_positions()] == ["AAPL", "MSFT"]

    async def test_buy_spending_the_entire_balance(self, repo: Repository) -> None:
        result = await repo.execute_trade("AAPL", "buy", 100, 100.0)
        assert result.cash_balance == 0.0

    async def test_buy_beyond_cash_is_refused(self, repo: Repository) -> None:
        with pytest.raises(InsufficientCashError) as exc:
            await repo.execute_trade("NVDA", "buy", 100, 800.0)
        assert exc.value.code == "INSUFFICIENT_CASH"
        assert exc.value.http_status == 409

    async def test_insufficient_cash_message_is_user_facing(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 10, 190.50)
        with pytest.raises(InsufficientCashError) as exc:
            await repo.execute_trade("NVDA", "buy", 100, 800.0)
        assert exc.value.message == "Insufficient cash: need $80,000.00, have $8,095.00"

    async def test_ticker_is_normalized(self, repo: Repository) -> None:
        result = await repo.execute_trade("  aapl  ", "buy", 1, 100.0)
        assert result.trade.ticker == "AAPL"

    async def test_side_is_normalized(self, repo: Repository) -> None:
        result = await repo.execute_trade("AAPL", "BUY", 1, 100.0)
        assert result.trade.side == "buy"


class TestSell:
    @pytest.fixture
    async def held(self, repo: Repository) -> Repository:
        await repo.execute_trade("AAPL", "buy", 10, 100.0)
        return repo

    async def test_partial_sell_reduces_quantity(self, held: Repository) -> None:
        result = await held.execute_trade("AAPL", "sell", 4, 150.0)
        assert result.position is not None
        assert result.position.quantity == 6

    async def test_partial_sell_leaves_avg_cost_untouched(self, held: Repository) -> None:
        result = await held.execute_trade("AAPL", "sell", 4, 150.0)
        assert result.position is not None
        assert result.position.avg_cost == 100.0

    async def test_sell_at_a_loss_also_leaves_avg_cost_untouched(self, held: Repository) -> None:
        result = await held.execute_trade("AAPL", "sell", 4, 10.0)
        assert result.position is not None
        assert result.position.avg_cost == 100.0

    async def test_sell_credits_cash(self, held: Repository) -> None:
        result = await held.execute_trade("AAPL", "sell", 4, 150.0)
        assert result.cash_balance == 9000.0 + 600.0

    async def test_sell_to_zero_deletes_the_row(self, held: Repository) -> None:
        result = await held.execute_trade("AAPL", "sell", 10, 150.0)
        assert result.position is None
        assert await held.get_position("AAPL") is None
        assert await held.get_positions() == []

    async def test_sell_to_zero_reports_null_position_in_payload(self, held: Repository) -> None:
        payload = (await held.execute_trade("AAPL", "sell", 10, 150.0)).to_dict()
        assert payload["position"] is None

    async def test_float_dust_deletes_the_row(self, repo: Repository) -> None:
        # Three thirds of a share do not sum back to one in binary floating point.
        await repo.execute_trade("AAPL", "buy", 1, 90.0)
        third = 1 / 3
        await repo.execute_trade("AAPL", "sell", third, 100.0)
        await repo.execute_trade("AAPL", "sell", third, 100.0)
        result = await repo.execute_trade("AAPL", "sell", third, 100.0)
        assert result.position is None
        assert await repo.get_positions() == []

    async def test_residue_above_epsilon_keeps_the_row(self, held: Repository) -> None:
        result = await held.execute_trade("AAPL", "sell", 10 - 1e-6, 100.0)
        assert result.position is not None
        assert result.position.quantity == pytest.approx(1e-6)

    async def test_overselling_is_refused(self, held: Repository) -> None:
        with pytest.raises(InsufficientSharesError) as exc:
            await held.execute_trade("AAPL", "sell", 11, 150.0)
        assert exc.value.code == "INSUFFICIENT_SHARES"
        assert exc.value.http_status == 409
        assert exc.value.message == "Insufficient shares: tried to sell 11 AAPL, hold 10"

    async def test_selling_an_unheld_ticker_is_refused(self, repo: Repository) -> None:
        with pytest.raises(InsufficientSharesError) as exc:
            await repo.execute_trade("TSLA", "sell", 1, 200.0)
        assert "hold 0" in exc.value.message

    async def test_selling_within_epsilon_of_holding_is_allowed(self, held: Repository) -> None:
        result = await held.execute_trade("AAPL", "sell", 10 + 1e-12, 100.0)
        assert result.position is None

    async def test_rebuy_after_full_sell_starts_a_fresh_basis(self, held: Repository) -> None:
        await held.execute_trade("AAPL", "sell", 10, 150.0)
        result = await held.execute_trade("AAPL", "buy", 2, 300.0)
        assert result.position is not None
        assert result.position.avg_cost == 300.0
        assert result.position.quantity == 2


class TestValidation:
    @pytest.mark.parametrize("quantity", [0, -1, -0.0001, float("nan"), float("inf")])
    async def test_bad_quantity_is_refused(self, repo: Repository, quantity: float) -> None:
        with pytest.raises(InvalidQuantityError) as exc:
            await repo.execute_trade("AAPL", "buy", quantity, 100.0)
        assert exc.value.code == "INVALID_QUANTITY"
        assert exc.value.http_status == 400

    async def test_non_numeric_quantity_is_refused(self, repo: Repository) -> None:
        with pytest.raises(InvalidQuantityError):
            await repo.execute_trade("AAPL", "buy", "ten", 100.0)  # type: ignore[arg-type]

    async def test_none_quantity_is_refused(self, repo: Repository) -> None:
        with pytest.raises(InvalidQuantityError):
            await repo.execute_trade("AAPL", "buy", None, 100.0)  # type: ignore[arg-type]

    @pytest.mark.parametrize("side", ["hodl", "", "b", "sellall"])
    async def test_bad_side_is_refused(self, repo: Repository, side: str) -> None:
        with pytest.raises(InvalidSideError) as exc:
            await repo.execute_trade("AAPL", side, 1, 100.0)
        assert exc.value.code == "INVALID_SIDE"

    @pytest.mark.parametrize("ticker", ["", "TOOLONG", "AA1", "aa-pl", "  "])
    async def test_bad_ticker_is_refused(self, repo: Repository, ticker: str) -> None:
        with pytest.raises(InvalidTickerError) as exc:
            await repo.execute_trade(ticker, "buy", 1, 100.0)
        assert exc.value.code == "INVALID_TICKER"

    @pytest.mark.parametrize("price", [None, 0, -5.0, float("nan"), float("inf")])
    async def test_unusable_price_raises_unknown_ticker(
        self, repo: Repository, price: float | None
    ) -> None:
        # A None price is the price cache saying "no snapshot yet" -- normal under
        # Massive's 15s poll. Zero and NaN are a half-parsed response saying the
        # same thing. None of them may fill.
        with pytest.raises(UnknownTickerError) as exc:
            await repo.execute_trade("AAPL", "buy", 1, price)
        assert exc.value.code == "UNKNOWN_TICKER"
        assert exc.value.http_status == 404

    async def test_validation_failure_writes_nothing(self, repo: Repository) -> None:
        with pytest.raises(InvalidQuantityError):
            await repo.execute_trade("AAPL", "buy", -1, 100.0)
        assert await repo.get_trades() == []
        assert await repo.get_cash_balance() == 10000.0


class TestAtomicity:
    async def test_insufficient_cash_leaves_no_partial_state(self, repo: Repository) -> None:
        with pytest.raises(InsufficientCashError):
            await repo.execute_trade("NVDA", "buy", 100, 800.0)
        assert await repo.get_cash_balance() == 10000.0
        assert await repo.get_positions() == []
        assert await repo.get_trades() == []
        assert await repo.get_portfolio_history() == []

    async def test_oversell_leaves_no_partial_state(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 10, 100.0)
        with pytest.raises(InsufficientSharesError):
            await repo.execute_trade("AAPL", "sell", 50, 100.0)
        assert await repo.get_cash_balance() == 9000.0
        position = await repo.get_position("AAPL")
        assert position is not None and position.quantity == 10
        assert len(await repo.get_trades()) == 1
        assert len(await repo.get_portfolio_history()) == 1

    async def test_failure_after_the_writes_rolls_everything_back(
        self, repo: Repository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The snapshot is the last write in the transaction. Blowing up there is
        # the only way to prove the earlier three writes are genuinely inside the
        # same transaction rather than merely adjacent.
        def _explode(*args: object, **kwargs: object) -> float:
            raise RuntimeError("valuation blew up")

        monkeypatch.setattr(Repository, "_total_value", staticmethod(_explode))

        with pytest.raises(RuntimeError):
            await repo.execute_trade("AAPL", "buy", 10, 100.0)

        assert await repo.get_cash_balance() == 10000.0
        assert await repo.get_positions() == []
        assert await repo.get_trades() == []
        assert await repo.get_portfolio_history() == []

    async def test_database_still_usable_after_a_rollback(
        self, repo: Repository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _explode(*args: object, **kwargs: object) -> float:
            raise RuntimeError("boom")

        monkeypatch.setattr(Repository, "_total_value", staticmethod(_explode))
        with pytest.raises(RuntimeError):
            await repo.execute_trade("AAPL", "buy", 1, 100.0)
        monkeypatch.undo()

        result = await repo.execute_trade("AAPL", "buy", 1, 100.0)
        assert result.cash_balance == 9900.0

    async def test_sequential_trades_stay_consistent(self, repo: Repository) -> None:
        for _ in range(25):
            await repo.execute_trade("AAPL", "buy", 1, 100.0)
        position = await repo.get_position("AAPL")
        assert position is not None
        assert position.quantity == 25
        assert await repo.get_cash_balance() == 7500.0
        assert len(await repo.get_trades()) == 25
        assert len(await repo.get_portfolio_history()) == 25


class TestSnapshotOnTrade:
    async def test_snapshot_written_per_trade(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 1, 100.0)
        await repo.execute_trade("AAPL", "buy", 1, 100.0)
        assert len(await repo.get_portfolio_history()) == 2

    async def test_snapshot_values_the_traded_ticker_at_the_fill(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 10, 100.0)
        history = await repo.get_portfolio_history()
        # 9000 cash + 10 shares at the 100.00 fill = 10000, unchanged by the buy.
        assert history[-1].total_value == 10000.0

    async def test_snapshot_uses_supplied_market_prices(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 10, 100.0)
        await repo.execute_trade("MSFT", "buy", 10, 100.0, market_prices={"AAPL": 150.0})
        history = await repo.get_portfolio_history()
        # 8000 cash + AAPL 10x150 + MSFT 10x100 (the fill) = 10500
        assert history[-1].total_value == 10500.0

    async def test_unpriced_position_falls_back_to_cost_basis(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 10, 100.0)
        await repo.execute_trade("MSFT", "buy", 10, 100.0)
        history = await repo.get_portfolio_history()
        assert history[-1].total_value == 10000.0

    async def test_snapshot_reflects_a_profitable_sell(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 10, 100.0)
        await repo.execute_trade("AAPL", "sell", 10, 150.0)
        history = await repo.get_portfolio_history()
        assert history[-1].total_value == 10500.0
