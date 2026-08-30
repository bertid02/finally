"""Watchlist CRUD -- membership only, idempotent, capacity-bounded."""

from __future__ import annotations

import pytest

from app.db import (
    DEFAULT_WATCHLIST,
    MAX_WATCHLIST_SIZE,
    InvalidTickerError,
    Repository,
    WatchlistFullError,
)


class TestRead:
    async def test_returns_seeded_tickers_in_insertion_order(self, repo: Repository) -> None:
        assert await repo.get_watchlist() == list(DEFAULT_WATCHLIST)

    async def test_returns_plain_strings(self, repo: Repository) -> None:
        assert all(isinstance(t, str) for t in await repo.get_watchlist())


class TestAdd:
    async def test_add_appends_and_returns_full_list(self, repo: Repository) -> None:
        result = await repo.add_to_watchlist("PYPL")
        assert result == [*DEFAULT_WATCHLIST, "PYPL"]
        assert await repo.get_watchlist() == result

    async def test_add_normalizes(self, repo: Repository) -> None:
        assert (await repo.add_to_watchlist("  pypl "))[-1] == "PYPL"

    async def test_add_existing_is_idempotent(self, repo: Repository) -> None:
        result = await repo.add_to_watchlist("AAPL")
        assert result == list(DEFAULT_WATCHLIST)

    async def test_add_existing_lowercase_is_idempotent(self, repo: Repository) -> None:
        assert await repo.add_to_watchlist("aapl") == list(DEFAULT_WATCHLIST)

    @pytest.mark.parametrize("ticker", ["", "TOOLONG", "AA1", "A-B", "  ", "BANANA"])
    async def test_malformed_symbols_are_refused(self, repo: Repository, ticker: str) -> None:
        with pytest.raises(InvalidTickerError) as exc:
            await repo.add_to_watchlist(ticker)
        assert exc.value.code == "INVALID_TICKER"
        assert exc.value.http_status == 400

    async def test_invalid_add_leaves_watchlist_untouched(self, repo: Repository) -> None:
        with pytest.raises(InvalidTickerError):
            await repo.add_to_watchlist("NOPE!")
        assert await repo.get_watchlist() == list(DEFAULT_WATCHLIST)

    async def test_single_letter_ticker_allowed(self, repo: Repository) -> None:
        assert "F" in await repo.add_to_watchlist("F")

    async def test_five_letter_ticker_allowed(self, repo: Repository) -> None:
        assert "GOOGL" in await repo.add_to_watchlist("GOOGL")

    async def test_fills_to_the_cap(self, repo: Repository) -> None:
        fillers = [
            "AA",
            "AB",
            "AC",
            "AD",
            "AE",
            "AF",
            "AG",
            "AH",
            "AI",
            "AJ",
            "AK",
            "AL",
            "AM",
            "AN",
            "AO",
            "AP",
            "AQ",
            "AR",
            "AS",
            "AT",
        ]
        for ticker in fillers:
            result = await repo.add_to_watchlist(ticker)
        assert len(result) == MAX_WATCHLIST_SIZE

    async def test_beyond_the_cap_is_refused(self, repo: Repository) -> None:
        for i in range(MAX_WATCHLIST_SIZE - len(DEFAULT_WATCHLIST)):
            await repo.add_to_watchlist(f"Z{chr(ord('A') + i)}")
        with pytest.raises(WatchlistFullError) as exc:
            await repo.add_to_watchlist("PYPL")
        assert exc.value.code == "WATCHLIST_FULL"
        assert exc.value.http_status == 409
        assert str(MAX_WATCHLIST_SIZE) in exc.value.message

    async def test_re_adding_an_existing_ticker_at_the_cap_still_succeeds(
        self, repo: Repository
    ) -> None:
        # Idempotency is checked before capacity, so a full watchlist does not
        # start rejecting no-op adds -- which the LLM will make routinely.
        for i in range(MAX_WATCHLIST_SIZE - len(DEFAULT_WATCHLIST)):
            await repo.add_to_watchlist(f"Z{chr(ord('A') + i)}")
        assert len(await repo.add_to_watchlist("AAPL")) == MAX_WATCHLIST_SIZE

    async def test_full_watchlist_rejection_writes_nothing(self, repo: Repository) -> None:
        for i in range(MAX_WATCHLIST_SIZE - len(DEFAULT_WATCHLIST)):
            await repo.add_to_watchlist(f"Z{chr(ord('A') + i)}")
        with pytest.raises(WatchlistFullError):
            await repo.add_to_watchlist("PYPL")
        assert len(await repo.get_watchlist()) == MAX_WATCHLIST_SIZE


class TestRemove:
    async def test_remove_returns_full_new_list(self, repo: Repository) -> None:
        result = await repo.remove_from_watchlist("TSLA")
        assert "TSLA" not in result
        assert len(result) == len(DEFAULT_WATCHLIST) - 1
        assert await repo.get_watchlist() == result

    async def test_remove_preserves_order_of_the_rest(self, repo: Repository) -> None:
        result = await repo.remove_from_watchlist("MSFT")
        assert result == [t for t in DEFAULT_WATCHLIST if t != "MSFT"]

    async def test_remove_normalizes(self, repo: Repository) -> None:
        assert "TSLA" not in await repo.remove_from_watchlist(" tsla ")

    async def test_remove_absent_is_idempotent(self, repo: Repository) -> None:
        assert await repo.remove_from_watchlist("PYPL") == list(DEFAULT_WATCHLIST)

    async def test_remove_twice_is_idempotent(self, repo: Repository) -> None:
        first = await repo.remove_from_watchlist("V")
        assert await repo.remove_from_watchlist("V") == first

    async def test_malformed_symbol_is_refused(self, repo: Repository) -> None:
        with pytest.raises(InvalidTickerError):
            await repo.remove_from_watchlist("NOT-A-TICKER")

    async def test_remove_all_leaves_empty_list(self, repo: Repository) -> None:
        for ticker in DEFAULT_WATCHLIST:
            await repo.remove_from_watchlist(ticker)
        assert await repo.get_watchlist() == []

    async def test_removing_does_not_touch_positions(self, repo: Repository) -> None:
        await repo.execute_trade("AAPL", "buy", 1, 100.0)
        await repo.remove_from_watchlist("AAPL")
        assert await repo.get_position("AAPL") is not None

    async def test_re_add_after_remove_goes_to_the_end(self, repo: Repository) -> None:
        await repo.remove_from_watchlist("AAPL")
        assert (await repo.add_to_watchlist("AAPL"))[-1] == "AAPL"
