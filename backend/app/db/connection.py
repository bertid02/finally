"""SQLite connection management and lazy initialization.

## Why one connection behind a lock, and not a pool

FinAlly is single-user by construction (PLAN.md section 3: "no auth = no
multi-user = no need for a database server"). Every query here touches at most a
few dozen rows and returns in well under a millisecond. A pool would buy
concurrency that no caller can use and would cost the one thing that actually
matters at this size: a simple, obviously-correct transaction boundary for
`execute_trade`, which must write four tables atomically.

So: one `sqlite3.Connection` per `Database`, opened lazily, `check_same_thread=
False`, and every access serialized through a `threading.Lock`. Reads queue
behind writes, which is fine when writes are microseconds long.

## Why `asyncio.to_thread` and not aiosqlite

The app is async and `sqlite3` is blocking, so calls must leave the event loop or
the SSE stream stutters. `asyncio.to_thread` gets that with the standard library.
aiosqlite would add a dependency for the same behaviour it implements internally
(a worker thread per connection), and the db-engineer does not own
`pyproject.toml` -- avoiding a dependency request is a real saving here, not a
principled stand.

The lock is a *threading* lock, not an asyncio one, precisely because the work
happens on worker threads: an asyncio lock held across `to_thread` would not
protect the connection from a second worker.

## Lazy initialization

PLAN.md section 7: no migration step. `initialize()` creates the schema and seeds
defaults if they are missing, and is safe to call on every startup and on every
request. It is guarded so that concurrent first requests initialize once.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar
from uuid import uuid4

from .schema import (
    DEFAULT_CASH_BALANCE,
    DEFAULT_USER_ID,
    DEFAULT_WATCHLIST,
    SCHEMA_SQL,
)

T = TypeVar("T")

# Overridden by FINALLY_DB_PATH. The container mounts its volume at /app/db and
# sets the variable; this relative default is what a developer running uvicorn
# from the repo root gets.
DEFAULT_DB_PATH = "db/finally.db"

_MEMORY = ":memory:"


def utc_now_iso() -> str:
    """ISO-8601 UTC with a 'Z' suffix.

    Every timestamp column in the schema uses this format, which matters more
    than it looks: they are TEXT columns, so ORDER BY is a string comparison, and
    only a fixed-width UTC representation sorts chronologically.
    """
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def new_id() -> str:
    """A UUID4 primary key, as the schema specifies."""
    return str(uuid4())


class Database:
    """A lazily-initialized SQLite database behind a serializing lock.

    Not a singleton by design -- tests construct their own instances against
    temporary files. `get_database()` provides the process-wide one the app uses.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        raw = str(path) if path is not None else os.getenv("FINALLY_DB_PATH", DEFAULT_DB_PATH)
        self.path: str = raw if raw == _MEMORY else str(Path(raw).expanduser())
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._init_lock = asyncio.Lock()
        self._initialized = False

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _connection(self) -> sqlite3.Connection:
        """Open on first use. Callers must already hold `self._lock`."""
        if self._conn is None:
            if self.path != _MEMORY:
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
            conn.row_factory = sqlite3.Row
            # Autocommit. Transactions are opened explicitly with BEGIN IMMEDIATE
            # so the one place that needs atomicity says so out loud, rather than
            # relying on sqlite3's implicit-BEGIN heuristics (which do not cover
            # SELECTs, and so would not protect a read-then-write trade).
            conn.isolation_level = None
            if self.path != _MEMORY:
                conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            self._conn = conn
        return self._conn

    def run_sync(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Run `fn` against the connection with the lock held. Blocking."""
        with self._lock:
            return fn(self._connection())

    async def run(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Run `fn` on a worker thread so the event loop keeps serving SSE."""
        return await asyncio.to_thread(self.run_sync, fn)

    def transaction_sync(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Run `fn` inside BEGIN IMMEDIATE ... COMMIT, rolling back on any exception.

        IMMEDIATE (not DEFERRED) takes the write lock up front, so a transaction
        that reads a position and then writes it cannot be beaten to the write by
        another connection between the two.

        Any exception -- a domain error like InsufficientCashError, or a bug -- rolls
        the whole thing back and propagates. Nothing partially applies, which is
        what makes a four-table trade safe to describe as one operation.
        """
        with self._lock:
            conn = self._connection()
            conn.execute("BEGIN IMMEDIATE")
            try:
                result = fn(conn)
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
            return result

    async def transaction(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """`transaction_sync` on a worker thread."""
        return await asyncio.to_thread(self.transaction_sync, fn)

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Create the schema and seed defaults if absent. Idempotent and concurrency-safe.

        The asyncio lock matters on a cold start: FastAPI can be handling several
        first requests at once, and without it each would try to seed.
        """
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            await asyncio.to_thread(self._initialize_sync)
            self._initialized = True

    def _initialize_sync(self) -> None:
        # The DDL runs outside the transaction: executescript() commits any open
        # transaction before it starts, so wrapping it would silently end the one
        # the seed needs. Two steps, each atomic on its own -- and CREATE TABLE IF
        # NOT EXISTS makes a repeat of the first step a no-op anyway.
        with self._lock:
            self._connection().executescript(SCHEMA_SQL)
        self.transaction_sync(self._seed)

    @staticmethod
    def _seed(conn: sqlite3.Connection) -> None:
        """Insert the default profile and watchlist if they are not already there.

        `INSERT OR IGNORE` against the primary key and the (user_id, ticker)
        UNIQUE constraint does the whole job: a database that has been used
        before -- cash spent, tickers removed -- is left exactly as the user left
        it. Re-seeding a removed ticker would be worse than not seeding at all.
        """
        now = utc_now_iso()
        cur = conn.execute("SELECT COUNT(*) AS n FROM users_profile")
        already_seeded = cur.fetchone()["n"] > 0

        conn.execute(
            "INSERT OR IGNORE INTO users_profile (id, cash_balance, created_at) VALUES (?, ?, ?)",
            (DEFAULT_USER_ID, DEFAULT_CASH_BALANCE, now),
        )
        if already_seeded:
            # Only a brand-new database gets the default watchlist. Otherwise a
            # user who deliberately removed TSLA would find it back after every
            # restart.
            return
        conn.executemany(
            "INSERT OR IGNORE INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
            [(new_id(), DEFAULT_USER_ID, ticker, now) for ticker in DEFAULT_WATCHLIST],
        )

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the connection. Idempotent; the next call reopens it."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
        self._initialized = False


_database: Database | None = None
_database_lock = threading.Lock()


def get_database() -> Database:
    """The process-wide Database, created on first call.

    A module-level singleton rather than a FastAPI dependency because the
    background market-data task and the LLM chat flow both need it outside a
    request scope. `set_database()` exists so tests and startup code can point it
    somewhere else.
    """
    global _database
    with _database_lock:
        if _database is None:
            _database = Database()
        return _database


def set_database(db: Database | None) -> None:
    """Replace the process-wide Database. Pass None to reset."""
    global _database
    with _database_lock:
        _database = db
