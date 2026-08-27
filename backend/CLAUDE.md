# Backend — Developer Guide

## Project Setup

```bash
cd backend
uv sync --extra dev   # Install all dependencies including test/lint tools
```

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
uv run --extra dev pytest -v                       # All tests
uv run --extra dev pytest --cov=app                # With coverage
uv run --extra dev ruff check app/ tests/          # Lint
uv run --extra dev ruff format app/ tests/         # Format
```

Pass `seed=` to `GBMSimulator` / `SimulatorDataSource` for reproducible price paths.

Massive fixtures must be built from the real SDK dataclasses (`LastTrade`, `Agg`, `TickerSnapshot`), never bare `MagicMock`s — a MagicMock invents any attribute you read from it, which is how a call to a non-existent field once passed 13 green tests while dropping every snapshot in production.

## Demo

```bash
uv run market_data_demo.py   # Live terminal dashboard with simulated prices
```
