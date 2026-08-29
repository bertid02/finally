# Market Data Backend — Summary

**Status:** Complete against `PLAN.md` §6. Reviewed (`MARKET_DATA_REVIEW.md`), all findings resolved.

## What Was Built

A complete market data subsystem in `backend/app/market/` (8 modules) providing live price simulation and real market data via a unified interface.

### Architecture

```
MarketDataSource (ABC)
├── SimulatorDataSource  →  GBM simulator (default, no API key needed)
└── MassiveDataSource    →  Polygon.io REST poller (when MASSIVE_API_KEY set)
        │
        ▼
   PriceCache (thread-safe, in-memory)
        │
        ├──→ SSE stream endpoint (/api/stream/prices)
        ├──→ Portfolio valuation
        └──→ Trade execution
```

### Modules

| File | Purpose |
|------|---------|
| `models.py` | `PriceUpdate` — immutable frozen dataclass (ticker, price, previous_price, session_open, timestamp, provider_change_percent) |
| `interface.py` | `MarketDataSource` ABC — `start/stop/add_ticker/remove_ticker/get_tickers/supports_ticker/name`, plus the shared `normalize_ticker()` |
| `cache.py` | `PriceCache` — thread-safe price store, session-open anchoring, version counter for SSE change detection |
| `seed_prices.py` | Realistic seed prices, per-ticker GBM params (drift/volatility), correlation groups |
| `simulator.py` | `GBMSimulator` (Geometric Brownian Motion with Cholesky-correlated moves) + `SimulatorDataSource` |
| `massive_client.py` | `MassiveDataSource` — REST polling client for Polygon.io, with a price fallback chain and a fatal-error latch |
| `factory.py` | `create_market_data_source()` — selects simulator or Massive based on `MASSIVE_API_KEY` |
| `stream.py` | `create_stream_router()` — FastAPI SSE router factory using version-based change detection |

### Key Design Decisions

- **Strategy pattern** — both data sources implement the same ABC; downstream code is source-agnostic
- **PriceCache as single point of truth** — producers write, consumers read; no direct coupling
- **Two notions of change, never confused** — `change_percent` is tick-over-tick and drives the flash animation only; `change_percent_session` is versus `session_open` and is the displayed daily change
- **Session anchor captured once** — first write for a ticker sets it, and it is preserved thereafter so the denominator never drifts
- **GBM with correlated moves** — Cholesky decomposition of a sector-based correlation matrix; tech 0.6, finance 0.5, cross-sector 0.3
- **Random shock events** — ~0.1% chance per tick per ticker of a 2-5% move for visual drama
- **SSE over WebSockets** — simpler, one-way push, universal browser support
- **Fail fast on an unusable API key** — `MassiveDataSource.start()` raises so the caller can fall back to the simulator rather than serving an empty grid

## Test Suite

**193 tests, all passing. 100% statement coverage of `app/`.**

| Module | Tests | Focus |
|--------|-------|-------|
| test_models.py | 20 | Both change notions, provider-percent precedence, the `to_dict` wire contract |
| test_cache.py | 23 | Session-open capture/preserve/reset, version bumps, rounding |
| test_simulator.py | 41 | GBM statistics, correlation, determinism, normalization, shock events |
| test_simulator_source.py | 32 | Lifecycle, session anchoring, `supports_ticker`, no-writes-after-stop |
| test_massive.py | 48 | Price fallback chain, nanosecond timestamps, fatal-error latch, plan tiers |
| test_stream.py | 19 | SSE framing, payload contract, idle silence, keepalive, disconnect |
| test_factory.py | 10 | Source selection, interface conformance |

The GBM mathematics is verified statistically, not just smoke-tested: realized volatility, pairwise correlation, and the Itō drift correction are each asserted against their targets over 60,000 steps with a fixed seed.

## Usage for Downstream Code

```python
from app.market import PriceCache, create_market_data_source

# Startup
cache = PriceCache()
source = create_market_data_source(cache)  # Reads MASSIVE_API_KEY
try:
    await source.start(["AAPL", "GOOGL", "MSFT", ...])
except RuntimeError:
    # Massive cannot serve this key — fall back to the simulator
    source = SimulatorDataSource(price_cache=cache)
    await source.start([...])

# Read prices
update = cache.get("AAPL")          # PriceUpdate or None
price = cache.get_price("AAPL")     # float or None — what a trade fills against
all_prices = cache.get_all()        # dict[str, PriceUpdate]

# Dynamic watchlist (POST /api/watchlist)
if await source.supports_ticker("PYPL"):   # else 422 UNSUPPORTED_TICKER
    await source.add_ticker("PYPL")
await source.remove_ticker("GOOGL")

# Shutdown
await source.stop()
```

## Demo

A Rich terminal demo is available at `backend/market_data_demo.py`:

```bash
cd backend
uv run market_data_demo.py
```

Displays a live-updating dashboard with all 10 tickers, sparklines, color-coded direction arrows, and an event log for notable price moves. Runs 60 seconds or until Ctrl+C.
