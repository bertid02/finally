"""Schema creation and seeding."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db import (
    DEFAULT_CASH_BALANCE,
    DEFAULT_USER_ID,
    DEFAULT_WATCHLIST,
    Database,
    Repository,
)

TABLES = (
    "users_profile",
    "watchlist",
    "positions",
    "trades",
    "portfolio_snapshots",
    "chat_messages",
)


def _table_names(db: Database) -> set[str]:
    return db.run_sync(
        lambda c: {
            r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    )


class TestSchemaCreation:
    async def test_creates_all_six_tables(self, raw_repo: Repository) -> None:
        await raw_repo.initialize()
        assert TABLES == tuple(t for t in TABLES if t in _table_names(raw_repo.db))

    async def test_creates_parent_directory(self, db_path: Path, raw_repo: Repository) -> None:
        assert not db_path.parent.exists()
        await raw_repo.initialize()
        assert db_path.exists()

    async def test_indexes_exist(self, raw_repo: Repository) -> None:
        await raw_repo.initialize()
        names = raw_repo.db.run_sync(
            lambda c: {
                r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
            }
        )
        assert {"idx_trades_user_time", "idx_snapshots_user_time", "idx_chat_user_time"} <= names

    async def test_wal_mode_on_file_database(self, raw_repo: Repository) -> None:
        await raw_repo.initialize()
        mode = raw_repo.db.run_sync(lambda c: c.execute("PRAGMA journal_mode").fetchone()[0])
        assert mode == "wal"

    async def test_side_check_constraint_rejects_junk(self, repo: Repository) -> None:
        import sqlite3

        with pytest.raises(sqlite3.IntegrityError):
            repo.db.run_sync(
                lambda c: c.execute(
                    "INSERT INTO trades (id, user_id, ticker, side, quantity, price, executed_at)"
                    " VALUES ('x', 'default', 'AAPL', 'hodl', 1, 1, 'now')"
                )
            )

    async def test_role_check_constraint_rejects_junk(self, repo: Repository) -> None:
        import sqlite3

        with pytest.raises(sqlite3.IntegrityError):
            repo.db.run_sync(
                lambda c: c.execute(
                    "INSERT INTO chat_messages (id, user_id, role, content, created_at)"
                    " VALUES ('x', 'default', 'system', 'hi', 'now')"
                )
            )

    async def test_watchlist_unique_constraint(self, repo: Repository) -> None:
        import sqlite3

        with pytest.raises(sqlite3.IntegrityError):
            repo.db.run_sync(
                lambda c: c.execute(
                    "INSERT INTO watchlist (id, user_id, ticker, added_at)"
                    " VALUES ('x', 'default', 'AAPL', 'now')"
                )
            )

    async def test_positions_unique_constraint(self, repo: Repository) -> None:
        import sqlite3

        await repo.execute_trade("AAPL", "buy", 1, 100.0)
        with pytest.raises(sqlite3.IntegrityError):
            repo.db.run_sync(
                lambda c: c.execute(
                    "INSERT INTO positions (id, user_id, ticker, quantity, avg_cost, updated_at)"
                    " VALUES ('x', 'default', 'AAPL', 1, 1, 'now')"
                )
            )


class TestSeeding:
    async def test_seeds_default_profile(self, repo: Repository) -> None:
        assert await repo.get_cash_balance() == DEFAULT_CASH_BALANCE

    async def test_seeds_default_watchlist_in_order(self, repo: Repository) -> None:
        assert await repo.get_watchlist() == list(DEFAULT_WATCHLIST)

    async def test_seeds_ten_tickers(self, repo: Repository) -> None:
        assert len(DEFAULT_WATCHLIST) == 10

    async def test_no_positions_or_trades_on_fresh_database(self, repo: Repository) -> None:
        assert await repo.get_positions() == []
        assert await repo.get_trades() == []
        assert await repo.get_portfolio_history() == []
        assert await repo.get_chat_messages() == []

    async def test_initialize_is_idempotent(self, repo: Repository) -> None:
        await repo.initialize()
        await repo.initialize()
        assert await repo.get_watchlist() == list(DEFAULT_WATCHLIST)
        assert await repo.get_cash_balance() == DEFAULT_CASH_BALANCE

    async def test_reopening_preserves_user_state(self, db_path: Path) -> None:
        first = Repository(Database(db_path))
        await first.initialize()
        await first.remove_from_watchlist("TSLA")
        await first.execute_trade("AAPL", "buy", 5, 100.0)
        first.db.close()

        second = Repository(Database(db_path))
        await second.initialize()
        assert "TSLA" not in await second.get_watchlist()
        assert await second.get_cash_balance() == 9500.0
        second.db.close()

    async def test_reseeding_does_not_resurrect_removed_tickers(self, repo: Repository) -> None:
        await repo.remove_from_watchlist("NVDA")
        repo.db._initialized = False  # force the seed path to run again
        await repo.initialize()
        assert "NVDA" not in await repo.get_watchlist()

    async def test_seed_restores_missing_profile_row(self, repo: Repository) -> None:
        # The profile is the one row nothing can work without; INSERT OR IGNORE
        # puts it back even on a database that has been used before.
        repo.db.run_sync(lambda c: c.execute("DELETE FROM users_profile"))
        repo.db._initialized = False
        await repo.initialize()
        assert await repo.get_cash_balance() == DEFAULT_CASH_BALANCE

    async def test_default_user_id_constant(self) -> None:
        assert DEFAULT_USER_ID == "default"
