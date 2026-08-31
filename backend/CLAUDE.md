# Backend — Developer Guide

## Project Setup

```bash
cd backend
uv sync --all-extras   # Install all dependencies including test/lint tools
```

`uv sync` on its own installs the runtime dependencies only. `uv run pytest` then
fails with a bare `Failed to spawn: pytest / No such file or directory`, which
reads like a broken checkout rather than a missing extra.

## The four subsystems

`app/` is four packages plus two files, and each package re-exports its whole
public surface from its `__init__.py` — import from `app.db`, never from
`app.db.repository`, so the internal layout stays free to change.

| Package | Owns | Tests |
|---|---|---|
| `app/market/` | Simulator, Massive client, price cache, SSE stream | 193 |
| `app/db/` | Schema DDL, lazy init, seed, repository, transactional trades | 230 |
| `app/api/` | Routes, error envelope, valuation helper, dependencies | 99 |
| `app/llm/` | LiteLLM client, prompt, structured output, mock mode | 105 |

`app/config.py` reads the environment once (`load_settings()`); `app/main.py`
assembles the app (`create_app()`), owns the lifespan, and mounts the static
export. 627 tests total, 100% statement coverage of `app/`, enforced in CI at
`--cov-fail-under=100`.

### `app/db` — persistence

```python
from app.db import Repository, get_database

repo = Repository()          # process-wide Database
await repo.initialize()      # lazy schema + seed; safe on every startup
```

`Repository` is the only way in. Reads: `get_portfolio`, `get_cash_balance`,
`get_positions`, `get_position`, `get_trades`, `get_realized_pnl`,
`get_portfolio_history`, `get_watchlist`, `get_chat_messages`. Writes:
`execute_trade`, `record_snapshot`, `add_to_watchlist`, `remove_from_watchlist`,
`add_chat_message`.

- **`execute_trade` is one SQLite transaction** across `trades`, `positions`,
  `users_profile.cash_balance` and `portfolio_snapshots`. Nothing partially
  applies.
- **A buy re-weights `avg_cost`; a sell never touches it.** A sell to within
  `QUANTITY_EPSILON` (1e-9) of zero **deletes** the position row rather than
  leaving `quantity = 0`.
- **Realized P&L is derived, not stored** (`get_realized_pnl`), and appears only
  in the LLM's portfolio context — never in the positions table, which is
  unrealized only.
- Failures raise typed `DatabaseError` subclasses (`InsufficientCashError`,
  `InsufficientSharesError`, `InvalidQuantityError`, …) that carry the PLAN.md §8
  code and user-facing message. Routes do not re-word them.

### `app/api` — routes and the error envelope

`register_error_handlers(app)` turns every `DatabaseError` and `APIError` into the
single envelope `{"error": {"code": …, "message": …}}`. **There is one error
vocabulary, not two** — the manual trade path and the LLM auto-execution path both
surface these same strings, and the chat panel reuses `message` verbatim.

`app/api/valuation.py` is the one server-side valuation helper (`value_position`,
`value_portfolio`, `prices_from_cache`), shared by `/api/portfolio` and the chat
context builder. The client recomputes the same numbers live between fetches and
is authoritative for anything displayed — PLAN.md §13.3 S5.

### `app/llm` — the chat turn

```python
from app.llm import run_chat_turn

body = await run_chat_turn(message=..., repo=..., cache=..., source=...,
                           llm_mock=settings.llm_mock)
```

`app/api/chat.py` is the only production caller. **Only an empty message is
rejected**; a provider outage (`LLMUnavailableError`) or an unparseable reply
(`LLMResponseError`) degrades to prose in a 200, because a chat panel showing an
error banner is worse than one that says what went wrong. Trades the model asks
for are auto-executed through the same validation as manual ones, and failures are
recorded in `actions` with their code and message.

`mock_completion()` maps keywords in the user's message to canned structured
responses (`buy`, `sell`, `watch`/`add`, `unwatch`/`remove`, `yolo`/`all in` for a
failed trade, `unavailable`, `malformed`). The E2E suite is pinned to that mapping
— changing a keyword breaks `test/e2e/06-chat.spec.ts`.

## Market Data API

The market data subsystem lives in `app/market/`. Use these imports:

```python
from app.market import PriceCache, PriceUpdate, MarketDataSource, create_market_data_source
```

### Core Types

- **`PriceUpdate`** — Immutable dataclass: `ticker`, `price`, `previous_price`, `session_open`, `timestamp`, `provider_change_percent`, plus properties `change`, `change_percent`, `change_session`, `change_percent_session`, `direction` ("up"/"down"/"flat"), and `to_dict()` for JSON serialization.

  **Two notions of change live here and must not be confused:**

  | Property | Basis | Use |
  |---|---|---|
  | `change` / `change_percent` | previous tick (~500ms) | flash animation direction only — never display the number |
  | `change_session` / `change_percent_session` | `session_open` | the watchlist "daily change %" and positions table |

- **`PriceCache`** — Thread-safe in-memory store. Key methods:
  - `update(ticker, price, timestamp=None, session_open=None, change_percent_session=None) -> PriceUpdate`
  - `get(ticker) -> PriceUpdate | None`
  - `get_price(ticker) -> float | None`
  - `get_all() -> dict[str, PriceUpdate]`
  - `remove(ticker)` — drops the price *and* the session anchor, and bumps the version
  - `version` property — monotonic counter, bumped on every write (for SSE change detection)

  `session_open` is captured on the **first** update for a ticker and preserved thereafter, so the daily-change denominator never drifts mid-session.

- **`MarketDataSource`** — Abstract interface implemented by `SimulatorDataSource` and `MassiveDataSource`. Lifecycle: `start(tickers)` -> `add_ticker()` / `remove_ticker()` -> `stop()`. Also exposes `supports_ticker(ticker)` (await before a watchlist insert) and a `name` property (`"simulator"` / `"massive"`).

  - `start()` raises `RuntimeError` if the source cannot produce data at all (bad key, endpoint not in plan) — the caller should fall back to the simulator. It also raises if called twice.
  - After `stop()`, a source never writes to the cache again.
  - Both sources normalize symbols identically via `normalize_ticker()` (trim + uppercase).

- **`create_market_data_source(cache)`** — Factory. Returns `MassiveDataSource` if `MASSIVE_API_KEY` is set, otherwise `SimulatorDataSource`. Returns an **unstarted** source.

### SSE Streaming

```python
from app.market import create_stream_router

router = create_stream_router(price_cache)  # Returns a fresh FastAPI APIRouter
# Endpoint: GET /api/stream/prices (text/event-stream)
```

One event per tick carries **every** ticker, keyed by symbol. The stream only emits when the cache version changes — an idle cache sends nothing, so clients must not treat silence as a disconnect. A `: keepalive` comment goes out every 15s of idle so intermediaries don't close the connection.

### Seed Data

Default tickers: AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX. Seed prices and per-ticker volatility/drift params are in `app/market/seed_prices.py`. Under the simulator the seed price is also the session anchor.

## Running Tests

```bash
uv run pytest                                      # All 627
uv run pytest --cov=app --cov-report=term-missing  # 100% of app/ today
uv run ruff check .                                # Lint (CI gate)
uv run pytest tests/db -q                          # One subsystem
```

`ruff format` is **not** a CI gate and 15 files do not currently satisfy it —
running it will reformat unrelated code and bury your diff. `ruff check` is the
gate.

The suite is hermetic: an autouse fixture in `tests/conftest.py` deletes
`OPENROUTER_API_KEY`, `MASSIVE_API_KEY`, `LLM_MOCK`, `FINALLY_DB_PATH` and
`FINALLY_STATIC_DIR` before every test. Without it, `load_dotenv()` — ours, or the
one LiteLLM fires at import — leaves the developer's real `.env` in `os.environ`
for the rest of the session, so an assertion about an *absent* variable passes in
CI and fails only for the person whose chat panel works.

Pass `seed=` to `GBMSimulator` / `SimulatorDataSource` for reproducible price paths.

Massive fixtures must be built from the real SDK dataclasses (`LastTrade`, `Agg`, `TickerSnapshot`), never bare `MagicMock`s — a MagicMock invents any attribute you read from it, which is how a call to a non-existent field once passed 13 green tests while dropping every snapshot in production.

## Demo

```bash
uv run market_data_demo.py   # Live terminal dashboard with simulated prices
```

## Running the server directly

The image's defaults are absolute (`/app/db`, `/app/static`), so a local run needs
both pointed somewhere real:

```bash
FINALLY_DB_PATH=../db/finally.db FINALLY_STATIC_DIR=../frontend/out \
  uv run uvicorn app.main:app --port 8000
```

A missing static directory is not an error — `/api/*` works and `GET /` explains
that no frontend build is present. `GET /api/health` reports what the process
actually resolved: `market_source`, `requested_source`, `fallback`, `tickers`,
`llm_configured` (key *presence* only — never the key, a prefix, or its length),
`llm_mock` and `static`.
