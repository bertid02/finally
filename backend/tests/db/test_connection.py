"""Connection management, transaction boundaries, and the module singleton."""

from __future__ import annotations

import asyncio
import re
import sqlite3
from pathlib import Path

import pytest

from app.db import Database, Repository, get_database, new_id, set_database, utc_now_iso
from app.db.connection import DEFAULT_DB_PATH


class TestTimestamps:
    def test_utc_now_iso_is_zulu(self) -> None:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", utc_now_iso())

    def test_timestamps_sort_chronologically_as_text(self) -> None:
        first = utc_now_iso()
        second = utc_now_iso()
        assert first <= second

    def test_new_id_is_a_uuid(self) -> None:
        assert re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", new_id())

    def test_new_ids_are_unique(self) -> None:
        assert len({new_id() for _ in range(1000)}) == 1000


class TestPathResolution:
    def test_env_var_wins(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        target = tmp_path / "from-env.db"
        monkeypatch.setenv("FINALLY_DB_PATH", str(target))
        assert Database().path == str(target)

    def test_falls_back_to_default_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FINALLY_DB_PATH", raising=False)
        assert Database().path == str(Path(DEFAULT_DB_PATH))

    def test_explicit_path_beats_env_var(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("FINALLY_DB_PATH", "/nope/should-not-be-used.db")
        assert Database(tmp_path / "explicit.db").path == str(tmp_path / "explicit.db")

    def test_expands_user_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        assert Database("~/finally.db").path == str(tmp_path / "finally.db")

    def test_memory_path_is_left_alone(self) -> None:
        assert Database(":memory:").path == ":memory:"


class TestMemoryDatabase:
    async def test_memory_database_works_end_to_end(self) -> None:
        repo = Repository(Database(":memory:"))
        await repo.initialize()
        result = await repo.execute_trade("AAPL", "buy", 2, 50.0)
        assert result.cash_balance == 9900.0
        repo.db.close()

    async def test_memory_database_survives_across_worker_threads(self) -> None:
        # asyncio.to_thread hands work to whichever pool thread is free, so a
        # connection opened on one thread must be usable from another. This is
        # the check that check_same_thread=False is actually doing its job.
        repo = Repository(Database(":memory:"))
        await repo.initialize()
        await asyncio.gather(*(repo.get_cash_balance() for _ in range(20)))
        repo.db.close()

    async def test_memory_database_skips_wal(self) -> None:
        db = Database(":memory:")
        await db.initialize()
        assert db.run_sync(lambda c: c.execute("PRAGMA journal_mode").fetchone()[0]) == "memory"
        db.close()


class TestConnectionLifecycle:
    async def test_connection_is_reused(self, db: Database) -> None:
        await db.initialize()
        first = db.run_sync(lambda c: c)
        second = db.run_sync(lambda c: c)
        assert first is second

    async def test_close_is_idempotent(self, db: Database) -> None:
        await db.initialize()
        db.close()
        db.close()

    async def test_close_then_reuse_reopens(self, db: Database) -> None:
        repo = Repository(db)
        await repo.initialize()
        db.close()
        await repo.initialize()
        assert await repo.get_cash_balance() == 10000.0

    async def test_close_resets_initialized_flag(self, db: Database) -> None:
        await db.initialize()
        assert db._initialized
        db.close()
        assert not db._initialized

    async def test_foreign_keys_pragma_on(self, db: Database) -> None:
        await db.initialize()
        assert db.run_sync(lambda c: c.execute("PRAGMA foreign_keys").fetchone()[0]) == 1

    async def test_row_factory_gives_named_access(self, db: Database) -> None:
        await db.initialize()
        row = db.run_sync(lambda c: c.execute("SELECT cash_balance FROM users_profile").fetchone())
        assert row["cash_balance"] == 10000.0

    async def test_concurrent_initialize_seeds_once(self, db: Database) -> None:
        await asyncio.gather(*(db.initialize() for _ in range(10)))
        count = db.run_sync(lambda c: c.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0])
        assert count == 10


class TestTransactions:
    async def test_commit_persists(self, db: Database) -> None:
        await db.initialize()
        await db.transaction(
            lambda c: c.execute("UPDATE users_profile SET cash_balance = 42 WHERE id = 'default'")
        )
        assert (
            db.run_sync(lambda c: c.execute("SELECT cash_balance FROM users_profile").fetchone()[0])
            == 42
        )

    async def test_exception_rolls_back(self, db: Database) -> None:
        await db.initialize()

        def _boom(conn: sqlite3.Connection) -> None:
            conn.execute("UPDATE users_profile SET cash_balance = 42 WHERE id = 'default'")
            raise RuntimeError("nope")

        with pytest.raises(RuntimeError):
            await db.transaction(_boom)

        assert (
            db.run_sync(lambda c: c.execute("SELECT cash_balance FROM users_profile").fetchone()[0])
            == 10000.0
        )

    async def test_base_exception_also_rolls_back(self, db: Database) -> None:
        # Cancellation is a BaseException, and a trade cancelled mid-transaction
        # must not leave half a trade behind.
        await db.initialize()

        def _cancel(conn: sqlite3.Connection) -> None:
            conn.execute("DELETE FROM watchlist")
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            await db.transaction(_cancel)

        assert (
            db.run_sync(lambda c: c.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]) == 10
        )

    async def test_transaction_returns_value(self, db: Database) -> None:
        await db.initialize()
        assert await db.transaction(lambda c: "done") == "done"

    async def test_transaction_sync_available(self, db: Database) -> None:
        await db.initialize()
        assert db.transaction_sync(lambda c: 7) == 7

    async def test_isolation_level_is_manual(self, db: Database) -> None:
        await db.initialize()
        assert db.run_sync(lambda c: c.isolation_level) is None


class TestSingleton:
    def test_get_database_returns_same_instance(self) -> None:
        set_database(None)
        try:
            assert get_database() is get_database()
        finally:
            set_database(None)

    def test_set_database_overrides(self) -> None:
        replacement = Database(":memory:")
        set_database(replacement)
        try:
            assert get_database() is replacement
        finally:
            set_database(None)
            replacement.close()

    def test_set_none_resets(self) -> None:
        replacement = Database(":memory:")
        set_database(replacement)
        set_database(None)
        try:
            assert get_database() is not replacement
        finally:
            set_database(None)

    def test_repository_defaults_to_singleton(self) -> None:
        replacement = Database(":memory:")
        set_database(replacement)
        try:
            assert Repository().db is replacement
        finally:
            set_database(None)
            replacement.close()
