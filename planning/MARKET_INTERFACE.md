# Market Data Interface — Unified Design

> The single contract every part of FinAlly uses to get a stock price, regardless of whether
> that price came from Massive or from the simulator.
>
> Companion documents: `MASSIVE_API.md` (the real data source), `MARKET_SIMULATOR.md` (the
> synthetic one). `MARKET_DATA_SUMMARY.md` describes what is built today; this document
> describes what it should become.

---

## 1. The Shape

```
                    create_market_data_source(cache)
                                 │
                    MASSIVE_API_KEY set?
                    ┌────────────┴────────────┐
                   yes                        no
                    │                          │
          MassiveDataSource            SimulatorDataSource
          (REST poll, ~15s)            (GBM tick, 500ms)
                    │                          │
                    └────────────┬─────────────┘
                                 │  writes
                                 ▼
                          ┌─────────────┐
                          │ PriceCache  │  ← the only source of price truth
                          └─────────────┘
                                 │  reads
              ┌──────────────────┼──────────────────┬─────────────────┐
              ▼                  ▼                  ▼                 ▼
      SSE /api/stream    trade execution    portfolio value     LLM context
```

Four properties this buys, each of which is load-bearing:

- **Nothing downstream knows which source is running.** No `if simulator:` anywhere outside
  the factory. Swapping sources is an environment variable, not a code path.
- **Writers push, readers pull.** The sources never call into the SSE layer and the SSE layer
  never calls a source. They are coupled only through the cache, so a slow poll cannot stall a
  stream and a disconnecting client cannot perturb the data feed.
- **One price, one place.** Trade fills, portfolio valuation, the LLM's context, and the
  browser all read the same `PriceCache` entry. They cannot disagree — which is exactly why
  `PLAN.md` §8 makes `GET /api/watchlist` return membership only.
- **The cache is source-independent state.** Multi-user, replay, and a recorded-tape test
  source all slot in without touching a consumer.

---

## 2. `PriceUpdate` — the value type

One immutable record per ticker per tick. This is the atom the whole system moves around, and
the only thing that crosses the wire to the browser.

```python
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
    timestamp: float = field(default_factory=time.time)   # Unix seconds

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

Deriving `(price − session_open) / session_open` locally is correct until a corporate action.
On the morning of a 2-for-1 split, Massive's `prev_day.close` is the *unadjusted* pre-split
price while `price` is post-split, so the local formula prints −50%. Massive's own
`todaysChangePerc` is adjusted and prints the truth.

The simulator has no corporate actions, so it leaves the field `None` and the derived path
runs. One field, two correct behaviours, no branching in the consumer.

### Deltas from the shipped `models.py`

| Change | Reason |
|---|---|
| `session_open` added (required) | `PLAN.md` §6 — no data source for "daily change %" exists today |
| `provider_change_percent` added | Preserve Massive's split-adjusted figure |
| `change_session`, `change_percent_session` | The numbers actually rendered |
| `to_dict()` gains four keys | Frontend contract; purely additive |

Making `session_open` a required positional field is deliberate — a default would let a
source forget to set it and silently emit a 0% daily change forever.

---

## 3. `MarketDataSource` — the abstract interface

```python
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

        Should populate the cache promptly. The simulator can do so synchronously;
        Massive cannot, and the frontend must tolerate a priceless row until the
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
        the simulator is visibly doing so rather than quietly lying about being live.
        """
```

### Design notes

**`supports_ticker` is async and per-source.** Massive must make a network call; the
simulator answers from a regex. Forcing both through the same async signature keeps the
caller identical — `PLAN.md` §8 step 5 is one `await` either way.

**`get_tickers` is sync.** It reads a local list. Making it async would imply I/O that never
happens and force every caller into an `await` for nothing.

**`start()` raising is how fallback works.** A Basic-tier Massive key cannot reach the
snapshot endpoint (`MASSIVE_API.md` §2). Detecting that inside `start()` — on the first,
synchronous poll — gives the factory a clean decision point. Discovering it later, inside a
background task, gives us a terminal that shows an empty grid with no explanation.

**No `get_price` on the interface.** Tempting, and wrong: it would create a second read path
that can disagree with the cache and would let a trade fill at a price the user never saw.
The cache is the only reader-facing surface.

---

## 4. `PriceCache` — shared state

The shipped implementation is sound. The changes below carry `session_open` and keep the
first-tick semantics honest.

```python
import time
from threading import Lock


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
        self._version = 0        # bumped on every write; SSE change detection

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

### Two decisions worth defending

**`session_open` lives in a separate dict, not on the `PriceUpdate`.** `PriceUpdate` is frozen
and replaced on every tick; the anchor must outlive individual updates. Keeping it beside the
prices makes "capture once, preserve thereafter" a two-line invariant instead of a rule every
caller has to remember.

**`remove()` bumps the version.** The shipped version does not, which means removing the last
ticker leaves the SSE stream believing nothing changed — the browser keeps showing a ticker
the backend has forgotten. Cheap fix, real bug.

---

## 5. The factory

```python
import logging
import os

logger = logging.getLogger(__name__)


def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    """Select a data source from the environment.

    MASSIVE_API_KEY set and non-empty -> MassiveDataSource
    otherwise                         -> SimulatorDataSource

    Returns an UNSTARTED source; the caller awaits start(tickers).
    """
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if api_key:
        logger.info("Market data source: Massive (real data)")
        return MassiveDataSource(api_key=api_key, price_cache=price_cache)
    logger.info("Market data source: GBM simulator")
    return SimulatorDataSource(price_cache=price_cache)


async def start_market_data(price_cache: PriceCache, tickers: list[str]) -> MarketDataSource:
    """Create and start a source, falling back to the simulator if the real one
    cannot serve this API key.

    A Basic-tier Massive key has no snapshot access at all (MASSIVE_API.md §2), so
    "key is present" does not imply "key works". Falling back keeps the demo alive
    and — because the failure is logged and the source names itself at /api/health —
    keeps it honest about what the user is looking at.
    """
    source = create_market_data_source(price_cache)
    try:
        await source.start(tickers)
        return source
    except Exception as e:
        if isinstance(source, SimulatorDataSource):
            raise                            # the simulator failing is a real bug
        logger.error("Massive unavailable (%s); falling back to the simulator", e)
        await source.stop()
        fallback = SimulatorDataSource(price_cache=price_cache)
        await fallback.start(tickers)
        return fallback
```

The environment variable stays the *only* switch, per `PLAN.md` §5 — the fallback is an error
path, not a second configuration knob.

---

## 6. Wiring into FastAPI

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

DEFAULT_TICKERS = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA",
                   "NVDA", "META", "JPM", "V", "NFLX"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    cache = PriceCache()
    tickers = load_watchlist_from_db() or DEFAULT_TICKERS   # DB is the authority

    source = await start_market_data(cache, tickers)
    app.state.price_cache = cache
    app.state.market_source = source

    yield

    await source.stop()


app = FastAPI(lifespan=lifespan)
app.include_router(create_stream_router(app.state.price_cache))
```

The watchlist comes from the database, not from a constant — `DEFAULT_TICKERS` seeds an empty
database (`PLAN.md` §7) and is the fallback only if that seeding has not happened yet.

### How the watchlist endpoints use the interface

`PLAN.md` §8's validation sequence maps one-to-one onto the interface:

```python
@router.post("/api/watchlist")
async def add_to_watchlist(body: TickerBody, request: Request):
    source: MarketDataSource = request.app.state.market_source

    ticker = body.ticker.strip().upper()                      # 1. normalize
    if not re.fullmatch(r"[A-Z]{1,5}", ticker):               # 2. format
        raise ApiError("INVALID_TICKER", 400, f"'{body.ticker}' is not a valid symbol.")

    current = db_watchlist_tickers()
    if ticker in current:                                     # 3. idempotent
        return {"tickers": current}
    if len(current) >= 30:                                    # 4. capacity
        raise ApiError("WATCHLIST_FULL", 409, "Watchlist is full (30 tickers).")
    if not await source.supports_ticker(ticker):              # 5. source check
        raise ApiError("UNSUPPORTED_TICKER", 422,
                       f"No market data available for {ticker}.")

    db_insert_watchlist(ticker)                               # 6. persist, then track
    await source.add_ticker(ticker)
    return {"tickers": db_watchlist_tickers()}
```

Order matters: the DB insert precedes `add_ticker` so that a crash between them leaves a
watched-but-unpriced ticker (self-healing on next restart) rather than a priced-but-unwatched
one (a leak that survives restarts).

---

## 7. Reading Prices Downstream

Every consumer reads the cache. Three examples, covering the three shapes of read.

**Trade execution** — fills at the cached price, never a client-supplied one:

```python
def execute_trade(cache: PriceCache, ticker: str, quantity: float, side: str):
    price = cache.get_price(ticker)
    if price is None:
        raise ApiError("UNKNOWN_TICKER", 404, f"No current price for {ticker}.")
    ...
```

**Portfolio valuation** — one helper, shared by `/api/portfolio` and the LLM context builder,
per `PLAN.md` §13.3 S5:

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

Valuing an unpriced position at cost is the least-wrong default: it contributes zero P&L
rather than zero value, so a just-added ticker awaiting its first Massive poll does not make
the portfolio appear to have lost money.

**SSE stream** — unchanged from the shipped `stream.py`; `to_dict()` simply carries four more
keys. The version-gated loop already skips idle ticks.

---

## 8. Source Comparison

| | Simulator | Massive |
|---|---|---|
| `name` | `"simulator"` | `"massive"` |
| Update mechanism | in-process GBM tick | REST poll in a worker thread |
| Cadence | 500 ms | 15 s (2 s on Advanced) |
| `start()` populates cache | synchronously, before returning | first poll, before returning |
| `add_ticker` price latency | immediate | up to one poll interval |
| `supports_ticker` | regex `^[A-Z]{1,5}$` | snapshot lookup (1 API call) |
| `session_open` | seed price, fixed for process life | `prev_day.close`, per session |
| `provider_change_percent` | `None` (derived) | `todaysChangePerc` (adjusted) |
| Failure mode | none — pure computation | network, auth, plan limits |
| External dependency | none | api.massive.com |

The asymmetry that leaks into the UI is `add_ticker` latency. `PLAN.md` §8 already handles it:
disable the trade button until a price arrives. That rule is source-independent — it just
never fires under the simulator.

---

## 9. Testing the Contract

**Conformance tests run against both implementations.** Parametrizing over sources is what
keeps the abstraction real rather than aspirational:

```python
@pytest.fixture(params=["simulator", "massive"])
def source(request, cache):
    if request.param == "simulator":
        return SimulatorDataSource(price_cache=cache, update_interval=0.01)
    src = MassiveDataSource(api_key="test", price_cache=cache)
    src._client = FakeRESTClient()      # see MASSIVE_API.md §9
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
```

`test_start_populates_cache` contains no `sleep`. That is the point: "the cache is warm when
`start()` returns" is a contract clause, and a test that sleeps first would pass even if the
clause were violated.

**Cache and model tests** are source-free:

| Behaviour | Assertion |
|---|---|
| First update | `previous_price == price`, `direction == "flat"` |
| `session_open` capture | set on first update, unchanged across later updates |
| `session_open` fallback | absent anchor → first observed price |
| Provider percent wins | `provider_change_percent=5.0` → `change_percent_session == 5.0` |
| Derived percent | `None` → computed from `session_open` |
| Zero-division guards | `session_open == 0` → `0.0`, not `ZeroDivisionError` |
| Version bump | increments on `update()` **and** on `remove()` |
| Thread safety | N threads × M updates → version == N×M, no lost writes |

**`to_dict()` is a frozen contract.** Assert the exact key set — the frontend parses it, and a
silent rename breaks the terminal with no server-side error:

```python
def test_to_dict_keys_are_stable():
    keys = set(PriceUpdate("AAPL", 190.5, 190.4, 190.0).to_dict())
    assert keys == {
        "ticker", "price", "previous_price", "session_open", "timestamp",
        "change", "change_percent", "change_session",
        "change_percent_session", "direction",
    }
```

---

## 10. Migration From What Is Built

`backend/app/market/` already implements most of this. The delta:

| File | Change | Why |
|---|---|---|
| `models.py` | Add `session_open`, `provider_change_percent`, two properties, four `to_dict` keys | `PLAN.md` §6 — daily change % has no data source today |
| `cache.py` | Track `_session_opens`; extend `update()`; bump version in `remove()` | Anchor capture; removal is currently invisible to SSE |
| `interface.py` | Add `supports_ticker()` and `name` | `PLAN.md` §6/§8 — watchlist validation |
| `simulator.py` | Pass seed price as `session_open`; implement the two new members | Anchor is the seed price |
| `massive_client.py` | **Fix `last_trade.timestamp` → `sip_timestamp`, ns not ms**; add the §5 fallback chain; pass `prev_day.close` + `todaysChangePerc`; latch 401/403; raise from `start()`; implement the two new members | `MASSIVE_API.md` §3 — the current code skips every ticker on every poll |
| `factory.py` | Add `start_market_data()` with simulator fallback | A present key is not a working key |
| `stream.py` | None | `to_dict()` change is additive |
| `tests/market/` | Extend for the above; replace `Mock()` snapshot fixtures with `TickerSnapshot.from_dict` | A `Mock` answers any attribute, which is how the `.timestamp` bug survived review |

The `massive_client.py` timestamp bug is the one to fix first: it is not a rough edge, it is a
total failure of the real-data path that the current tests cannot see.
