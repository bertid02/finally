"""Database subsystem for FinAlly.

Everything an upstream caller needs is re-exported here, so routes and the chat
flow import from `app.db` and never from a submodule -- the internal layout stays
free to change.

Typical use:

    from app.db import Repository, get_database

    repo = Repository()            # uses the process-wide Database
    await repo.initialize()        # lazy schema + seed; safe every startup
    portfolio = await repo.get_portfolio()

Public API:
    Database, get_database, set_database - connection management
    Repository                           - all reads and writes
    Portfolio, Position, Trade, TradeResult, PortfolioSnapshot, ChatMessage
    DatabaseError and subclasses         - typed failures carrying PLAN.md
                                           section 8 codes and messages
"""

from .connection import Database, get_database, new_id, set_database, utc_now_iso
from .exceptions import (
    DatabaseError,
    InsufficientCashError,
    InsufficientSharesError,
    InvalidQuantityError,
    InvalidSideError,
    InvalidTickerError,
    UnknownTickerError,
    WatchlistFullError,
)
from .models import (
    ChatMessage,
    Portfolio,
    PortfolioSnapshot,
    Position,
    Trade,
    TradeResult,
)
from .repository import Repository
from .schema import (
    DEFAULT_CASH_BALANCE,
    DEFAULT_USER_ID,
    DEFAULT_WATCHLIST,
    MAX_WATCHLIST_SIZE,
    QUANTITY_EPSILON,
)

__all__ = [
    "Database",
    "get_database",
    "set_database",
    "new_id",
    "utc_now_iso",
    "Repository",
    "Portfolio",
    "Position",
    "Trade",
    "TradeResult",
    "PortfolioSnapshot",
    "ChatMessage",
    "DatabaseError",
    "InvalidQuantityError",
    "InvalidSideError",
    "InvalidTickerError",
    "UnknownTickerError",
    "InsufficientCashError",
    "InsufficientSharesError",
    "WatchlistFullError",
    "DEFAULT_USER_ID",
    "DEFAULT_CASH_BALANCE",
    "DEFAULT_WATCHLIST",
    "MAX_WATCHLIST_SIZE",
    "QUANTITY_EPSILON",
]
