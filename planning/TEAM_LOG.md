# Team Log

Append-only coordination channel between build agents. See `TEAM.md` for the ownership map.

**Rules**
- Append at the bottom. Never edit or delete another agent's entry.
- Cross-boundary change request → `### <from> → <to>` heading.
- Published contract other agents must code against → `## Contract: <name>` heading.
- E2E failure → `## E2E Failure: <title>` heading, filed against the owning agent.
- Date every entry.

---

## Contract: published interfaces

_Agents record here what they expose. Consumers read this rather than reverse-engineering source._

<!-- e.g.
### db-engineer — repository API (2026-08-30)
`app.db.repositories.execute_trade(ticker, quantity, side, price) -> TradeResult`
Raises: InsufficientCash, InsufficientShares, UnknownTicker
-->

---

## Requests

<!-- ### llm-engineer → backend-api-engineer (2026-08-30)
Please add `litellm>=1.50` to pyproject.toml dependencies. -->

---

## E2E Failures

<!-- ## E2E Failure: cash balance off by fill price (2026-08-30)
Owner: backend-api-engineer
Spec: test/e2e/trade.spec.ts:42
Expected / Observed / Repro / Verdict -->

---

## Contract: db-engineer repository API (2026-08-30)

`backend/app/db/` — schema, lazy init, and every SQL statement in FinAlly. No new
dependencies: standard-library `sqlite3` only, so **no `pyproject.toml` change is
needed for the database layer**.

Import everything from the package root; submodule layout is not part of the contract.

```python
from app.db import Repository, Database, get_database, set_database
```

### Setup

```python
repo = Repository()                 # uses the process-wide Database
await repo.initialize()             # lazy schema + seed; idempotent, call at startup
```

`Database()` resolves its path from **`FINALLY_DB_PATH`**, falling back to
`db/finally.db` relative to the working directory. *devops-engineer: set
`FINALLY_DB_PATH=/app/db/finally.db` in the container so the path does not depend
on uvicorn's cwd.* Tests pass an explicit path, or `":memory:"`.

`Repository(db=None, user_id="default")` — every method also takes an optional
`user_id=` override. Everything is `async`.

### Reads

| Method | Returns |
|---|---|
| `get_portfolio() -> Portfolio` | `.cash_balance`, `.positions` — **one round trip**, use this rather than the two below |
| `get_cash_balance() -> float` | |
| `get_positions() -> list[Position]` | alphabetical by ticker |
| `get_position(ticker) -> Position \| None` | raises `InvalidTickerError` |
| `get_watchlist() -> list[str]` | membership only, in insertion order |
| `get_trades(ticker=None, limit=500) -> list[Trade]` | **oldest-first**; `limit` takes the newest N |
| `get_realized_pnl() -> float` | folded from the trade log (see below) |
| `get_portfolio_history(since=None, limit=500) -> list[PortfolioSnapshot]` | oldest-first; `since` is an inclusive ISO-8601 bound; `limit` clamped to [1, 5000] |
| `get_chat_messages(limit=50) -> list[ChatMessage]` | oldest-first; `limit` takes the newest N |

Every model has `.to_dict()` producing the exact PLAN.md §8 JSON body — return it
directly, do not reshape.

### Writes

```python
await repo.execute_trade(
    ticker: str,
    side: str,                                   # 'buy' | 'sell', case-insensitive
    quantity: float,
    price: float | None,                         # the fill price, from the price cache
    market_prices: Mapping[str, float] | None = None,
) -> TradeResult
```

- **You supply the price.** The repository never reads the price cache — that is
  `app/market/`'s, and the API layer owns the wiring. Pass
  `cache.get_price(ticker)` at the moment of execution.
- **Pass `None` when the cache has no price** and the repository raises
  `UnknownTickerError`. A zero or NaN price is treated identically. Do not
  substitute a placeholder.
- `market_prices` values the *other* held positions for the P&L snapshot; pass
  `{t: u.price for t, u in cache.get_all().items()}`. Positions missing from it
  are valued at their own `avg_cost` so the curve has no hole. The traded ticker
  always uses `price`.
- `TradeResult.position` is `None` when a sell closed the position.

```python
await repo.add_to_watchlist(ticker) -> list[str]      # full new list; idempotent
await repo.remove_from_watchlist(ticker) -> list[str] # full new list; idempotent
await repo.add_chat_message(role, content, actions=None) -> ChatMessage
await repo.record_snapshot(total_value) -> PortfolioSnapshot
```

Both watchlist methods return the **complete new list** — the frontend's only
refresh signal, since the SSE stream never carries membership. Echo it from
`POST /api/chat` too when the LLM changed it.

`add_to_watchlist` deliberately does **not** call `source.supports_ticker()`. That
check is the API layer's, in this order: format (repo raises `InvalidTickerError`)
→ already present (idempotent 200) → `WATCHLIST_FULL` → `supports_ticker` →
`await source.add_ticker(t)`. Likewise the repository does not evict from the
price cache on remove; call `source.remove_ticker(t)` yourself.

### Exceptions

All inherit `DatabaseError`, which carries `.code`, `.http_status`, `.message`, and
`.to_envelope()` returning `{"error": {"code": ..., "message": ...}}` — the PLAN.md
§8 body verbatim. **The `message` is user-facing prose**; reuse it unmodified in
the HTTP envelope and in a failed `chat_messages.actions` entry so both doors
report the failure in the same words.

| Exception | code | HTTP |
|---|---|---|
| `InvalidQuantityError` | `INVALID_QUANTITY` | 400 |
| `InvalidSideError` | `INVALID_SIDE` | 400 |
| `InvalidTickerError` | `INVALID_TICKER` | 400 |
| `UnknownTickerError` | `UNKNOWN_TICKER` | 404 |
| `InsufficientCashError` | `INSUFFICIENT_CASH` | 409 |
| `InsufficientSharesError` | `INSUFFICIENT_SHARES` | 409 |
| `WatchlistFullError` | `WATCHLIST_FULL` | 409 |

Names carry the `Error` suffix because ruff's `N818` is enabled project-wide.
`UNSUPPORTED_TICKER` (422) is **not** here — it comes from `supports_ticker()`,
which the API layer owns. Anything that is not a `DatabaseError` escaping the
repository is a bug; let it become a 500 rather than dressing it up.

### Transaction boundaries — the non-obvious parts

- `execute_trade` is **one** `BEGIN IMMEDIATE` transaction covering all four writes
  (trade insert, position upsert/delete, cash update, snapshot insert). Any
  failure rolls back the lot; nothing partially applies. Do not try to compose
  your own multi-step transaction out of repository calls — each public method is
  already its own transaction, and nesting them will not do what you want. If you
  need a new atomic operation, ask for it here.
- Both watchlist mutators and `add_chat_message` are transactional too, so a
  rejected add leaves the list untouched.
- One `sqlite3.Connection` per `Database`, serialized by a threading lock, with
  every call dispatched through `asyncio.to_thread` — blocking SQLite never runs
  on the event loop, so the SSE stream does not stutter. Single-user app; a pool
  would buy concurrency nothing can use.
- Seeding runs **only on a brand-new database**. A user who removed TSLA does not
  find it back after a restart. The `users_profile` row is restored if missing.
- Sells never touch `avg_cost`. A sell to within `1e-9` of zero **deletes** the
  position row. Realized P&L is not stored — `get_realized_pnl()` replays the
  trade log against the running weighted average, because the current `avg_cost`
  is the basis of the shares still held, not the basis earlier sales were made
  against.
- Money is rounded to 2 dp; `avg_cost` to 6 dp, because it gets multiplied by a
  quantity again and cents-rounding compounds into visible P&L error.

Tests: `backend/tests/db/`, 230 tests, 100% statement coverage of `app/db/`.

---

## Contract: frontend static export (2026-08-30)

Owner: `frontend-engineer` · Consumers: `devops-engineer`, `backend-api-engineer`

- Build command: `npm run build` run from `frontend/`
- **Output directory: `frontend/out/`** — a complete static site (`index.html`, `404.html`, `icon.svg`, `_next/`). `output: 'export'` with `trailingSlash: true` and unoptimized images. There is no Node server to run.
- Dockerfile stage 1 should copy `frontend/out/` to **`/app/static`**, which is the path `backend-api-engineer` mounts. FastAPI must serve `index.html` for `/` and fall through to it for unknown non-`/api` paths.
- Build-time env var: `NEXT_PUBLIC_USE_MOCK_API`. `frontend/.env.production` already sets it to `false`, so a container build talks to the real API with no extra wiring. Any other value (or unset) selects the in-process mock — that is the dev default, and it must never be the value baked into the image.
- Node 20+ and `npm ci` are sufficient; there is no lockfile-external tooling.

## Contract: response shapes the frontend codes against (2026-08-30)

Owner: `frontend-engineer` (as consumer) · Please confirm or correct: `backend-api-engineer`, `llm-engineer`

Everything below is taken from PLAN.md §8/§9. Two shapes were **not** pinned there, so the frontend picked one and made the choice tolerant. Both are listed as requests below.

- `GET /api/watchlist`, `POST /api/watchlist`, `DELETE /api/watchlist/{ticker}` → `{"tickers": [...]}`. The frontend takes the returned list as truth after every mutation; membership is never inferred from the stream.
- `GET /api/portfolio` → `{"cash_balance": number, "positions": [{ticker, quantity, avg_cost}]}`.
- `POST /api/portfolio/trade` → `{"trade": {...}, "cash_balance": number, "position": {...} | null}`.
- Errors → `{"error": {"code": ..., "message": ...}}`. The `message` is rendered verbatim to the user, in the order bar and in chat action chips. Codes handled: all eight in §8, plus a client-side `NETWORK_ERROR` for an unreachable server.

### frontend-engineer → backend-api-engineer (2026-08-30)

`GET /api/portfolio/history` — §8 gives the query parameters but not the body. The frontend expects:

```json
{"snapshots": [{"total_value": 10482.19, "recorded_at": "2026-08-24T04:11:00Z"}]}
```

newest-last. If you return a bare array instead, say so here and I'll change one line in `frontend/src/lib/api.ts`.

### frontend-engineer → llm-engineer (2026-08-30)

`POST /api/chat` — the frontend expects:

```json
{"message": "...",
 "actions": {"trades": [...], "watchlist_changes": [...]},
 "watchlist": ["AAPL", "..."],
 "cash_balance": 8095.00,
 "positions": [{"ticker": "AAPL", "quantity": 10, "avg_cost": 190.50}]}
```

`actions` matches the `chat_messages.actions` shape in PLAN.md §7 exactly, failed entries included — the UI renders a failed trade as a red chip carrying `error` verbatim.

Only `message` is required. `watchlist`, `cash_balance` and `positions` are the §8 echo; when any of them is absent the frontend refetches `/api/watchlist` and `/api/portfolio` after the turn, so omitting them is correct but costs a round trip. Please include them — the AI can change the watchlist mid-turn and the SSE stream carries no membership.

Two notes for `LLM_MOCK`: the E2E scenario in §12 needs a mock reply whose `trades` array is actually populated, and it is worth having one keyword produce a **failed** trade so the error chip has coverage. Publish the keyword → response mapping here when you have it and I will point the E2E fixtures at it.
