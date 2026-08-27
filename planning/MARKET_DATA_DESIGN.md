# Market Data Backend — Detailed Design

> **Purpose.** A single implementation-ready specification for FinAlly's market data
> subsystem: the unified `MarketDataSource` contract, the GBM simulator, the Massive
> (Polygon.io) REST poller, the shared price cache, and the SSE endpoint that feeds the
> browser.
>
> **Relationship to the other docs.** This consolidates `MARKET_INTERFACE.md` (the contract),
> `MARKET_SIMULATOR.md` (the synthetic source), and `MASSIVE_API.md` (the real source) into
> one buildable design, and reconciles all three against the code shipped in
> `backend/app/market/`. Where this document and the shipped code disagree, this document is
> the target — §14 lists every delta. `PLAN.md` §6 and §8 remain the product-level authority.
>
> **Status of the code today.** `backend/app/market/` exists and passes 73 tests, but three
> things in it are wrong or missing and are load-bearing for the rest of the platform:
> `session_open` (no data source for "daily change %"), `supports_ticker()` (no watchlist
> validation), and a field-name bug in the Massive poller that silently skips *every* ticker
> on *every* poll. All are addressed below.

---

## Table of Contents

1. [Architecture](#1-architecture)
2. [File Structure](#2-file-structure)
3. [`models.py` — the `PriceUpdate` value type](#3-modelspy--the-priceupdate-value-type)
4. [`cache.py` — `PriceCache`](#4-cachepy--pricecache)
5. [`interface.py` — `MarketDataSource`](#5-interfacepy--marketdatasource)
6. [`seed_prices.py` — simulator data](#6-seed_pricespy--simulator-data)
7. [`simulator.py` — GBM simulator](#7-simulatorpy--gbm-simulator)
8. [`massive_client.py` — the real data source](#8-massive_clientpy--the-real-data-source)
9. [`factory.py` — selection and fallback](#9-factorypy--selection-and-fallback)
10. [`stream.py` — the SSE endpoint](#10-streampy--the-sse-endpoint)
11. [FastAPI wiring](#11-fastapi-wiring)
12. [Downstream consumers](#12-downstream-consumers)
13. [Testing](#13-testing)
14. [Implementation checklist — delta from shipped code](#14-implementation-checklist--delta-from-shipped-code)
15. [Configuration and error reference](#15-configuration-and-error-reference)

---

## 1. Architecture

```
                    create_market_data_source(cache)
                                 │
                     MASSIVE_API_KEY set?
                    ┌────────────┴────────────┐
                   yes                        no
                    │                          │
          MassiveDataSource            SimulatorDataSource
       (REST poll, ~15s, thread)         (GBM tick, 500ms)
                    │                          │
                    └────────────┬─────────────┘
                                 │  writes
                                 ▼
                        ┌────────────────┐
                        │   PriceCache   │  ← the only source of price truth
                        │  + version ctr │
                        └────────────────┘
                                 │  reads
        ┌────────────────┬───────┴────────┬─────────────────┐
        ▼                ▼                ▼                 ▼
 SSE /api/stream   trade execution  portfolio value    LLM context
```

Four properties, each load-bearing:

- **Nothing downstream knows which source is running.** There is no `if simulator:` anywhere
  outside `factory.py`. Swapping sources is an environment variable, not a code path.
- **Writers push, readers pull.** Sources never call the SSE layer; the SSE layer never calls
  a source. They are coupled only through the cache, so a slow REST poll cannot stall a
  stream, and a client disconnecting cannot perturb the feed.
- **One price, one place.** Trade fills, portfolio valuation, the LLM's context, and the
  browser all read the same `PriceCache` entry. They cannot disagree — which is exactly why
  `PLAN.md` §8 makes `GET /api/watchlist` return membership only.
- **The cache is source-independent state.** Multi-user, replay, and a recorded-tape test
  source all slot in without touching a consumer.

### The two "change" numbers

This is the single most confusable thing in the module, so it is pinned here once:

| Field | Denominator | Used for |
|---|---|---|
| `change`, `change_percent` | the **previous tick** (~500 ms ago) | the flash animation's direction, and nothing else. Displaying it as a number prints meaningless ±0.02% noise |
| `change_session`, `change_percent_session` | **`session_open`** | the watchlist's "daily change %" column and the positions table's "% change" |

---

## 2. File Structure

```
backend/app/market/
├── __init__.py           # public exports
├── models.py             # PriceUpdate            — shared
├── cache.py              # PriceCache             — shared
├── interface.py          # MarketDataSource ABC   — shared
├── factory.py            # env-var selection + fallback
├── seed_prices.py        # simulator data only: seeds, params, correlations
├── simulator.py          # GBMSimulator (pure math) + SimulatorDataSource (async)
├── massive_client.py     # MassiveDataSource + price/session resolvers
└── stream.py             # create_stream_router() — SSE endpoint

backend/tests/market/
├── conftest.py           # cache fixture, snapshot builders, FakeRESTClient
├── test_models.py        # PriceUpdate properties, to_dict key contract
├── test_cache.py         # anchoring, versioning, thread safety
├── test_simulator.py     # GBM math, statistics, correlation, events
├── test_simulator_source.py
├── test_massive.py       # resolvers, fatal latch, plan-tier fixtures
├── test_factory.py       # selection + fallback
├── test_conformance.py   # NEW: contract tests parametrized over both sources
└── test_stream.py        # NEW: SSE framing and version gating
```

---

## 3. `models.py` — the `PriceUpdate` value type

One immutable record per ticker per tick. This is the atom the whole system moves around and
the only thing that crosses the wire to the browser.

```python
"""Data models for market data."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """Immutable snapshot of one ticker's price at a point in time.

    Two distinct notions of "change" live here and must not be confused:

      change / change_percent    - versus the PREVIOUS TICK (~500ms ago).
                                   Drives the flash animation. Meaningless as a
                                   displayed number (typically +/-0.02%).

      change_session /           - versus SESSION_OPEN. This is the "daily change %"
      change_percent_session       the watchlist and positions table display.
    """

    ticker: str
    price: float
    previous_price: float
    session_open: float
    timestamp: float = field(default_factory=time.time)  # Unix seconds

    # Set when the provider computes the session change itself (Massive's
    # todaysChangePerc, which is split- and dividend-adjusted). None means
    # "derive it from session_open".
    provider_change_percent: float | None = None

    # --- tick-over-tick: flash animation only ---

    @property
    def change(self) -> float:
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
        if self.previous_price == 0:
            return 0.0
        return round((self.price - self.previous_price) / self.previous_price * 100, 4)

    @property
    def direction(self) -> str:
        """'up' | 'down' | 'flat' — the CSS class the frontend applies."""
        if self.price > self.previous_price:
            return "up"
        if self.price < self.previous_price:
            return "down"
        return "flat"

    # --- versus session open: the displayed daily change ---

    @property
    def change_session(self) -> float:
        return round(self.price - self.session_open, 4)

    @property
    def change_percent_session(self) -> float:
        """Prefer the provider's adjusted figure; derive only as a fallback."""
        if self.provider_change_percent is not None:
            return round(self.provider_change_percent, 4)
        if self.session_open == 0:
            return 0.0
        return round((self.price - self.session_open) / self.session_open * 100, 4)

    def to_dict(self) -> dict:
        """Serialize for JSON / SSE. This shape is the frontend contract."""
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "session_open": self.session_open,
            "timestamp": self.timestamp,
            "change": self.change,
            "change_percent": self.change_percent,
            "change_session": self.change_session,
            "change_percent_session": self.change_percent_session,
            "direction": self.direction,
        }
```

### Why `provider_change_percent` exists

Deriving `(price − session_open) / session_open` locally is correct right up until a corporate
action. On the morning of a 2-for-1 split, Massive's `prev_day.close` is the *unadjusted*
pre-split price while `price` is post-split, so the local formula prints −50%. Massive's own
`todaysChangePerc` is adjusted and prints the truth.

The simulator has no corporate actions, so it leaves the field `None` and the derived path
runs. One field, two correct behaviours, no branching in any consumer.

### Why `session_open` is a required positional field

A default would let a source forget to set it and silently emit a 0% daily change forever.
Making it required means a source that does not supply an anchor fails at construction, in a
test, rather than in a demo.

---

## 4. `cache.py` — `PriceCache`

```python
"""Thread-safe in-memory price cache."""

from __future__ import annotations

import time
from threading import Lock

from .models import PriceUpdate


class PriceCache:
    """Thread-safe in-memory store of the latest PriceUpdate per ticker.

    Writer: exactly one data source. Readers: SSE stream, trade execution,
    portfolio valuation, LLM context builder.

    Thread-safety is required, not decorative: the Massive poller runs its
    blocking REST call in a worker thread via asyncio.to_thread, so writes
    genuinely arrive off the event loop.
    """

    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self._session_opens: dict[str, float] = {}
        self._lock = Lock()
        self._version = 0  # bumped on every mutation; SSE change detection

    def update(
        self,
        ticker: str,
        price: float,
        timestamp: float | None = None,
        session_open: float | None = None,
        change_percent_session: float | None = None,
    ) -> PriceUpdate:
        """Record a new price. Computes previous_price and direction.

        session_open is captured on the FIRST update for a ticker and preserved
        thereafter, so the daily-change denominator never drifts mid-session. A
        source that knows the true anchor (Massive: prev_day.close) passes it; the
        simulator passes its seed price; a newly added ticker with neither anchors
        at its first observed price, per PLAN.md §6.
        """
        with self._lock:
            ts = timestamp if timestamp is not None else time.time()
            prev = self._prices.get(ticker)
            previous_price = prev.price if prev else price

            if ticker not in self._session_opens:
                self._session_opens[ticker] = session_open if session_open else price
            anchor = self._session_opens[ticker]

            update = PriceUpdate(
                ticker=ticker,
                price=round(price, 2),
                previous_price=round(previous_price, 2),
                session_open=round(anchor, 2),
                timestamp=ts,
                provider_change_percent=change_percent_session,
            )
            self._prices[ticker] = update
            self._version += 1
            return update

    def get(self, ticker: str) -> PriceUpdate | None:
        with self._lock:
            return self._prices.get(ticker)

    def get_price(self, ticker: str) -> float | None:
        """Just the float. This is what trade execution fills against."""
        update = self.get(ticker)
        return update.price if update else None

    def get_all(self) -> dict[str, PriceUpdate]:
        """Shallow copy — safe to iterate without holding the lock."""
        with self._lock:
            return dict(self._prices)

    def remove(self, ticker: str) -> None:
        """Evict on watchlist removal. Drops the session anchor too, so a
        re-added ticker re-anchors rather than resurrecting a stale denominator."""
        with self._lock:
            self._prices.pop(ticker, None)
            self._session_opens.pop(ticker, None)
            self._version += 1

    @property
    def version(self) -> int:
        return self._version

    def __len__(self) -> int:
        with self._lock:
            return len(self._prices)

    def __contains__(self, ticker: str) -> bool:
        with self._lock:
            return ticker in self._prices
```

### Three decisions worth defending

**`session_open` lives in a separate dict, not on the `PriceUpdate`.** `PriceUpdate` is frozen
and replaced on every tick; the anchor must outlive individual updates. Keeping it beside the
prices makes "capture once, preserve thereafter" a two-line invariant instead of a rule every
caller has to remember.

**`remove()` bumps the version.** The shipped implementation does not — which means removing
the last ticker leaves the SSE stream believing nothing changed, and the browser keeps
rendering a ticker the backend has forgotten. Cheap fix, real bug.

**`timestamp if timestamp is not None` rather than `timestamp or time.time()`.** The shipped
version uses `or`, which silently rewrites a legitimate `0.0` timestamp to "now". It will
almost never matter and it costs nothing to be correct.

---

## 5. `interface.py` — `MarketDataSource`

```python
"""Abstract interface for market data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod


class MarketDataSource(ABC):
    """Contract for market data providers.

    Implementations run a background task that writes PriceUpdates into a shared
    PriceCache on their own schedule. Downstream code NEVER calls a data source to
    read a price — it reads the cache. The source is a writer, not a service.

    Lifecycle:
        source = create_market_data_source(cache)
        await source.start(["AAPL", "GOOGL"])       # once, at app startup
        ...
        if await source.supports_ticker("PYPL"):    # before a watchlist insert
            await source.add_ticker("PYPL")
        await source.remove_ticker("GOOGL")
        ...
        await source.stop()                          # at app shutdown
    """

    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Begin producing updates. Call exactly once.

        Must populate the cache for every supported ticker BEFORE returning, so a
        client connecting immediately after startup sees prices rather than an
        empty grid.

        Raises RuntimeError if the source cannot produce data at all (bad API key,
        endpoint not in plan). The caller may then fall back to another source.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Stop the background task and release resources. Idempotent.

        After stop(), the source must not write to the cache again.
        """

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Track a new ticker. No-op if already tracked.

        Should populate the cache promptly. The simulator does so synchronously;
        Massive may not, and the frontend must tolerate a priceless row until the
        next poll (PLAN.md §8: disable the trade button until a price arrives).
        """

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Stop tracking a ticker and evict it from the cache. No-op if absent."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Currently tracked tickers. Synchronous — reads local state only."""

    @abstractmethod
    async def supports_ticker(self, ticker: str) -> bool:
        """Can this source produce prices for this symbol?

        Called by POST /api/watchlist before inserting. Without it, a typo like
        'APPL' silently streams an invented price under the simulator and sits
        permanently priceless under Massive.

        Must not raise: return False on any error. PLAN.md maps False to a 422
        UNSUPPORTED_TICKER, which is explicable to the user; an unhandled exception
        is a 500, which is not.
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable source identity, e.g. 'simulator' or 'massive'.

        Surfaced at /api/health and in the UI, so a demo that silently fell back to
        the simulator is visibly doing so rather than quietly claiming to be live.
        """
```

### Design notes

**`supports_ticker` is async and per-source.** Massive must make a network call; the simulator
answers from a regex. Forcing both through the same async signature keeps the caller
identical — `PLAN.md` §8 step 5 is one `await` either way.

**`get_tickers` is sync.** It reads a local list. Making it async would imply I/O that never
happens and force every caller into an `await` for nothing.

**`start()` raising is how fallback works.** A Basic-tier Massive key cannot reach the snapshot
endpoint at all (§8). Detecting that inside `start()` — on the first, synchronous poll — gives
the factory a clean decision point. Discovering it later, inside a background task, gives us a
terminal that shows an empty grid with no explanation.

**No `get_price` on the interface.** Tempting, and wrong: it would create a second read path
that can disagree with the cache, and would let a trade fill at a price the user never saw.
The cache is the only reader-facing surface.

---

## 6. `seed_prices.py` — simulator data

Data only — no logic, no imports. This file is where a demo is tuned.

```python
"""Seed prices and per-ticker parameters for the market simulator."""

# Realistic starting prices for the default watchlist (as of project creation)
SEED_PRICES: dict[str, float] = {
    "AAPL": 190.00, "GOOGL": 175.00, "MSFT": 420.00, "AMZN": 185.00, "TSLA": 250.00,
    "NVDA": 800.00, "META": 500.00, "JPM": 195.00,  "V": 280.00,   "NFLX": 600.00,
}

# Per-ticker GBM parameters
#   sigma: annualized volatility (higher = more visible movement)
#   mu:    annualized drift / expected return
TICKER_PARAMS: dict[str, dict[str, float]] = {
    "AAPL":  {"sigma": 0.22, "mu": 0.05},
    "GOOGL": {"sigma": 0.25, "mu": 0.05},
    "MSFT":  {"sigma": 0.20, "mu": 0.05},
    "AMZN":  {"sigma": 0.28, "mu": 0.05},
    "TSLA":  {"sigma": 0.50, "mu": 0.03},   # high vol
    "NVDA":  {"sigma": 0.40, "mu": 0.08},   # high vol, strong drift
    "META":  {"sigma": 0.30, "mu": 0.05},
    "JPM":   {"sigma": 0.18, "mu": 0.04},   # low vol (bank)
    "V":     {"sigma": 0.17, "mu": 0.04},   # low vol (payments)
    "NFLX":  {"sigma": 0.35, "mu": 0.05},
}

# Applied to any ticker the user adds that is not listed above.
DEFAULT_PARAMS: dict[str, float] = {"sigma": 0.25, "mu": 0.05}

# Seed price range for unknown tickers.
UNKNOWN_SEED_RANGE: tuple[float, float] = (50.0, 300.0)

CORRELATION_GROUPS: dict[str, set[str]] = {
    "tech":    {"AAPL", "GOOGL", "MSFT", "AMZN", "META", "NVDA", "NFLX"},
    "finance": {"JPM", "V"},
}

INTRA_TECH_CORR = 0.6     # tech names move together
INTRA_FINANCE_CORR = 0.5  # banks / payments move together
CROSS_GROUP_CORR = 0.3    # broad market beta; also the default for unknown tickers
TSLA_CORR = 0.3           # TSLA trades on its own news
```

The σ spread from 0.17 (V) to 0.50 (TSLA) is what makes the grid look heterogeneous — roughly
a 3× difference in tick size between the calmest and busiest rows, visible within seconds.

μ barely matters at demo timescales: over ten minutes drift contributes
`0.05 × (600 / 5,896,800) ≈ 5e-6`, five thousandths of a percent, swamped by diffusion. It is
there for correctness of the model, not visible effect. **Do not inflate μ to "make the
portfolio go up"** — that produces a suspiciously monotonic chart. Volatility is what creates
the impression of a live market, and it already does.

`CROSS_GROUP_CORR = 0.3` does quiet double duty as the default for unknown tickers, so a
symbol the user adds participates in market-wide moves instead of visibly wandering off alone.

---

## 7. `simulator.py` — GBM simulator

### 7.1 The model

```
S(t + dt) = S(t) · exp( (μ − σ²/2)·dt  +  σ·√dt·Z )
```

Why GBM fits:

- **Prices stay positive.** The exponential form makes a negative price impossible. A naive
  additive random walk eventually prints a negative stock price during a long demo, which is a
  uniquely embarrassing bug.
- **Moves are proportional.** A 1% move on NVDA at $800 is $8; on JPM at $195 it's $1.95. That
  falls out of the model rather than needing per-ticker tuning.
- **σ is directly interpretable.** "TSLA has 50% annualized vol" is a claim a trader can check
  against reality, so the parameters can be sanity-checked rather than fiddled with.

The `−σ²/2` term is the Itô correction. Without it `E[S(t)] = S(0)·e^((μ+σ²/2)t)` and prices
drift up faster than μ claims — subtly, but over a long-running demo, visibly.

**Scaling `dt` to a 500 ms tick:**

```python
TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600   # 5,896,800
DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR   # ~8.48e-8
```

252 trading days × 6.5 hours per session. Using calendar seconds (31.5M) would under-scale
volatility by ~5.3× and the grid would look nearly frozen.

Sanity check for AAPL at $190 with σ = 0.22: `σ·√dt ≈ 6.4e-5`, so a one-sigma move is
`190 × 6.4e-5 ≈ $0.012`. Just over a cent. Rounded to 2 dp, most ticks move the price a cent
or two and some do not move it at all — exactly what a real quote screen does. Over a
ten-minute demo (1,200 ticks) accumulated drift is `0.012 × √1200 ≈ $0.42`: a few tenths of a
percent of wander, enough for a sparkline to have shape.

### 7.2 Correlation

Independent draws produce ten unrelated charts; real sectors move together. Draw correlated
normals via **Cholesky decomposition**: given correlation matrix `C` with lower-triangular
factor `L` where `L·Lᵀ = C`, if `Z` is a vector of independent standard normals then `L·Z` has
correlation `C`. One matrix multiply per tick.

`np.linalg.cholesky` raises `LinAlgError` on a matrix that is not positive-definite. The
current block structure is safe **by construction rather than by check**, and one edit to the
constants could break it at runtime — on the `add_ticker` path, where a user typing a symbol
would kill the tick loop. Guard it:

```python
def _rebuild_cholesky(self) -> None:
    """Rebuild the Cholesky factor. Called on add/remove only, never in step()."""
    n = len(self._tickers)
    if n <= 1:
        self._cholesky = None
        return

    corr = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            rho = self._pairwise_correlation(self._tickers[i], self._tickers[j])
            corr[i, j] = corr[j, i] = rho

    try:
        self._cholesky = np.linalg.cholesky(corr)
        return
    except np.linalg.LinAlgError:
        logger.warning("Correlation matrix not positive-definite; applying ridge")

    # Nudge the diagonal until it factors. Degrading to weaker correlation is far
    # better than killing the tick loop.
    for eps in (1e-6, 1e-4, 1e-2):
        try:
            self._cholesky = np.linalg.cholesky(corr + eps * np.eye(n))
            return
        except np.linalg.LinAlgError:
            continue

    logger.error("Cholesky failed; falling back to uncorrelated moves")
    self._cholesky = None
```

`self._cholesky = None` is already handled in `step()` as "use independent draws", so the worst
case is a slightly less convincing demo rather than a dead price feed. Cost is `O(n³)`,
rebuilt only on add/remove; at the 30-ticker watchlist cap that is microseconds and never
touches the hot path.

### 7.3 `GBMSimulator` — pure, synchronous math

The split between `GBMSimulator` and `SimulatorDataSource` is the most valuable structural
decision in the module, for one concrete reason: **the math is testable without an event
loop.** 100,000 ticks run in milliseconds; the same coverage through the async source would
need ~14 hours of wall clock or an elaborate clock mock. Statistical properties need tens of
thousands of samples to test at all, so this is the difference between having those tests and
not having them.

```python
"""GBM-based market simulator."""

from __future__ import annotations

import asyncio
import logging
import math
import random
import re

import numpy as np

from .cache import PriceCache
from .interface import MarketDataSource
from .seed_prices import (
    CORRELATION_GROUPS, CROSS_GROUP_CORR, DEFAULT_PARAMS,
    INTRA_FINANCE_CORR, INTRA_TECH_CORR, SEED_PRICES,
    TICKER_PARAMS, TSLA_CORR, UNKNOWN_SEED_RANGE,
)

logger = logging.getLogger(__name__)

TICKER_RE = re.compile(r"[A-Z]{1,5}")


class GBMSimulator:
    """Correlated Geometric Brownian Motion price simulator.

        S(t+dt) = S(t) * exp((mu - sigma^2/2) * dt + sigma * sqrt(dt) * Z)

    Pure math: no asyncio, no cache, no I/O. See SimulatorDataSource for plumbing.
    """

    TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600   # 5,896,800
    DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR   # ~8.48e-8

    def __init__(
        self,
        tickers: list[str],
        dt: float = DEFAULT_DT,
        event_probability: float = 0.001,
    ) -> None:
        self._dt = dt
        self._event_prob = event_probability

        self._tickers: list[str] = []
        self._prices: dict[str, float] = {}     # unrounded, full precision
        self._seeds: dict[str, float] = {}      # the session_open anchor
        self._params: dict[str, dict[str, float]] = {}
        self._cholesky: np.ndarray | None = None

        for ticker in tickers:
            self._add_ticker_internal(ticker)
        self._rebuild_cholesky()

    # --- Public API ---

    def step(self) -> dict[str, float]:
        """Advance every ticker one step. Returns {ticker: rounded_price}.

        Hot path — called every 500ms.
        """
        n = len(self._tickers)
        if n == 0:
            return {}

        z = np.random.standard_normal(n)
        if self._cholesky is not None:
            z = self._cholesky @ z

        result: dict[str, float] = {}
        for i, ticker in enumerate(self._tickers):
            p = self._params[ticker]
            mu, sigma = p["mu"], p["sigma"]

            drift = (mu - 0.5 * sigma**2) * self._dt
            diffusion = sigma * math.sqrt(self._dt) * z[i]
            self._prices[ticker] *= math.exp(drift + diffusion)

            # Single-stock news: ~0.1% per ticker per tick.
            if random.random() < self._event_prob:
                shock = random.uniform(0.02, 0.05) * random.choice([-1, 1])
                self._prices[ticker] *= 1 + shock
                logger.debug("Event on %s: %+.1f%%", ticker, shock * 100)

            result[ticker] = round(self._prices[ticker], 2)

        return result

    def add_ticker(self, ticker: str) -> None:
        if ticker in self._prices:
            return
        self._add_ticker_internal(ticker)
        self._rebuild_cholesky()

    def remove_ticker(self, ticker: str) -> None:
        if ticker not in self._prices:
            return
        self._tickers.remove(ticker)
        del self._prices[ticker]
        del self._params[ticker]
        del self._seeds[ticker]
        self._rebuild_cholesky()

    def get_price(self, ticker: str) -> float | None:
        price = self._prices.get(ticker)
        return round(price, 2) if price is not None else None

    def get_seed_price(self, ticker: str) -> float | None:
        """The price this ticker started at — the session_open anchor."""
        return self._seeds.get(ticker)

    def set_price(self, ticker: str, price: float) -> None:
        """Re-anchor a tracked ticker. Used only by the optional real-close
        seeding in §7.5, before the first step(). Resets the seed too, so the
        session anchor is the real close rather than the built-in constant."""
        if ticker not in self._prices or price <= 0:
            return
        self._prices[ticker] = price
        self._seeds[ticker] = price

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- Internals ---

    def _add_ticker_internal(self, ticker: str) -> None:
        """Add without rebuilding Cholesky (batch initialization)."""
        if ticker in self._prices:
            return
        seed = SEED_PRICES.get(ticker) or round(random.uniform(*UNKNOWN_SEED_RANGE), 2)
        self._tickers.append(ticker)
        self._prices[ticker] = seed
        self._seeds[ticker] = seed
        # dict() copies deliberately: sharing DEFAULT_PARAMS would let a future
        # per-ticker tweak mutate the default for every unknown ticker at once.
        self._params[ticker] = TICKER_PARAMS.get(ticker) or dict(DEFAULT_PARAMS)

    def _rebuild_cholesky(self) -> None:
        ...   # see §7.2

    @staticmethod
    def _pairwise_correlation(t1: str, t2: str) -> float:
        tech = CORRELATION_GROUPS["tech"]
        finance = CORRELATION_GROUPS["finance"]
        if t1 == "TSLA" or t2 == "TSLA":     # in tech, but trades on its own news
            return TSLA_CORR
        if t1 in tech and t2 in tech:
            return INTRA_TECH_CORR
        if t1 in finance and t2 in finance:
            return INTRA_FINANCE_CORR
        return CROSS_GROUP_CORR
```

Two properties `step()` gets right and must keep:

- **Unrounded internal state, rounded output.** `self._prices` holds full precision; only the
  returned value is rounded to cents. Rounding the state instead would let sub-cent moves get
  quantized away and, at low volatility, could freeze a price permanently.
- **One vectorized draw, then a Python loop.** Full vectorization is possible but would
  complicate the per-ticker event logic for no gain — at n ≤ 30 twice a second this is nowhere
  near a bottleneck.

**On random events.** 10 tickers × 2 ticks/sec × 0.001 = one event roughly every 50 seconds
somewhere on the grid: frequent enough that a viewer sees one during a short demo, rare enough
that it still reads as an event rather than as noise. The shock is applied *after* the GBM step
and *outside* the correlated draw, so it does not propagate through the sector — a 2–5% jump on
one ticker while its peers stay put reads correctly as single-stock news, and that isolation is
what makes it legible. It does not revert: news is repriced, not un-priced. Mean reversion
would be more sophisticated and would make the sparkline harder to read, not easier.

### 7.4 `SimulatorDataSource` — the async wrapper

```python
class SimulatorDataSource(MarketDataSource):
    """MarketDataSource backed by the GBM simulator.

    Owns an asyncio task that calls GBMSimulator.step() on a timer and writes
    the results into the PriceCache.
    """

    def __init__(
        self,
        price_cache: PriceCache,
        update_interval: float = 0.5,
        event_probability: float = 0.001,
        seed_prices: dict[str, float] | None = None,
    ) -> None:
        self._cache = price_cache
        self._interval = update_interval
        self._event_prob = event_probability
        self._seed_overrides = seed_prices or {}   # see §7.5
        self._sim: GBMSimulator | None = None
        self._task: asyncio.Task | None = None

    @property
    def name(self) -> str:
        return "simulator"

    async def start(self, tickers: list[str]) -> None:
        tickers = [t.strip().upper() for t in tickers]
        self._sim = GBMSimulator(tickers, event_probability=self._event_prob)
        for ticker, price in self._seed_overrides.items():
            self._sim.set_price(ticker, price)     # optional real-close seeding

        # Warm the cache BEFORE returning: the interface requires that a client
        # connecting immediately after startup sees prices, not an empty grid.
        for ticker in tickers:
            price = self._sim.get_price(ticker)
            if price is not None:
                self._cache.update(ticker=ticker, price=price, session_open=price)

        self._task = asyncio.create_task(self._run_loop(), name="simulator-loop")
        logger.info("Simulator started: %d tickers, %.2fs tick", len(tickers), self._interval)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Simulator stopped")

    async def add_ticker(self, ticker: str) -> None:
        if not self._sim:
            return
        ticker = ticker.strip().upper()
        self._sim.add_ticker(ticker)
        price = self._sim.get_price(ticker)
        if price is not None:
            # Anchor at the seed price so the new row reads 0.00% rather than a
            # percentage against a denominator it never actually traded at.
            self._cache.update(ticker=ticker, price=price, session_open=price)
        logger.info("Simulator: added %s", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = ticker.strip().upper()
        if self._sim:
            self._sim.remove_ticker(ticker)
        self._cache.remove(ticker)
        logger.info("Simulator: removed %s", ticker)

    def get_tickers(self) -> list[str]:
        return self._sim.get_tickers() if self._sim else []

    async def supports_ticker(self, ticker: str) -> bool:
        """The simulator can price any well-formed symbol. It must still reject
        malformed ones — otherwise POST /api/watchlist {"ticker": "BANANA"} silently
        succeeds and streams an invented price under a name that does not exist."""
        return bool(TICKER_RE.fullmatch(ticker.strip().upper()))

    async def _run_loop(self) -> None:
        while True:
            try:
                if self._sim:
                    for ticker, price in self._sim.step().items():
                        self._cache.update(ticker=ticker, price=price)
            except Exception:
                logger.exception("Simulator step failed")   # never kill the loop
            await asyncio.sleep(self._interval)
```

The broad `except` around the step is intentional and is the one place a bare catch-all earns
its keep: an unhandled exception there silently kills the task, and the app keeps serving a
frozen price grid with no error visible anywhere. Logging and continuing is strictly better.

**Session-open anchoring falls out for free.** `GBMSimulator.__init__` seeds prices, `start()`
writes them to the cache with `session_open=price` before the first `step()`, and the cache
preserves the anchor thereafter. `_run_loop` then calls `update()` *without* `session_open` and
the cache ignores the absence. Per `PLAN.md` §6 the anchor is the seed price, fixed for the
process lifetime. Under the simulator `provider_change_percent` is always `None`, so
`change_percent_session` always takes the derived path — which is why that path carries an
explicit zero-denominator guard.

### 7.5 Optional: seed from real closes

The hard-coded `SEED_PRICES` are accurate as of project creation and drift out of date. A
one-call refinement — and the best use of a Basic-tier Massive key, which cannot drive live
data at all (§8.1):

```python
async def seed_from_massive(api_key: str, tickers: list[str]) -> dict[str, float]:
    """Fetch real previous-day closes to use as simulator seeds. One API call total.

    Uses the grouped-daily endpoint, which is included in ALL plans including Basic —
    unlike the snapshot endpoints. Walks back up to 5 days to skip weekends/holidays.
    Returns {} on any failure; the caller falls back to SEED_PRICES.
    """
    from datetime import date, timedelta

    from massive import RESTClient

    client = RESTClient(api_key=api_key)
    wanted = set(tickers)

    for days_back in range(1, 6):
        day = (date.today() - timedelta(days=days_back)).isoformat()
        try:
            bars = await asyncio.to_thread(client.get_grouped_daily_aggs, date=day)
        except Exception as e:
            logger.warning("Seed fetch for %s failed: %s", day, e)
            continue
        seeds = {b.ticker: b.close for b in bars if b.ticker in wanted and b.close}
        if seeds:
            logger.info("Seeded %d/%d tickers from real closes (%s)",
                        len(seeds), len(wanted), day)
            return seeds

    logger.warning("No real closes available; using built-in seed prices")
    return {}
```

A demo that opens with AAPL at its genuine last close and then moves plausibly is markedly more
convincing than one anchored to a stale constant, and it costs a single API call at startup.

**This is a stretch goal, not core.** It adds a network dependency to the startup path of an
otherwise zero-dependency component, so it must degrade silently — wrap the call in
`asyncio.wait_for(..., timeout=5.0)` and take `{}` as the answer if it does not return
promptly. A slow seed fetch must never delay app startup.

---

## 8. `massive_client.py` — the real data source

### 8.1 What the plan tiers actually allow

| Plan | Price | Rate limit | Recency | Snapshot endpoint |
|---|---|---|---|---|
| **Basic** (free) | $0 | 5 calls/min | **End-of-day only** | ❌ **not included** |
| Starter | $29/mo | Unlimited | 15-min delayed | ✅ |
| Developer | $79/mo | Unlimited | 15-min delayed | ✅ |
| Advanced | $199/mo | Unlimited | **Real-time** | ✅ |
| Business | custom | Unlimited | Real-time | ✅ |

> **⚠️ Correction to `PLAN.md` §6.** `PLAN.md` says *"Free tier (5 calls/min): poll every 15
> seconds"* using the grouped snapshot endpoint. **This does not work.** The snapshot endpoints
> are documented as included in Starter, Developer, Advanced and Business — **not Basic**. A
> free key calling `get_snapshot_all` receives a 403, not data.
>
> Consequences: (a) rate limiting is *only* a Basic-tier concern, since every paid tier is
> unlimited; (b) a Basic key must fall back to the simulator (§9), optionally seeded from real
> closes via §7.5.

**Poll cadence.** Since paid tiers are unlimited, cadence is about not hammering the API for
data that isn't changing rather than about staying under a quota:

| Plan | Data freshness | Interval | Rationale |
|---|---|---|---|
| Starter / Developer | 15-min delayed | **15 s** (default) | The data is stale anyway; polling faster buys nothing |
| Advanced / Business | Real-time | 2 s | Genuinely fresh; close enough to the UI's 500 ms flash cadence |

`15.0` is the right default: correct for the tiers most users hold, harmless on Advanced.

### 8.2 Client basics

```python
from massive import RESTClient

client = RESTClient(api_key="...")   # or omit: reads MASSIVE_API_KEY from the environment
```

Verified against `massive` 2.2.0 in `backend/.venv`:

| Parameter | Default | Note |
|---|---|---|
| `api_key` | `os.getenv("MASSIVE_API_KEY")` | raises `AuthError` if still `None` |
| `base` | `https://api.massive.com` | Polygon.io rebranded 2025-10-30; same API, same keys |
| `connect_timeout` / `read_timeout` | `10.0` s each | |
| `retries` | `3` | urllib3 `Retry`, backoff factor 0.1, on 413/429/499/500/502/503/504 |
| `pagination` | `True` | auto-follows `next_url` |

Two things follow directly:

- **The client is synchronous** — built on `urllib3.PoolManager`, not asyncio. Every call from
  the event loop must go through `asyncio.to_thread(...)` or it stalls the SSE stream for every
  connected client.
- **Our own retry logic should be absent, not layered on top.** A transient 429 is already
  absorbed inside the client and never reaches our code.

### 8.3 The primary endpoint

```
GET /v2/snapshot/locale/us/markets/stocks/tickers
```

**One call returns every requested ticker.** This is the entire reason the call budget works:
10 tickers cost one request, not ten, and adding a ticker to the watchlist adds zero API cost.

```python
snapshots = client.get_snapshot_all(
    market_type=SnapshotMarketType.STOCKS,   # -> "stocks"
    tickers=["AAPL", "GOOGL", "MSFT"],       # a list is joined for you; case-sensitive
)
```

Always pass `tickers` — omitting it returns 10,000+ symbols.

**`TickerSnapshot` — JSON key → attribute renaming.** This table is where naive code goes
wrong:

| Attribute | JSON key | Type | Meaning |
|---|---|---|---|
| `ticker` | `ticker` | `str` | Symbol |
| `todays_change` | `todaysChange` | `float` | Absolute change **vs previous close** |
| `todays_change_percent` | `todaysChangePerc` | `float` | Percent change **vs previous close**, split-adjusted |
| `updated` | `updated` | `int` | **Nanoseconds** since epoch |
| `day` | `day` | `Agg` | Today's bar so far |
| `prev_day` | `prevDay` | `Agg` | Previous session's completed bar |
| `min` | `min` | `MinuteSnapshot` | Most recent one-minute bar |
| `last_trade` | `lastTrade` | `LastTrade` | Latest trade — **plan-dependent, often `None`** |
| `last_quote` | `lastQuote` | `LastQuote` | Latest NBBO quote — **plan-dependent** |

`Agg` exposes `open/high/low/close` (`o/h/l/c`), `volume`, `vwap`, `timestamp` (**ms**).
`LastTrade` exposes `price` (`p`), `size`, `sip_timestamp` (`t`, **ns**), `exchange`, `tape`.

Every field is `Optional` and defaults to `None`. The `@modelclass` decorator builds a
dataclass whose `__init__` silently ignores unknown keyword arguments — so a missing field is
`None`, never an exception, but a **misspelled attribute raises `AttributeError`**.

> **⚠️ Two bugs this exposes in the shipped `massive_client.py`.** `_poll_once` currently does:
>
> ```python
> price = snap.last_trade.price
> timestamp = snap.last_trade.timestamp / 1000.0   # wrong on two counts
> ```
>
> 1. **`LastTrade` has no `timestamp` attribute** — the field is `sip_timestamp`. This raises
>    `AttributeError`, which the surrounding `except (AttributeError, TypeError)` swallows, so
>    **every ticker is silently skipped on every poll** and the cache never fills. The failure
>    is invisible: one `logger.warning` per ticker and an empty watchlist.
> 2. **The unit is nanoseconds, not milliseconds.** Even with the name fixed, `/ 1000.0` yields
>    a timestamp ~31,000 years in the future.
>
> Additionally `snap.last_trade` is `None` on Starter and Basic, so `snap.last_trade.price`
> raises there too. §8.4 is the fix.

### 8.4 Resolving a usable price

Because `last_trade` and `last_quote` are plan-gated and can be `None`, and because bar fields
are `0` or `None` before the session opens, a single field access is not enough. Resolve in
descending order of freshness:

```python
def resolve_price(snap) -> float | None:
    """Best available current price from a TickerSnapshot, freshest first.

    Ordered by staleness, not preference:
      last_trade      - real-time; Developer/Advanced/Business only
      min.close       - up to 60s stale; Starter and above
      day.close       - today's running close; 0/None before the open
      prev_day.close  - always present once the ticker has ever traded
    """
    if snap.last_trade is not None and snap.last_trade.price:
        return snap.last_trade.price
    if snap.min is not None and snap.min.close:
        return snap.min.close
    if snap.day is not None and snap.day.close:
        return snap.day.close
    if snap.prev_day is not None and snap.prev_day.close:
        return snap.prev_day.close
    return None
```

The truthiness checks (`if ....price`, not `is not None`) are deliberate: Massive returns `0`
— not `null` — for bar fields on a ticker that has not traded yet today. `0` is not a tradeable
price and `0.0` is falsy, so one check handles both cases.

### 8.5 The session-open anchor

`PLAN.md` §6 asks for `session_open` and specifies *"the day's opening price from the snapshot
response"* — i.e. `day.open`. **Use `prev_day.close` instead.** Three reasons:

1. `day.open` is `0` before the market opens and during pre-market, so the percentage is
   undefined or infinite for a meaningful part of the day.
2. Massive's own `todaysChangePerc` is computed against the previous close, so anchoring on
   `day.open` makes our number disagree with the provider's on the same screen.
3. Previous close is what every finance site means by "daily change %". Open-to-current is a
   different statistic that merely looks similar.

Better still: don't compute it when Massive already did.

```python
def resolve_session_change(snap) -> tuple[float | None, float | None]:
    """(session_open_anchor, change_percent_session) — prefer the provider's own numbers."""
    anchor = snap.prev_day.close if snap.prev_day and snap.prev_day.close else None
    if snap.todays_change_percent is not None:
        return anchor, snap.todays_change_percent   # authoritative, split-adjusted
    return anchor, None                             # caller derives from anchor
```

**Timestamp units — one table, because they are inconsistent:**

| Source | Unit | Convert |
|---|---|---|
| `TickerSnapshot.updated` | nanoseconds | `/ 1e9` |
| `LastTrade.sip_timestamp` | nanoseconds | `/ 1e9` |
| `Agg.timestamp` (`day`, `prev_day`) | **milliseconds** | `/ 1e3` |
| `MinuteSnapshot.timestamp` | **milliseconds** | `/ 1e3` |
| `GroupedDailyAgg.timestamp` | **milliseconds** | `/ 1e3` |

Mixing these up is the single most likely bug in this module, and it has already happened once.
Guard it: assert any converted timestamp lands within ±7 days of `time.time()`, and fall back
to `time.time()` if not. A wrong-by-a-millennium timestamp should degrade to "now", not poison
the chart's x-axis.

### 8.6 The corrected poller

```python
"""Massive (Polygon.io) API client for real market data."""

from __future__ import annotations

import asyncio
import logging
import time

from massive import RESTClient
from massive.rest.models import SnapshotMarketType

from .cache import PriceCache
from .interface import MarketDataSource

logger = logging.getLogger(__name__)

NS_PER_SEC = 1_000_000_000
MAX_TIMESTAMP_SKEW = 7 * 24 * 3600   # seconds


class MassiveDataSource(MarketDataSource):
    """MarketDataSource backed by the Massive REST snapshot endpoint.

    One grouped snapshot call per poll covers every watched ticker, so watchlist
    size does not affect API cost.
    """

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float = 15.0,
    ) -> None:
        self._api_key = api_key
        self._cache = price_cache
        self._interval = poll_interval
        self._tickers: list[str] = []
        self._client: RESTClient | None = None
        self._task: asyncio.Task | None = None
        self._fatal = False          # 401/403 latch — stop retrying forever
        self._probes: dict[str, object] = {}   # supports_ticker() snapshot reuse

    @property
    def name(self) -> str:
        return "massive"

    async def start(self, tickers: list[str]) -> None:
        self._client = RESTClient(api_key=self._api_key)
        self._tickers = [t.strip().upper() for t in tickers]

        await self._poll_once()      # fail fast, before the app claims to be live
        if self._fatal:
            raise RuntimeError("Massive unavailable for this API key; see log")

        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info("Massive poller started: %d tickers, %.1fs interval",
                    len(self._tickers), self._interval)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._client = None
        logger.info("Massive poller stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = ticker.strip().upper()
        if ticker in self._tickers:
            return
        self._tickers.append(ticker)

        # POST /api/watchlist just called supports_ticker(), which fetched a
        # snapshot for exactly this symbol. Reuse it so the row gets a price
        # immediately instead of waiting up to a full poll interval — zero extra
        # API calls. If there is no probe, the next poll fills it in.
        snap = self._probes.pop(ticker, None)
        if snap is not None:
            self._apply(snap)
        logger.info("Massive: added %s (priced=%s)", ticker, snap is not None)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = ticker.strip().upper()
        self._tickers = [t for t in self._tickers if t != ticker]
        self._probes.pop(ticker, None)
        self._cache.remove(ticker)
        logger.info("Massive: removed %s", ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    async def supports_ticker(self, ticker: str) -> bool:
        """True if Massive returns a snapshot for this symbol.

        Costs one extra API call, on a user-initiated action only (watchlist add),
        so it does not meaningfully compete with the polling budget. Never raises:
        PLAN.md maps False to a 422 UNSUPPORTED_TICKER, which is explicable to the
        user; an unhandled exception is a 500, which is not.
        """
        ticker = ticker.strip().upper()
        if not self._client:
            return False
        try:
            snaps = await asyncio.to_thread(
                self._client.get_snapshot_all,
                market_type=SnapshotMarketType.STOCKS,
                tickers=[ticker],
            )
        except Exception as e:
            logger.warning("supports_ticker(%s) failed (%s); assuming unsupported", ticker, e)
            return False

        for snap in snaps:
            if snap.ticker == ticker and resolve_price(snap) is not None:
                self._probes[ticker] = snap      # primed for add_ticker()
                return True
        return False

    # --- Internals ---

    async def _poll_loop(self) -> None:
        while not self._fatal:
            await asyncio.sleep(self._interval)
            await self._poll_once()
        logger.error("Massive poller halted: unrecoverable error")

    async def _poll_once(self) -> None:
        if not self._tickers or not self._client:
            return
        try:
            snaps = await asyncio.to_thread(
                self._client.get_snapshot_all,
                market_type=SnapshotMarketType.STOCKS,
                tickers=list(self._tickers),
            )
        except Exception as e:
            status = getattr(e, "status", None) or getattr(e, "code", None)
            if status in (401, 403):
                self._fatal = True
                logger.error(
                    "Massive rejected the API key (HTTP %s). Snapshot endpoints "
                    "require Starter or above; Basic keys cannot drive live data.",
                    status,
                )
            else:
                logger.error("Massive poll failed: %s", e)
            return   # never clear the cache — a stale price beats an empty terminal

        if not snaps:
            # Snapshot data is cleared daily ~3:30 AM ET and repopulates from ~4:00 AM ET.
            # A poll in that window legitimately returns nothing.
            logger.debug("Massive returned no snapshots (pre-market or unknown symbols)")
            return

        updated = 0
        for snap in snaps:
            if self._apply(snap):
                updated += 1
        logger.debug("Massive poll: updated %d/%d tickers", updated, len(self._tickers))

    def _apply(self, snap) -> bool:
        """Write one snapshot into the cache. Returns True if it produced a price."""
        price = resolve_price(snap)
        if price is None:
            logger.debug("No usable price for %s; leaving cache entry stale", snap.ticker)
            return False
        anchor, pct = resolve_session_change(snap)
        self._cache.update(
            ticker=snap.ticker,
            price=price,
            timestamp=self._safe_timestamp(snap.updated),
            session_open=anchor,
            change_percent_session=pct,
        )
        return True

    @staticmethod
    def _safe_timestamp(raw_ns) -> float:
        """Nanoseconds -> seconds, with a sanity guard. Never poisons the chart axis."""
        now = time.time()
        if not raw_ns:
            return now
        ts = raw_ns / NS_PER_SEC
        return ts if abs(ts - now) < MAX_TIMESTAMP_SKEW else now
```

Four properties worth naming, because they are the difference between this and the shipped
version:

- **A failed poll leaves the previous price in place.** The cache is never cleared on error, so
  a network blip shows a briefly stale price rather than an empty terminal.
- **`_fatal` latches.** 401 and 403 fail identically forever; retrying them every 15 seconds
  for the life of the process produces an infinite error log and never recovers.
- **`start()` raises on a fatal first poll**, giving the factory a clean place to fall back to
  the simulator rather than starting a source that will never produce data.
- **`supports_ticker` primes `add_ticker`.** The `POST /api/watchlist` flow calls both back to
  back; reusing the probe snapshot removes the one asymmetry that leaks into the UI, at no API
  cost.

### 8.7 Supporting endpoints — what we use and what we don't

| Endpoint | Client method | Verdict |
|---|---|---|
| Full market snapshot | `get_snapshot_all` | **Primary.** One call, all tickers |
| Daily market summary (grouped) | `get_grouped_daily_aggs` | **Basic-tier fallback** (§7.5). All plans; one call, every US ticker; EOD only. `{date}` must be a trading day — weekends return `resultsCount: 0`, so walk back ~5 days |
| Single-ticker snapshot | `get_snapshot_ticker` | Not needed — `get_snapshot_all` with one symbol does the same job and matches the poll path |
| Previous day bar | `get_previous_close_agg` | All plans, but **one call per ticker**. Prefer grouped daily whenever more than one symbol is needed |
| Custom bars | `get_aggs` / `list_aggs` | Not in the MVP — `PLAN.md` §10 builds the detail chart from SSE-accumulated ticks. This is the endpoint for "show me a real 1-month chart" later |
| Last trade | `get_last_trade` | Deliberately unused — one call per ticker, excluded from Basic and Starter, and the snapshot already embeds it |
| Unified snapshot (`/v3`) | `list_universal_snapshots` | Noted, not adopted: same plan exclusions, and FinAlly is equities-only, so it is a second code path for no new capability |

---

## 9. `factory.py` — selection and fallback

```python
"""Factory for creating market data sources."""

from __future__ import annotations

import asyncio
import logging
import os

from .cache import PriceCache
from .interface import MarketDataSource
from .massive_client import MassiveDataSource
from .simulator import SimulatorDataSource, seed_from_massive

logger = logging.getLogger(__name__)


def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    """Select a data source from the environment.

    MASSIVE_API_KEY set and non-empty -> MassiveDataSource
    otherwise                         -> SimulatorDataSource

    Returns an UNSTARTED source; the caller awaits start(tickers).
    """
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if api_key:
        interval = float(os.environ.get("MASSIVE_POLL_INTERVAL", "15.0"))
        logger.info("Market data source: Massive (real data), %.1fs poll", interval)
        return MassiveDataSource(api_key=api_key, price_cache=price_cache,
                                 poll_interval=interval)
    logger.info("Market data source: GBM simulator")
    return SimulatorDataSource(price_cache=price_cache)


async def start_market_data(price_cache: PriceCache, tickers: list[str]) -> MarketDataSource:
    """Create and start a source, falling back to the simulator if the real one
    cannot serve this API key.

    A Basic-tier Massive key has no snapshot access at all (§8.1), so "key is
    present" does not imply "key works". Falling back keeps the demo alive and —
    because the failure is logged and the source names itself at /api/health —
    keeps it honest about what the user is looking at.
    """
    source = create_market_data_source(price_cache)
    try:
        await source.start(tickers)
        return source
    except Exception as e:
        if isinstance(source, SimulatorDataSource):
            raise                       # the simulator failing is a real bug
        logger.error("Massive unavailable (%s); falling back to the simulator", e)
        await source.stop()

        # Stretch goal (§7.5): anchor the simulator to real closes, which a Basic
        # key CAN fetch. Degrade silently — startup must not block on the network.
        seeds: dict[str, float] = {}
        api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
        if api_key:
            try:
                seeds = await asyncio.wait_for(
                    seed_from_massive(api_key, tickers), timeout=5.0
                )
            except Exception:
                logger.debug("Real-close seeding unavailable; using built-in seeds")

        fallback = SimulatorDataSource(price_cache=price_cache, seed_prices=seeds)
        await fallback.start(tickers)
        return fallback
```

The environment variable stays the *only* switch, per `PLAN.md` §5 — the fallback is an error
path, not a second configuration knob. `MASSIVE_POLL_INTERVAL` is an optional tuning override
for Advanced-tier users, not part of the documented setup.

---

## 10. `stream.py` — the SSE endpoint

### The wire format

**One event per tick carries every ticker**, as a JSON object keyed by symbol — *not* one event
per ticker:

```
retry: 1000

data: {"AAPL": {"ticker":"AAPL","price":190.50,"previous_price":190.40,
                "session_open":190.00,"timestamp":1755993600.12,
                "change":0.10,"change_percent":0.05,"change_session":0.50,
                "change_percent_session":0.26,"direction":"up"},
       "GOOGL": {...}}
```

Two properties the frontend must be built around:

- **The stream only emits when the cache version changes.** An idle cache sends nothing, so
  clients must not treat silence as a disconnect.
- **The connection opens with `retry: 1000`**, so `EventSource` reconnects automatically after
  ~1 s.
- **The stream carries prices only.** Watchlist membership changes are never pushed here — the
  mutating watchlist endpoints and the chat response return the full new list instead
  (`PLAN.md` §8).

### Implementation

```python
"""SSE streaming endpoint for live price updates."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache

logger = logging.getLogger(__name__)


def create_stream_router(price_cache: PriceCache) -> APIRouter:
    """Build the SSE router bound to a specific PriceCache.

    The router is constructed INSIDE the factory. The shipped version decorates a
    module-level router, so calling the factory twice (which every test that builds
    an app does) registers duplicate routes on a shared object.
    """
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        return StreamingResponse(
            _generate_events(price_cache, request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",   # disable proxy buffering
            },
        )

    return router


async def _generate_events(
    price_cache: PriceCache,
    request: Request,
    interval: float = 0.5,
    heartbeat: float = 15.0,
) -> AsyncGenerator[str, None]:
    """Yield SSE frames whenever the cache version changes.

    Version gating means an idle cache costs nothing. A comment heartbeat keeps
    intermediary proxies from timing out an otherwise silent connection; comment
    lines are ignored by EventSource and never reach the client's handler.
    """
    yield "retry: 1000\n\n"

    last_version = -1
    since_emit = 0.0
    client = request.client.host if request.client else "unknown"
    logger.info("SSE client connected: %s", client)

    try:
        while True:
            if await request.is_disconnected():
                logger.info("SSE client disconnected: %s", client)
                break

            version = price_cache.version
            if version != last_version:
                last_version = version
                prices = price_cache.get_all()
                if prices:
                    payload = json.dumps({t: u.to_dict() for t, u in prices.items()})
                    yield f"data: {payload}\n\n"
                    since_emit = 0.0
            else:
                since_emit += interval
                if since_emit >= heartbeat:
                    yield ": keepalive\n\n"
                    since_emit = 0.0

            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("SSE stream cancelled: %s", client)
```

---

## 11. FastAPI wiring

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.market import PriceCache, create_stream_router, start_market_data

DEFAULT_TICKERS = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA",
                   "NVDA", "META", "JPM", "V", "NFLX"]

# The cache is created at import time so the router can bind to it. The SOURCE is
# started in lifespan — creating a background task at import time would attach it
# to whichever loop happens to be current, which under pytest is the wrong one.
price_cache = PriceCache()


@asynccontextmanager
async def lifespan(app: FastAPI):
    tickers = load_watchlist_from_db() or DEFAULT_TICKERS   # the DB is the authority
    source = await start_market_data(price_cache, tickers)

    app.state.price_cache = price_cache
    app.state.market_source = source
    try:
        yield
    finally:
        await source.stop()


app = FastAPI(lifespan=lifespan)
app.include_router(create_stream_router(price_cache))


@app.get("/api/health")
async def health():
    """Names the live source, so a demo that fell back to the simulator says so."""
    source = app.state.market_source
    return {
        "status": "ok",
        "market_source": source.name,
        "tickers": len(source.get_tickers()),
        "priced": len(price_cache),
    }
```

The watchlist comes from the database, not from a constant — `DEFAULT_TICKERS` seeds an empty
database (`PLAN.md` §7) and is the fallback only if that seeding has not happened yet.

### The watchlist endpoints

`PLAN.md` §8's validation sequence maps one-to-one onto the interface:

```python
import re

TICKER_RE = re.compile(r"[A-Z]{1,5}")
MAX_WATCHLIST = 30


@router.post("/api/watchlist")
async def add_to_watchlist(body: TickerBody, request: Request):
    source: MarketDataSource = request.app.state.market_source

    ticker = body.ticker.strip().upper()                      # 1. normalize
    if not TICKER_RE.fullmatch(ticker):                       # 2. format
        raise ApiError("INVALID_TICKER", 400, f"'{body.ticker}' is not a valid symbol.")

    current = db_watchlist_tickers()
    if ticker in current:                                     # 3. idempotent
        return {"tickers": current}
    if len(current) >= MAX_WATCHLIST:                         # 4. capacity
        raise ApiError("WATCHLIST_FULL", 409, "Watchlist is full (30 tickers).")
    if not await source.supports_ticker(ticker):              # 5. source check
        raise ApiError("UNSUPPORTED_TICKER", 422,
                       f"No market data available for {ticker}.")

    db_insert_watchlist(ticker)                               # 6. persist, then track
    await source.add_ticker(ticker)
    return {"tickers": db_watchlist_tickers()}


@router.delete("/api/watchlist/{ticker}")
async def remove_from_watchlist(ticker: str, request: Request):
    source: MarketDataSource = request.app.state.market_source
    ticker = ticker.strip().upper()
    db_delete_watchlist(ticker)          # idempotent: absent ticker is not an error
    await source.remove_ticker(ticker)   # drops it from the cache too
    return {"tickers": db_watchlist_tickers()}
```

Order matters in `POST`: the DB insert precedes `add_ticker` so that a crash between them
leaves a watched-but-unpriced ticker (self-healing on the next restart) rather than a
priced-but-unwatched one (a leak that survives restarts).

Both mutating endpoints return the **complete new watchlist** — the SSE stream never carries
membership, so this response is the frontend's only refresh signal.

---

## 12. Downstream consumers

Every consumer reads the cache. Three examples cover the three shapes of read.

**Trade execution** — fills at the cached price, never a client-supplied one:

```python
def execute_trade(cache: PriceCache, ticker: str, quantity: float, side: str):
    price = cache.get_price(ticker)
    if price is None:
        # Reachable for a just-added ticker under Massive's poll interval; PLAN.md §8
        # has the frontend disable the trade button until a price arrives.
        raise ApiError("UNKNOWN_TICKER", 404, f"No current price for {ticker}.")
    ...
```

**Portfolio valuation** — one helper shared by `/api/portfolio` and the LLM context builder, per
`PLAN.md` §13.3 S5:

```python
def value_portfolio(cache: PriceCache, positions, cash: float) -> dict:
    """Server-side valuation. The client recomputes this live between fetches;
    the client is authoritative for anything displayed."""
    prices = cache.get_all()
    holdings = []
    total = cash
    for pos in positions:
        update = prices.get(pos.ticker)
        current = update.price if update else pos.avg_cost   # unpriced -> at cost
        value = current * pos.quantity
        total += value
        holdings.append({
            "ticker": pos.ticker,
            "quantity": pos.quantity,
            "avg_cost": pos.avg_cost,
            "current_price": current,
            "market_value": round(value, 2),
            "unrealized_pnl": round((current - pos.avg_cost) * pos.quantity, 2),
            "change_percent": update.change_percent_session if update else 0.0,
        })
    return {"cash_balance": cash, "positions": holdings, "total_value": round(total, 2)}
```

Valuing an unpriced position at cost is the least-wrong default: it contributes zero P&L rather
than zero value, so a just-added ticker awaiting its first Massive poll does not make the
portfolio appear to have lost money.

Note `change_percent_session`, not `change_percent` — the positions table shows a daily change,
not a 500 ms one.

**SSE stream** — §10. `to_dict()` gaining keys is purely additive.

---

## 13. Testing

### 13.1 Conformance — the tests that keep the abstraction real

Parametrized over **both** implementations. Without these, the ABC is aspirational.

```python
@pytest.fixture(params=["simulator", "massive"])
def source(request, cache):
    if request.param == "simulator":
        return SimulatorDataSource(price_cache=cache, update_interval=0.01)
    src = MassiveDataSource(api_key="test", price_cache=cache, poll_interval=0.01)
    src._client = FakeRESTClient()      # see §13.4
    return src


async def test_start_populates_cache(source, cache):
    await source.start(["AAPL", "GOOGL"])
    assert cache.get("AAPL") is not None      # no sleep — start() must be sufficient
    assert cache.get("GOOGL") is not None
    await source.stop()


async def test_remove_evicts(source, cache):
    await source.start(["AAPL", "GOOGL"])
    await source.remove_ticker("GOOGL")
    assert "GOOGL" not in cache
    assert "GOOGL" not in source.get_tickers()
    await source.stop()


async def test_stop_is_idempotent(source, cache):
    await source.start(["AAPL"])
    await source.stop()
    await source.stop()


async def test_unsupported_ticker_rejected(source):
    assert await source.supports_ticker("NOTAREALTICKER123") is False


async def test_source_names_itself(source):
    assert source.name in {"simulator", "massive"}
```

`test_start_populates_cache` contains no `sleep`. That is the point: "the cache is warm when
`start()` returns" is a contract clause, and a test that slept first would pass even if the
clause were violated.

### 13.2 Models and cache — source-free

| Behaviour | Assertion |
|---|---|
| First update | `previous_price == price`, `direction == "flat"` |
| `session_open` capture | set on the first update, unchanged across later ones |
| `session_open` fallback | no anchor supplied → first observed price |
| Provider percent wins | `provider_change_percent=5.0` → `change_percent_session == 5.0` |
| Derived percent | `None` → computed from `session_open` |
| Zero-division guards | `session_open == 0` and `previous_price == 0` → `0.0`, not an exception |
| Version bump | increments on `update()` **and** on `remove()` |
| Re-add re-anchors | remove then update → new anchor, not the old one |
| Thread safety | N threads × M updates → `version == N*M`, no lost writes |

**`to_dict()` is a frozen contract** — the frontend parses it, and a silent rename breaks the
terminal with no server-side error:

```python
def test_to_dict_keys_are_stable():
    keys = set(PriceUpdate("AAPL", 190.5, 190.4, 190.0).to_dict())
    assert keys == {
        "ticker", "price", "previous_price", "session_open", "timestamp",
        "change", "change_percent", "change_session",
        "change_percent_session", "direction",
    }
```

### 13.3 Simulator

**Statistical properties** run against `GBMSimulator` directly with `event_probability=0.0` so
shocks do not contaminate the estimates, and a fixed seed for reproducibility.

```python
def test_realized_volatility_matches_sigma():
    """Realized vol of log returns should match the configured sigma."""
    np.random.seed(42)
    sim = GBMSimulator(["AAPL"], event_probability=0.0)
    prev = sim.get_price("AAPL")
    log_returns = []
    for _ in range(200_000):
        price = sim.step()["AAPL"]
        log_returns.append(math.log(price / prev))
        prev = price

    realized = np.std(log_returns) / math.sqrt(GBMSimulator.DEFAULT_DT)
    assert 0.19 < realized < 0.25          # configured sigma for AAPL is 0.22


def test_tech_stocks_are_correlated():
    """AAPL/MSFT log returns should correlate near INTRA_TECH_CORR (0.6)."""
    np.random.seed(7)
    sim = GBMSimulator(["AAPL", "MSFT"], event_probability=0.0)
    prev = {"AAPL": sim.get_price("AAPL"), "MSFT": sim.get_price("MSFT")}
    a, m = [], []
    for _ in range(50_000):
        p = sim.step()
        a.append(math.log(p["AAPL"] / prev["AAPL"]))
        m.append(math.log(p["MSFT"] / prev["MSFT"]))
        prev = p

    assert 0.5 < np.corrcoef(a, m)[0, 1] < 0.7
```

Rounding to cents adds quantization noise, which is why the bands are wide and the sample
counts large — ±0.1 around the target is expected, not sloppy. Both tests are worth their
runtime: they are the only thing standing between "the simulator is correlated" as a claim and
as a fact.

**Behavioural:**

| Property | Test |
|---|---|
| Prices stay positive | 100k steps, assert `> 0` |
| Prices stay finite | assert not `isnan` / `isinf` after a long run |
| Tick size is plausible | ≥95% of consecutive moves under 0.5% |
| Drift invisible short-term | 1,000 steps, mean log return within a few σ of 0 |
| Unknown ticker seeded | `add_ticker("ZZZZ")` → price in `[50, 300]`, `DEFAULT_PARAMS` |
| Default params not shared | mutate one unknown ticker's params; another is unaffected |
| Add rebuilds Cholesky | `_cholesky.shape == (n+1, n+1)` |
| Remove rebuilds Cholesky | shape shrinks; ticker gone from `get_tickers()` |
| Single ticker | `n == 1` → `_cholesky is None`, `step()` still works |
| Empty simulator | `step()` returns `{}`, no exception |
| Non-PD matrix survives | patch a correlation to 1.5; assert no raise, feed still ticks |
| Events fire | `event_probability=1.0`, one step, price moved ≥2% |
| Events disabled | `event_probability=0.0`, 10k steps, no move >1% |
| Determinism | same `np.random.seed` **and** `random.seed` → identical series |

The determinism test is what makes every statistical test above reproducible. It requires
seeding **both** `numpy.random` (the GBM draw) and stdlib `random` (the shock logic) — the
module uses both, and seeding only one leaves the series non-reproducible in a way that looks
like flakiness.

**Async source** tests use `update_interval=0.01` so a handful of ticks takes milliseconds:

| Property | Test |
|---|---|
| `start()` warms the cache | assert immediately, **no sleep** |
| Loop writes | sleep 0.05 s, assert `cache.version` increased |
| `stop()` halts writes | stop, record version, sleep, assert unchanged |
| `add_ticker` prices immediately | assert `cache.get(t)` right after the await |
| `remove_ticker` evicts | absent from cache and `get_tickers()` |
| `session_open` anchoring | equals the seed price after many ticks |
| `supports_ticker` | `"AAPL"` → True; `"BANANA"`, `""`, `" aapl "` handled correctly |
| Step exception survives | patch `step` to raise; assert the task is still alive |

The last one is the one people skip and the one that matters: it verifies the catch-all in
`_run_loop` actually keeps the feed alive rather than letting one bad tick silently end the
demo.

### 13.4 Massive — without an API key

`RESTClient` is instantiated inside `start()`, so tests inject a fake by assigning
`source._client` directly. **Build fixtures from the real model classes, never `Mock()`** — a
`Mock` answers *any* attribute access with a new `Mock`, which is exactly how the
`.timestamp` bug passed code review.

```python
from massive.rest.models.snapshot import TickerSnapshot


def make_snapshot(ticker="AAPL", price=192.05, prev_close=190.20, **overrides):
    """Build a TickerSnapshot from raw JSON, exercising the real from_dict mapping."""
    data = {
        "ticker": ticker,
        "todaysChange": round(price - prev_close, 4),
        "todaysChangePerc": round((price - prev_close) / prev_close * 100, 4),
        "updated": 1755993600123456789,
        "day":     {"o": 190.10, "h": 192.40, "l": 189.75, "c": price, "v": 41203311},
        "prevDay": {"o": 188.00, "h": 190.90, "l": 187.60, "c": prev_close, "v": 52118400},
        "min":     {"o": 191.98, "h": 192.10, "l": 191.90, "c": price,
                    "v": 18422, "t": 1755993540000},
        "lastTrade": {"p": price, "s": 100, "t": 1755993599123456789, "x": 4, "z": 3},
    }
    data.update(overrides)
    return TickerSnapshot.from_dict(data)


class FakeRESTClient:
    """Stands in for massive.RESTClient. Synchronous, like the real thing."""

    def __init__(self, snapshots=None, error=None):
        self._snapshots = snapshots
        self._error = error
        self.calls: list[list[str]] = []

    def get_snapshot_all(self, market_type, tickers):
        self.calls.append(list(tickers))
        if self._error:
            raise self._error
        if self._snapshots is not None:
            return [s for s in self._snapshots if s.ticker in tickers]
        return [make_snapshot(t) for t in tickers]
```

Cases worth covering explicitly:

| Case | Fixture | Asserts |
|---|---|---|
| Full data (Developer+) | all fields | `last_trade.price` is used |
| Starter plan | drop `lastTrade` / `lastQuote` | falls back to `min.close` |
| Pre-market | `day` all zeros, no `min` | falls back to `prev_day.close` |
| Never traded | `prevDay` zeros too | `resolve_price` returns `None`, cache untouched |
| Bad timestamp | `updated: 1` | `_safe_timestamp` returns ≈`time.time()` |
| Session anchor | normal snapshot | `session_open == prev_day.close` |
| Provider percent used | `todaysChangePerc: 3.5` | `change_percent_session == 3.5` |
| 403 on first poll | client raises with `status=403` | `_fatal` set; `start()` raises `RuntimeError` |
| 403 mid-run | raises on the second poll | loop exits; cache retains the last price |
| Transient error | raises `ConnectionError` | `_fatal` stays False; cache retains the last price |
| Empty response | `[]` | logs at debug; **does not clear** the cache |
| One call per poll | 10 tickers | `len(fake.calls[-1]) == 10`, one entry appended |
| Probe reuse | `supports_ticker` then `add_ticker` | price present with **no additional** `get_snapshot_all` |

### 13.5 Factory and stream

| Case | Assertion |
|---|---|
| No key | returns `SimulatorDataSource`, `name == "simulator"` |
| Whitespace-only key | treated as absent |
| Key present | returns `MassiveDataSource` |
| Massive `start()` raises | `start_market_data` returns a started simulator |
| Simulator `start()` raises | `start_market_data` re-raises — never masks a real bug |
| SSE first frame | begins with `retry: 1000` |
| SSE frames on change | update the cache → one `data:` frame containing every ticker |
| SSE silent when idle | no version change → no `data:` frame |
| SSE payload keys | parsed JSON matches the `to_dict()` key set |
| Router factory | called twice → two independent routers, no duplicate routes |

---

## 14. Implementation checklist — delta from shipped code

| File | Change | Why |
|---|---|---|
| `models.py` | Add `session_open` (required) and `provider_change_percent`; add `change_session` / `change_percent_session`; add four `to_dict` keys | `PLAN.md` §6 — "daily change %" has no data source today |
| `cache.py` | Track `_session_opens`; extend `update()` with `session_open` / `change_percent_session`; bump `_version` in `remove()`; `timestamp is not None` instead of `or` | Anchor capture; removal is currently invisible to SSE |
| `interface.py` | Add `supports_ticker()` and the `name` property | `PLAN.md` §6/§8 — watchlist validation and honest health reporting |
| `seed_prices.py` | Add `UNKNOWN_SEED_RANGE` | Constant currently inline in `simulator.py` |
| `simulator.py` | Track `_seeds` + `get_seed_price()`; pass `session_open` on `start()` and `add_ticker()`; normalize case; ridge-guard `_rebuild_cholesky`; implement `supports_ticker` and `name` | Anchoring; a non-PD matrix must not kill the tick loop |
| `massive_client.py` | **Fix `last_trade.timestamp` → `sip_timestamp`, ns not ms**; add the §8.4 fallback chain; pass `prev_day.close` + `todaysChangePerc`; latch 401/403; raise from `start()`; add probe reuse; implement `supports_ticker` and `name` | The current code skips every ticker on every poll — a total failure of the real-data path |
| `factory.py` | Add `start_market_data()` with simulator fallback; optional poll-interval override | A present key is not a working key |
| `stream.py` | Build the `APIRouter` inside the factory; add a comment heartbeat | Module-level router double-registers; silent connections can be dropped by proxies |
| `tests/market/` | Add `test_conformance.py` and `test_stream.py`; replace `Mock()` snapshot fixtures with `TickerSnapshot.from_dict`; extend cache/model tests for anchoring | A `Mock` answers any attribute, which is how the `.timestamp` bug survived review |

**Build order.** `models` → `cache` → `interface` → `simulator` → `massive_client` → `factory`
→ `stream`, testing each before moving on. The `massive_client.py` timestamp fix should be
first among the bug fixes: it is not a rough edge, it is a total failure of the real-data path
that the current tests cannot see.

---

## 15. Configuration and error reference

### Environment variables

| Variable | Default | Effect |
|---|---|---|
| `MASSIVE_API_KEY` | *(unset)* | Set and non-empty → Massive; otherwise → simulator |
| `MASSIVE_POLL_INTERVAL` | `15.0` | Optional. Seconds between snapshot polls; use `2.0` on Advanced |

### Tuning constants

| Constant | Location | Default | Effect |
|---|---|---|---|
| `update_interval` | `SimulatorDataSource` | `0.5` s | Tick cadence |
| `event_probability` | `SimulatorDataSource` | `0.001` | ~1 shock per 50 s across 10 tickers |
| `DEFAULT_DT` | `GBMSimulator` | `8.48e-8` | Must match `update_interval` / trading-year seconds |
| `interval` | `_generate_events` | `0.5` s | SSE poll of the cache version |
| `heartbeat` | `_generate_events` | `15.0` s | Comment frame on an idle stream |
| `MAX_TIMESTAMP_SKEW` | `massive_client` | 7 days | Beyond this, fall back to `time.time()` |

### Failure modes

| Symptom | Cause | Handling |
|---|---|---|
| `AuthError` at construction | `api_key` is `None` | Factory already checks for a non-empty key |
| HTTP 401 | Key invalid or revoked | **Fatal** — latch, log loudly, fall back to the simulator |
| HTTP 403 | Endpoint not in plan (Basic + snapshot) | **Fatal for this source** — §9 fallback |
| HTTP 429 | Rate limited | urllib3 retries 3× inside the client; if it still surfaces, the poll is skipped |
| Empty `tickers` array | All symbols invalid, or the 3:30–4:00 AM ET repopulation window | Not an error — log at `debug`, retry next interval |
| Ticker silently absent | Unknown or delisted symbol | Leave the cache entry stale; surface via `supports_ticker()` |
| `last_trade is None` | Plan lacks trade data | §8.4 fallback chain — **not** an error |
| Timeout | Network | urllib3 retries; then swallow and retry next interval |
| Simulator step raises | A bug | Logged with traceback; the loop continues |
| Non-PD correlation matrix | Bad constants | Ridge, then uncorrelated moves; the feed never dies |
| Cache read of an unknown ticker | Just added, or removed | `None` → `UNKNOWN_TICKER` (404) on trade; valued at cost in the portfolio |

### API-cost accounting (Massive)

| Action | Calls |
|---|---|
| Startup | 1 |
| Each poll | 1, regardless of watchlist size |
| Add a ticker | 1 (`supports_ticker`; `add_ticker` reuses the probe) |
| Remove a ticker | 0 |
| Trade, portfolio read, LLM context | 0 — all read the cache |

At the 15 s default that is 4 calls/minute plus user actions — comfortably within every paid
tier's unlimited budget, and the reason watchlist size never affects cost.
