"""Repository layer -- the only code in FinAlly that writes SQL.

Everything is async and returns domain models from `app/db/models.py`, never raw
`sqlite3.Row` objects, so no caller ends up depending on a column name.

Two rules shape the whole module:

1. **The repository never learns a price on its own.** Fill prices and valuation
   prices are arguments. The price cache belongs to `app/market/`, and a database
   that reached into it would make `execute_trade` untestable without a running
   market feed -- and would give the codebase a second opinion about what a
   ticker is worth.
2. **Every deliberate failure is a typed exception from `exceptions.py`.** They
   carry the PLAN.md section 8 error code and user-facing message, so the HTTP
   route and the LLM chat turn report the same failure in the same words.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections.abc import Mapping

from .connection import Database, get_database, new_id, utc_now_iso
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
from .models import ChatMessage, Portfolio, PortfolioSnapshot, Position, Trade, TradeResult
from .schema import DEFAULT_USER_ID, MAX_WATCHLIST_SIZE, QUANTITY_EPSILON

# Same rule as PLAN.md section 8 and app/market/interface.py's TICKER_PATTERN.
# Duplicated rather than imported: importing app.market pulls numpy and the
# Massive SDK into every context that touches the database, including tests that
# have no business needing them.
_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")

_SIDES = ("buy", "sell")

# Cash and prices are money: two decimals, the way they are displayed. avg_cost
# gets six, because it is a derived ratio that will be multiplied by a quantity
# again -- rounding a cost basis to cents compounds a visible error into the P&L
# after a few partial buys.
_MONEY_DP = 2
_COST_DP = 6

_HISTORY_DEFAULT_LIMIT = 500
_HISTORY_MAX_LIMIT = 5000
_CHAT_DEFAULT_LIMIT = 50


def _normalize_ticker(ticker: str) -> str:
    """Trim and uppercase. Mirrors `app.market.normalize_ticker`."""
    return ticker.strip().upper()


def _validate_ticker(ticker: str) -> str:
    """Normalize and format-check a symbol, or raise InvalidTickerError."""
    symbol = _normalize_ticker(ticker)
    if not _TICKER_RE.match(symbol):
        raise InvalidTickerError(f"'{ticker.strip()}' is not a valid ticker symbol (1-5 letters).")
    return symbol


def _validate_side(side: str) -> str:
    """Normalize and check a trade side, or raise InvalidSideError."""
    normalized = side.strip().lower()
    if normalized not in _SIDES:
        raise InvalidSideError(f"'{side}' is not a valid side. Use 'buy' or 'sell'.")
    return normalized


def _validate_quantity(quantity: float) -> float:
    """Check a quantity is a positive finite number, or raise InvalidQuantityError."""
    try:
        value = float(quantity)
    except (TypeError, ValueError) as exc:
        raise InvalidQuantityError(f"'{quantity}' is not a valid quantity.") from exc
    if math.isnan(value) or math.isinf(value) or value <= 0:
        raise InvalidQuantityError("Quantity must be a positive number.")
    return value


def _fmt_qty(quantity: float) -> str:
    """Render a share count for a user-facing message without trailing zeros."""
    return f"{quantity:,.4f}".rstrip("0").rstrip(".")


def _row_to_position(row: sqlite3.Row) -> Position:
    return Position(ticker=row["ticker"], quantity=row["quantity"], avg_cost=row["avg_cost"])


def _row_to_trade(row: sqlite3.Row) -> Trade:
    return Trade(
        id=row["id"],
        ticker=row["ticker"],
        side=row["side"],
        quantity=row["quantity"],
        price=row["price"],
        executed_at=row["executed_at"],
    )


class Repository:
    """Async data access for one FinAlly database.

    `user_id` is a parameter on every method and defaults to "default". It is
    hardcoded everywhere upstream today (PLAN.md section 7); carrying it here
    costs nothing and means multi-user support never has to rewrite this file.
    """

    def __init__(self, db: Database | None = None, user_id: str = DEFAULT_USER_ID) -> None:
        self._db = db if db is not None else get_database()
        self.user_id = user_id

    @property
    def db(self) -> Database:
        return self._db

    async def initialize(self) -> None:
        """Create and seed the database if needed. Safe to call repeatedly."""
        await self._db.initialize()

    # ==================================================================
    # Portfolio reads
    # ==================================================================

    async def get_cash_balance(self, user_id: str | None = None) -> float:
        """Cash available to spend."""
        uid = user_id or self.user_id
        return await self._db.run(lambda c: self._cash_balance(c, uid))

    @staticmethod
    def _cash_balance(conn: sqlite3.Connection, user_id: str) -> float:
        row = conn.execute(
            "SELECT cash_balance FROM users_profile WHERE id = ?", (user_id,)
        ).fetchone()
        if row is None:
            # initialize() guarantees this row. Reaching here means the database
            # was created some other way, and silently inventing $10,000 would
            # hide that.
            raise DatabaseError(f"No profile found for user '{user_id}'. Database not initialized.")
        return row["cash_balance"]

    async def get_positions(self, user_id: str | None = None) -> list[Position]:
        """All held positions, alphabetically by ticker."""
        uid = user_id or self.user_id
        return await self._db.run(lambda c: self._positions(c, uid))

    @staticmethod
    def _positions(conn: sqlite3.Connection, user_id: str) -> list[Position]:
        rows = conn.execute(
            "SELECT ticker, quantity, avg_cost FROM positions WHERE user_id = ? ORDER BY ticker",
            (user_id,),
        ).fetchall()
        return [_row_to_position(r) for r in rows]

    async def get_position(self, ticker: str, user_id: str | None = None) -> Position | None:
        """One position, or None if not held."""
        uid = user_id or self.user_id
        symbol = _validate_ticker(ticker)
        return await self._db.run(lambda c: self._position(c, uid, symbol))

    @staticmethod
    def _position(conn: sqlite3.Connection, user_id: str, ticker: str) -> Position | None:
        row = conn.execute(
            "SELECT ticker, quantity, avg_cost FROM positions WHERE user_id = ? AND ticker = ?",
            (user_id, ticker),
        ).fetchone()
        return _row_to_position(row) if row else None

    async def get_portfolio(self, user_id: str | None = None) -> Portfolio:
        """Cash and positions in one read.

        One database round trip, one consistent view -- two separate calls could
        straddle a trade and report cash from before it with positions from after.
        """
        uid = user_id or self.user_id

        def _read(conn: sqlite3.Connection) -> Portfolio:
            return Portfolio(
                cash_balance=self._cash_balance(conn, uid),
                positions=self._positions(conn, uid),
            )

        return await self._db.run(_read)

    # ==================================================================
    # Trade execution
    # ==================================================================

    async def execute_trade(
        self,
        ticker: str,
        side: str,
        quantity: float,
        price: float | None,
        market_prices: Mapping[str, float] | None = None,
        user_id: str | None = None,
    ) -> TradeResult:
        """Execute a market order atomically and return everything it changed.

        `price` is the fill price, which the caller reads from the price cache at
        the moment of execution -- never a price supplied by an HTTP client
        (PLAN.md section 8). Pass None when the cache has no price for the symbol;
        that is a normal condition under Massive's 15s poll and raises
        UnknownTickerError rather than filling at zero.

        `market_prices` values the *other* positions for the snapshot written to
        the P&L curve. Any position missing from it is valued at its own
        `avg_cost`, which keeps the curve continuous instead of putting a hole in
        it; the traded ticker always uses `price`.

        All four writes -- trade, position, cash, snapshot -- happen inside one
        BEGIN IMMEDIATE transaction. Any failure rolls back the lot.

        Raises:
            InvalidTickerError, InvalidSideError, InvalidQuantityError -- malformed request
            UnknownTickerError      -- `price` is None
            InsufficientCashError   -- buy cost exceeds cash
            InsufficientSharesError -- sell quantity exceeds holding
        """
        uid = user_id or self.user_id
        symbol = _validate_ticker(ticker)
        normalized_side = _validate_side(side)
        qty = _validate_quantity(quantity)

        # A None price is the cache saying "not yet" -- and so is a zero or a NaN,
        # which is what a half-parsed provider response looks like. All three mean
        # the same thing to the user: this cannot be filled right now.
        if price is None or not math.isfinite(price) or price <= 0:
            raise UnknownTickerError(f"No price available for {symbol} yet. Try again in a moment.")
        fill_price = float(price)

        prices = dict(market_prices) if market_prices else {}

        def _apply(conn: sqlite3.Connection) -> TradeResult:
            cash = self._cash_balance(conn, uid)
            existing = self._position(conn, uid, symbol)
            now = utc_now_iso()

            if normalized_side == "buy":
                new_cash, position = self._apply_buy(
                    conn, uid, symbol, qty, fill_price, cash, existing, now
                )
            else:
                new_cash, position = self._apply_sell(
                    conn, uid, symbol, qty, fill_price, cash, existing, now
                )

            trade = Trade(
                id=new_id(),
                ticker=symbol,
                side=normalized_side,
                quantity=qty,
                price=fill_price,
                executed_at=now,
            )
            conn.execute(
                "INSERT INTO trades (id, user_id, ticker, side, quantity, price, executed_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (trade.id, uid, symbol, normalized_side, qty, fill_price, now),
            )
            conn.execute(
                "UPDATE users_profile SET cash_balance = ? WHERE id = ?",
                (new_cash, uid),
            )

            # Snapshot AFTER the position writes, so the curve records the
            # portfolio the user now has, not the one they had a moment ago.
            valuation = dict(prices)
            valuation[symbol] = fill_price
            total_value = self._total_value(conn, uid, new_cash, valuation)
            conn.execute(
                "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at)"
                " VALUES (?, ?, ?, ?)",
                (new_id(), uid, total_value, now),
            )

            return TradeResult(trade=trade, cash_balance=new_cash, position=position)

        return await self._db.transaction(_apply)

    @staticmethod
    def _apply_buy(
        conn: sqlite3.Connection,
        user_id: str,
        ticker: str,
        qty: float,
        price: float,
        cash: float,
        existing: Position | None,
        now: str,
    ) -> tuple[float, Position]:
        """Debit cash and fold the fill into the weighted-average cost basis."""
        cost = round(qty * price, _MONEY_DP)
        # The epsilon lets a user spend their balance to the last cent without a
        # float representation error refusing the trade.
        if cost > cash + QUANTITY_EPSILON:
            raise InsufficientCashError(f"Insufficient cash: need ${cost:,.2f}, have ${cash:,.2f}")

        new_cash = round(cash - cost, _MONEY_DP)
        old_qty = existing.quantity if existing else 0.0
        old_avg = existing.avg_cost if existing else 0.0
        new_qty = old_qty + qty
        new_avg = round((old_qty * old_avg + qty * price) / new_qty, _COST_DP)

        if existing:
            conn.execute(
                "UPDATE positions SET quantity = ?, avg_cost = ?, updated_at = ?"
                " WHERE user_id = ? AND ticker = ?",
                (new_qty, new_avg, now, user_id, ticker),
            )
        else:
            conn.execute(
                "INSERT INTO positions (id, user_id, ticker, quantity, avg_cost, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (new_id(), user_id, ticker, new_qty, new_avg, now),
            )
        return new_cash, Position(ticker=ticker, quantity=new_qty, avg_cost=new_avg)

    @staticmethod
    def _apply_sell(
        conn: sqlite3.Connection,
        user_id: str,
        ticker: str,
        qty: float,
        price: float,
        cash: float,
        existing: Position | None,
        now: str,
    ) -> tuple[float, Position | None]:
        """Credit cash and reduce the holding. `avg_cost` is never touched.

        Selling does not change what the remaining shares cost, so realized P&L
        stays derivable from the trade log (PLAN.md section 7) instead of being
        smeared into the cost basis.
        """
        held = existing.quantity if existing else 0.0
        if qty > held + QUANTITY_EPSILON:
            raise InsufficientSharesError(
                f"Insufficient shares: tried to sell {_fmt_qty(qty)} {ticker}, "
                f"hold {_fmt_qty(held)}"
            )

        proceeds = round(qty * price, _MONEY_DP)
        new_cash = round(cash + proceeds, _MONEY_DP)
        remaining = held - qty

        if remaining <= QUANTITY_EPSILON:
            # Float dust from a full sell is not a holding. Delete the row rather
            # than leave a 3e-16-share position in the table and the heatmap.
            conn.execute(
                "DELETE FROM positions WHERE user_id = ? AND ticker = ?", (user_id, ticker)
            )
            return new_cash, None

        conn.execute(
            "UPDATE positions SET quantity = ?, updated_at = ? WHERE user_id = ? AND ticker = ?",
            (remaining, now, user_id, ticker),
        )
        assert existing is not None  # remaining > 0 implies a row existed
        return new_cash, Position(ticker=ticker, quantity=remaining, avg_cost=existing.avg_cost)

    @staticmethod
    def _total_value(
        conn: sqlite3.Connection,
        user_id: str,
        cash: float,
        prices: Mapping[str, float],
    ) -> float:
        """Cash plus positions valued at the supplied prices, avg_cost as fallback."""
        total = cash
        for position in Repository._positions(conn, user_id):
            price = prices.get(position.ticker, position.avg_cost)
            total += position.quantity * price
        return round(total, _MONEY_DP)

    # ==================================================================
    # Trade history and realized P&L
    # ==================================================================

    async def get_trades(
        self,
        ticker: str | None = None,
        limit: int = _HISTORY_DEFAULT_LIMIT,
        user_id: str | None = None,
    ) -> list[Trade]:
        """The most recent `limit` fills, returned oldest-first.

        Newest-last matches how the LLM context and any transcript read: in the
        order the trades happened.
        """
        uid = user_id or self.user_id
        symbol = _validate_ticker(ticker) if ticker else None
        capped = max(1, min(int(limit), _HISTORY_MAX_LIMIT))

        def _read(conn: sqlite3.Connection) -> list[Trade]:
            sql = "SELECT * FROM trades WHERE user_id = ?"
            params: list = [uid]
            if symbol:
                sql += " AND ticker = ?"
                params.append(symbol)
            sql += " ORDER BY executed_at DESC, rowid DESC LIMIT ?"
            params.append(capped)
            rows = conn.execute(sql, params).fetchall()
            return [_row_to_trade(r) for r in reversed(rows)]

        return await self._db.run(_read)

    async def get_realized_pnl(self, user_id: str | None = None) -> float:
        """Realized P&L, folded out of the trade log.

        Not stored anywhere (PLAN.md section 7) and not shown in the positions
        table, which is unrealized-only. It exists for the LLM's portfolio context
        in section 9.

        The fold replays every fill in order, maintaining the same running
        weighted-average cost the positions table holds, and credits
        `(sell_price - avg_cost_at_that_moment) * qty` on each sell. Replaying is
        what makes it correct: the current `avg_cost` is the basis of the shares
        still held, which is not the basis the earlier sales were made against.
        """
        uid = user_id or self.user_id
        trades = await self.get_trades(limit=_HISTORY_MAX_LIMIT, user_id=uid)

        basis: dict[str, tuple[float, float]] = {}  # ticker -> (quantity, avg_cost)
        realized = 0.0
        for trade in trades:
            qty, avg = basis.get(trade.ticker, (0.0, 0.0))
            if trade.side == "buy":
                new_qty = qty + trade.quantity
                basis[trade.ticker] = (
                    new_qty,
                    (qty * avg + trade.quantity * trade.price) / new_qty,
                )
            else:
                realized += (trade.price - avg) * trade.quantity
                remaining = qty - trade.quantity
                if remaining <= QUANTITY_EPSILON:
                    basis.pop(trade.ticker, None)
                else:
                    basis[trade.ticker] = (remaining, avg)
        return round(realized, _MONEY_DP)

    # ==================================================================
    # Portfolio history (P&L chart)
    # ==================================================================

    async def get_portfolio_history(
        self,
        since: str | None = None,
        limit: int = _HISTORY_DEFAULT_LIMIT,
        user_id: str | None = None,
    ) -> list[PortfolioSnapshot]:
        """Portfolio value snapshots, oldest-first.

        `since` is an inclusive ISO-8601 lower bound. `limit` is clamped to
        [1, 5000] and selects the *most recent* N, which are then returned in
        chronological order -- a chart that has to drop points should drop the
        oldest, not the newest.
        """
        uid = user_id or self.user_id
        capped = max(1, min(int(limit), _HISTORY_MAX_LIMIT))

        def _read(conn: sqlite3.Connection) -> list[PortfolioSnapshot]:
            sql = "SELECT * FROM portfolio_snapshots WHERE user_id = ?"
            params: list = [uid]
            if since:
                sql += " AND recorded_at >= ?"
                params.append(since)
            sql += " ORDER BY recorded_at DESC, rowid DESC LIMIT ?"
            params.append(capped)
            rows = conn.execute(sql, params).fetchall()
            return [
                PortfolioSnapshot(
                    id=r["id"], total_value=r["total_value"], recorded_at=r["recorded_at"]
                )
                for r in reversed(rows)
            ]

        return await self._db.run(_read)

    async def record_snapshot(
        self, total_value: float, user_id: str | None = None
    ) -> PortfolioSnapshot:
        """Write a portfolio value point outside a trade.

        `execute_trade` already snapshots, and PLAN.md section 7 says trades are
        the only writer -- this exists so a caller that establishes a starting
        point (an initial $10,000 at first load) does not have to write SQL.
        """
        uid = user_id or self.user_id
        snapshot = PortfolioSnapshot(
            id=new_id(),
            total_value=round(float(total_value), _MONEY_DP),
            recorded_at=utc_now_iso(),
        )

        def _write(conn: sqlite3.Connection) -> PortfolioSnapshot:
            conn.execute(
                "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at)"
                " VALUES (?, ?, ?, ?)",
                (snapshot.id, uid, snapshot.total_value, snapshot.recorded_at),
            )
            return snapshot

        return await self._db.transaction(_write)

    # ==================================================================
    # Watchlist
    # ==================================================================

    async def get_watchlist(self, user_id: str | None = None) -> list[str]:
        """Watched tickers, in the order they were added.

        Membership only. Prices come exclusively from the SSE stream (PLAN.md
        section 8) so there is one source of price truth rather than two that can
        disagree.
        """
        uid = user_id or self.user_id

        def _read(conn: sqlite3.Connection) -> list[str]:
            rows = conn.execute(
                "SELECT ticker FROM watchlist WHERE user_id = ? ORDER BY added_at, rowid",
                (uid,),
            ).fetchall()
            return [r["ticker"] for r in rows]

        return await self._db.run(_read)

    async def add_to_watchlist(self, ticker: str, user_id: str | None = None) -> list[str]:
        """Add a ticker and return the complete new watchlist.

        Idempotent: adding a ticker already present is a 200 with an unchanged
        list, not an error (PLAN.md section 8).

        Returning the whole list is not a convenience -- it is the frontend's only
        refresh signal, because the SSE stream never carries membership.

        Does NOT call `source.supports_ticker()`. That check belongs to the API
        layer, which owns the market source; the repository would otherwise have
        to import it and every database test would need a market feed.

        Raises WatchlistFullError at MAX_WATCHLIST_SIZE tickers.
        """
        uid = user_id or self.user_id
        symbol = _validate_ticker(ticker)

        def _write(conn: sqlite3.Connection) -> list[str]:
            existing = conn.execute(
                "SELECT ticker FROM watchlist WHERE user_id = ? ORDER BY added_at, rowid",
                (uid,),
            ).fetchall()
            tickers = [r["ticker"] for r in existing]
            if symbol in tickers:
                return tickers
            if len(tickers) >= MAX_WATCHLIST_SIZE:
                raise WatchlistFullError(
                    f"Watchlist is full ({MAX_WATCHLIST_SIZE} tickers). "
                    "Remove one before adding another."
                )
            conn.execute(
                "INSERT INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
                (new_id(), uid, symbol, utc_now_iso()),
            )
            return [*tickers, symbol]

        return await self._db.transaction(_write)

    async def remove_from_watchlist(self, ticker: str, user_id: str | None = None) -> list[str]:
        """Remove a ticker and return the complete new watchlist.

        Idempotent -- removing an absent ticker is a 200 with an unchanged list.
        Evicting it from the price cache is the API layer's job
        (`source.remove_ticker`), for the same ownership reason as above.
        """
        uid = user_id or self.user_id
        symbol = _validate_ticker(ticker)

        def _write(conn: sqlite3.Connection) -> list[str]:
            conn.execute("DELETE FROM watchlist WHERE user_id = ? AND ticker = ?", (uid, symbol))
            rows = conn.execute(
                "SELECT ticker FROM watchlist WHERE user_id = ? ORDER BY added_at, rowid",
                (uid,),
            ).fetchall()
            return [r["ticker"] for r in rows]

        return await self._db.transaction(_write)

    # ==================================================================
    # Chat messages
    # ==================================================================

    async def add_chat_message(
        self,
        role: str,
        content: str,
        actions: dict | None = None,
        user_id: str | None = None,
    ) -> ChatMessage:
        """Persist one conversation turn.

        `actions` is the PLAN.md section 7 shape, stored as a JSON string.
        Failed actions belong in here too -- the chat panel renders them as
        errors, and section 9 feeds them back to the LLM on the next turn.
        """
        uid = user_id or self.user_id
        if role not in ("user", "assistant"):
            raise DatabaseError(f"'{role}' is not a valid chat role.")

        message = ChatMessage(
            id=new_id(), role=role, content=content, actions=actions, created_at=utc_now_iso()
        )
        payload = json.dumps(actions) if actions is not None else None

        def _write(conn: sqlite3.Connection) -> ChatMessage:
            conn.execute(
                "INSERT INTO chat_messages (id, user_id, role, content, actions, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (message.id, uid, role, content, payload, message.created_at),
            )
            return message

        return await self._db.transaction(_write)

    async def get_chat_messages(
        self, limit: int = _CHAT_DEFAULT_LIMIT, user_id: str | None = None
    ) -> list[ChatMessage]:
        """The most recent `limit` turns, returned oldest-first.

        Oldest-first is what the LLM prompt builder and the chat panel both want;
        the limit still has to take the newest, so the query reverses.
        """
        uid = user_id or self.user_id
        capped = max(1, min(int(limit), _HISTORY_MAX_LIMIT))

        def _read(conn: sqlite3.Connection) -> list[ChatMessage]:
            rows = conn.execute(
                "SELECT * FROM chat_messages WHERE user_id = ?"
                " ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (uid, capped),
            ).fetchall()
            return [
                ChatMessage(
                    id=r["id"],
                    role=r["role"],
                    content=r["content"],
                    actions=json.loads(r["actions"]) if r["actions"] else None,
                    created_at=r["created_at"],
                )
                for r in reversed(rows)
            ]

        return await self._db.run(_read)
