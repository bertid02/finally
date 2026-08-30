"""Schema DDL and seed data for the FinAlly SQLite database.

The DDL lives in a Python constant rather than a `.sql` file so the schema
travels with the package under any packaging or working-directory arrangement --
the container runs uvicorn from `/app`, tests run from `backend/`, and a missing
data file would fail only at runtime in one of them.

Every statement is `IF NOT EXISTS`, so `initialize()` is safe to run against a
database that is already current. There is no migration story by design
(PLAN.md section 7): a fresh Docker volume starts empty and gets seeded, and an
existing one is already correct.
"""

from __future__ import annotations

DEFAULT_USER_ID = "default"
DEFAULT_CASH_BALANCE = 10_000.0

# PLAN.md section 7. Order matters for the demo's first impression, so the
# watchlist is seeded (and read back) in this sequence.
DEFAULT_WATCHLIST: tuple[str, ...] = (
    "AAPL",
    "GOOGL",
    "MSFT",
    "AMZN",
    "TSLA",
    "NVDA",
    "META",
    "JPM",
    "V",
    "NFLX",
)

MAX_WATCHLIST_SIZE = 30

# Quantities below this are floating-point residue from a full sell, not a
# holding. PLAN.md section 7: treat as zero and delete the row.
QUANTITY_EPSILON = 1e-9

SCHEMA_SQL = """
-- Single-user today. Every table carries user_id defaulting to 'default' so a
-- future multi-user mode is a code change, not a migration (PLAN.md section 7).

CREATE TABLE IF NOT EXISTS users_profile (
    id           TEXT PRIMARY KEY,
    cash_balance REAL NOT NULL DEFAULT 10000.0,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist (
    id       TEXT PRIMARY KEY,
    user_id  TEXT NOT NULL DEFAULT 'default',
    ticker   TEXT NOT NULL,
    added_at TEXT NOT NULL,
    UNIQUE (user_id, ticker)
);

-- One row per held ticker. A position sold to zero is DELETED, never left at
-- quantity 0 -- the positions table and heatmap show holdings only.
CREATE TABLE IF NOT EXISTS positions (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL DEFAULT 'default',
    ticker     TEXT NOT NULL,
    quantity   REAL NOT NULL,
    avg_cost   REAL NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (user_id, ticker)
);

-- Append-only. Realized P&L is derived from this log; it is never stored.
CREATE TABLE IF NOT EXISTS trades (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT 'default',
    ticker      TEXT NOT NULL,
    side        TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    quantity    REAL NOT NULL,
    price       REAL NOT NULL,
    executed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trades_user_time
    ON trades (user_id, executed_at);

-- Written on trade execution only -- there is no periodic snapshot task
-- (PLAN.md section 7), so this series is sparse and the frontend joins it with
-- the live stream to draw the current segment of the curve.
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT 'default',
    total_value REAL NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_user_time
    ON portfolio_snapshots (user_id, recorded_at);

CREATE TABLE IF NOT EXISTS chat_messages (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL DEFAULT 'default',
    role       TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content    TEXT NOT NULL,
    actions    TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_user_time
    ON chat_messages (user_id, created_at);
"""
