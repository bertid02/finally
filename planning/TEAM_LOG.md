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

---

## Contract: HTTP response shapes — ruled by team-lead (2026-08-30)

Resolving the two open items the frontend-engineer raised at the end of Stage 1.
Both proposals are **adopted as specified**. They match the envelope convention
PLAN.md §8 already uses for `{"tickers": [...]}`, so they add no new idiom.

### 1. `GET /api/portfolio/history` — adopted

```json
{"snapshots": [{"total_value": 10482.19, "recorded_at": "2026-08-24T04:11:00Z"}]}
```

Newest-last, which is `Repository.get_portfolio_history()`'s oldest-first ordering
unchanged — same ordering, two names for it. Object, not a bare array: it leaves
room to add a cursor later without breaking the client. **backend-api-engineer:
implement this shape.**

### 2. `POST /api/chat` — adopted

```json
{"message": "...",
 "actions": {"trades": [...], "watchlist_changes": [...]},
 "watchlist": ["AAPL", "..."],
 "cash_balance": 8095.00,
 "positions": [{"ticker": "AAPL", "quantity": 10, "avg_cost": 190.50}]}
```

`actions` is the PLAN.md §7 `chat_messages.actions` shape verbatim, failed entries
included. **llm-engineer: populate all five fields.** The frontend tolerates the
echoes being absent by refetching, but a turn that changed the watchlist and did
not echo it costs a round trip on every message — so always send them.

### Assumptions confirmed, not overridden

The five assumptions the frontend listed are all restatements of PLAN.md §8 and
stand as written: static export `frontend/out/` → `/app/static` with `index.html`
fallback for unknown non-`/api` paths; all three watchlist endpoints return
`{"tickers": [...]}`; `GET /api/portfolio` carries no server-computed valuation;
the trade response carries `position: null` on a closing sell; and every non-2xx
uses `{"error": {"code", "message"}}` with user-facing prose in `message`.

**backend-api-engineer:** if you intend to deviate from any of the above, say so
here *before* implementing. The frontend is already written against these shapes,
so a silent change surfaces as an E2E failure in Stage 5 rather than a compile
error in Stage 2 — the most expensive place to find it.

---

## Contract: backend-api-engineer (2026-08-30)

`backend/app/main.py`, `backend/app/config.py`, `backend/app/api/**`. Stage 2 is
complete: every PLAN.md §8 endpoint is live, the market source runs in the
lifespan, and the frontend export is served from `/app/static`.

**All shapes ruled by team-lead above are implemented as specified.** Two
additive deviations are recorded at the bottom of this entry — neither breaks a
client coded against the ruled shape.

### Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/api/health` | `{status, market_source, requested_source, fallback, tickers, llm_mock, static}` |
| GET | `/api/portfolio` | `{cash_balance, positions:[{ticker,quantity,avg_cost}]}` — **unvalued**, per §8/§10 |
| POST | `/api/portfolio/trade` | `{ticker, quantity, side}` → `{trade, cash_balance, position\|null}` |
| GET | `/api/portfolio/history` | `?since=&limit=` → `{"snapshots":[…]}`, oldest-first |
| GET | `/api/watchlist` | `{"tickers":[…]}` |
| POST | `/api/watchlist` | `{ticker}` → `{"tickers":[…]}` (full new list) |
| DELETE | `/api/watchlist/{ticker}` | → `{"tickers":[…]}` (full new list) |
| GET | `/api/stream/prices` | `create_stream_router()` from `app/market/stream.py`, unmodified |

`POST /api/chat` is deliberately absent — it is `llm-engineer`'s
`backend/app/api/chat.py`. Register it in `create_app()` alongside the other
three `include_router` calls, **before** `_mount_frontend(app, settings)`.

### What Stage 3 imports

```python
# The single server-side valuation helper. One formula, one implementation.
from app.api.valuation import build_valuation, value_portfolio, prices_from_cache

valuation = await build_valuation(repo, cache)   # the one-liner for the chat context
valuation.total_value                 # cash + Σ(quantity × price)
valuation.total_unrealized_pnl        # and .._percent
valuation.positions                   # PositionValuation: price, market_value,
                                      # cost_basis, unrealized_pnl(_percent),
                                      # weight (% of total value, cash included),
                                      # priced (False = valued at avg_cost fallback)
valuation.to_dict()                   # JSON-ready, for dropping into a prompt
```

`value_portfolio(portfolio, prices)` is the pure function underneath — no I/O, so
it is testable without a database. `prices_from_cache(cache)` flattens the cache
to `{ticker: price}`, which is also exactly what
`Repository.execute_trade(market_prices=...)` wants. Realized P&L is **not** in
the valuation: it lives in the trade log, so call `await repo.get_realized_pnl()`.

```python
# The error vocabulary.
from app.api.errors import APIError, UnsupportedTickerError, validate_ticker, envelope
```

- `validate_ticker(raw) -> str` — trim, uppercase, enforce `^[A-Z]{1,5}$`; raises
  the **database layer's** `InvalidTickerError` so the format rule has one code.
  Call it before any cache lookup: the cache is keyed by normalized symbol.
- `UnsupportedTickerError` is the 422; `APIError` is its base and mirrors
  `DatabaseError`'s surface exactly (`.code`, `.http_status`, `.message`,
  `.to_envelope()`).
- Handlers for `DatabaseError`, `APIError`, `RequestValidationError`,
  `HTTPException` and bare `Exception` are installed by `register_error_handlers`
  in `create_app`. **Raise, don't catch:** any `DatabaseError` or `APIError` that
  escapes a route becomes the right envelope at the right status automatically.

```python
# Request-scoped access to the three long-lived objects.
from app.api.deps import RepositoryDep, PriceCacheDep, MarketSourceDep

async def chat(body: ChatRequest, repo: RepositoryDep, cache: PriceCacheDep,
               source: MarketSourceDep) -> dict: ...
```

They resolve `app.state.repository`, `app.state.price_cache`,
`app.state.market_source`. `app.state.settings` carries `llm_mock` — read it from
there rather than calling `os.getenv("LLM_MOCK")` again, so one object decides.

### Reusing the trade path for LLM auto-execution

There is no separate service layer to call; the route body *is* the path, and it
is three lines. Reproduce them inside the chat turn so a failure can be caught and
written into `chat_messages.actions` instead of becoming an HTTP error:

```python
from app.api.errors import validate_ticker
from app.api.valuation import prices_from_cache
from app.db import DatabaseError

try:
    ticker = validate_ticker(spec["ticker"])
    result = await repo.execute_trade(
        ticker=ticker, side=spec["side"], quantity=spec["quantity"],
        price=cache.get_price(ticker),            # None -> UnknownTickerError
        market_prices=prices_from_cache(cache),
    )
    entry = {"ticker": ticker, "side": result.trade.side,
             "quantity": result.trade.quantity, "status": "executed",
             "price": result.trade.price, "total": result.trade.total}
except DatabaseError as exc:                       # covers all seven DB codes
    entry = {"ticker": spec.get("ticker"), "side": spec.get("side"),
             "quantity": spec.get("quantity"), "status": "failed",
             "error_code": exc.code, "error": exc.message}
```

`exc.message` verbatim is what PLAN.md §7 requires — same words through both
doors. For watchlist changes, mirror `app/api/watchlist.py`: format → present →
`WATCHLIST_FULL` → `supports_ticker` (raise `UnsupportedTickerError`) →
`repo.add_to_watchlist` → `await source.add_ticker(t)`; and
`repo.remove_from_watchlist` → `await source.remove_ticker(t)` on removal. Then
echo the returned list as `watchlist` in the response — the stream carries no
membership.

### Startup, shutdown, static

- `create_app(settings=None, database=None)` builds it; `app = create_app()` at
  module scope is what `uvicorn app.main:app` serves. Both arguments exist for
  tests, so a second app with its own in-memory database does not disturb the
  first.
- Lifespan: `repo.initialize()` → read the watchlist → start the market source
  seeded from it. A `RuntimeError` from `start()` (a Massive key whose plan lacks
  the snapshot endpoint) falls back to `SimulatorDataSource`, and `/api/health`
  then reports `market_source: "simulator"`, `requested_source: "massive"`,
  `fallback: true`. Shutdown stops the source and closes the connection.
- `set_database(db)` is called in `create_app`, so a bare `Repository()` or
  `get_database()` anywhere in the process reaches the same connection.
- Static: `SPAStaticFiles` mounted at `/` **last**, after every router, with an
  `index.html` fallback for unknown non-`/api` paths. `/api/*` is excluded from
  that fallback — an unrouted API path stays a JSON 404 rather than returning a
  page of HTML that fails the frontend's `JSON.parse` instead of its status check.
  A missing directory is not an error: the mount is skipped and `/` returns a
  short JSON note, so `uv run uvicorn app.main:app` works in a checkout with no
  frontend build.
- **devops-engineer:** `FINALLY_DB_PATH=/app/db/finally.db` as db-engineer
  requested, and `/app/static` for the export. `FINALLY_STATIC_DIR` overrides the
  static path if you ever need it; the default is already `/app/static`.

### Deviations recorded (both additive)

1. **`/api/portfolio/history` snapshots carry an extra `id`.** The ruled shape is
   `{"total_value", "recorded_at"}`; the response is `PortfolioSnapshot.to_dict()`,
   which is that plus `id`. A superset — frontend needs no change. Say so here if
   you want it stripped.
2. **Four codes outside PLAN.md §8's eight**, for responses §8 does not cover, so
   that *every* non-2xx wears the envelope rather than FastAPI's `{"detail": …}`:
   `INVALID_REQUEST` (422, malformed body with no recognisable field),
   `NOT_FOUND` (404, unrouted path), `HTTP_ERROR` (405 and friends),
   `INTERNAL_ERROR` (500, unhandled exception). A malformed `quantity`, `side` or
   `ticker` field maps onto its §8 code instead, so `{"quantity": "ten"}` and
   `{"quantity": -1}` report identically. **frontend-engineer / integration-tester:**
   the eight §8 codes are unchanged; these are extra, and all wear
   `{"error": {"code", "message"}}`.

### Dependencies

`backend/pyproject.toml` gained `httpx>=0.27.0` under `[dev]` only. `litellm` is
**not** added — Stage 3's call. No runtime dependency changed.

### Tests

`backend/tests/api/` — 72 tests. Suite total **495 passing**, ruff clean, and
statement coverage is **100% of `app/`** including `app/api/`, `app/config.py`
and `app/main.py` (the 423/100% baseline is not regressed). Every one of the
eight §8 codes is asserted over HTTP in `tests/api/test_errors.py`.

One note for `integration-tester`: `/api/stream/prices` **cannot** be tested
through `httpx.ASGITransport` — it buffers the whole response, so it never sees
the first frame of an endless stream and simply hangs. `tests/api/test_app.py`
drives the ASGI app directly instead (`_read_sse`). Playwright's `EventSource` is
unaffected.
