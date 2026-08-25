# Market Simulator — Approach and Code Structure

> How FinAlly fabricates believable stock prices when no `MASSIVE_API_KEY` is present.
> This is the **default** path: most users will never set a key, so the simulator is what
> the demo actually looks like.
>
> Implements the `MarketDataSource` contract in `MARKET_INTERFACE.md`. Largely built today
> in `backend/app/market/simulator.py` and `seed_prices.py`; §8 and §9 describe the gaps.

---

## 1. What "Believable" Means Here

The simulator is not a research tool. Nobody backtests against it. Its job is narrower and
more demanding in a different way: **a person who trades for a living should watch the screen
for a minute and not notice anything wrong.**

That sets four requirements, in priority order:

1. **Prices move on the right scale.** A $190 stock ticking to $190.03 reads as real. Ticking
   to $193 twice a second reads as a random number generator.
2. **Stocks move together.** If AAPL, MSFT, and GOOGL wander independently, the watchlist
   looks like ten unrelated charts. Real tech names rise and fall as a group, and this is the
   single most recognizable property of a real market grid.
3. **Volatility varies by name.** TSLA should visibly jump around more than JPM. Uniform
   volatility makes every row look identical.
4. **Something happens occasionally.** A flat grid is boring. A 3% move on one ticker every
   minute or so gives the demo a heartbeat.

Explicit non-goals, so scope does not creep: no order book, no bid/ask spread, no volume
modelling, no market hours, no earnings calendar, no mean reversion, no fat tails. Market
orders only (`PLAN.md` §3) removes the need for all of it.

---

## 2. The Model — Geometric Brownian Motion

GBM is the standard model for equity prices and is a one-liner:

```
S(t + dt) = S(t) · exp( (μ − σ²/2)·dt  +  σ·√dt·Z )
```

| Term | Meaning |
|---|---|
| `S(t)` | Current price |
| `μ` | Annualized drift — expected return |
| `σ` | Annualized volatility |
| `dt` | Time step, as a fraction of a trading year |
| `Z` | Standard normal draw, correlated across tickers |

Why it fits:

- **Prices stay positive.** The exponential form makes a negative price impossible. A naive
  additive random walk eventually prints a negative stock price during a long demo, which is
  a uniquely embarrassing bug.
- **Moves are proportional.** A 1% move on NVDA at $800 is $8; on JPM at $195 it's $1.95.
  This falls out of the model rather than needing per-ticker tuning.
- **σ is directly interpretable.** "TSLA has 50% annualized vol" is a statement a trader can
  check against reality, so the parameters can be sanity-checked rather than fiddled with.

The `−σ²/2` term is the Itô correction. Without it, `E[S(t)] = S(0)·e^((μ + σ²/2)t)` and prices
drift upward faster than `μ` claims — subtly, but over a long-running demo, visibly. With it,
`E[S(t)] = S(0)·e^(μt)`, so `μ` means what it says.

### Scaling `dt` to a 500 ms tick

```python
TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600   # 5,896,800
DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR   # ~8.48e-8
```

252 trading days × 6.5 hours per session. Using calendar seconds (31.5M) instead would
under-scale volatility by ~5.3×, and the grid would look nearly frozen.

Sanity check on the resulting tick size for AAPL at $190 with σ = 0.22:

```
σ·√dt = 0.22 × √8.48e-8 ≈ 6.4e-5
one-sigma move ≈ $190 × 6.4e-5 ≈ $0.012
```

Just over a cent per tick. Rounded to 2dp, most ticks move the price by one or two cents and
some do not move it at all — which is exactly what a real quote screen does. Over a
ten-minute demo (1,200 ticks) the accumulated one-sigma drift is `0.012 × √1200 ≈ $0.42`, so
a stock wanders a few tenths of a percent. Realistic, and enough for a sparkline to have
shape.

---

## 3. Correlation — the detail that sells it

Independent draws produce ten unrelated charts. Real sectors move together. The fix is to
draw correlated normals via **Cholesky decomposition**.

Given a correlation matrix `C` and its lower-triangular factor `L` where `L·Lᵀ = C`, if `Z` is
a vector of independent standard normals then `L·Z` has correlation `C`. One matrix multiply
per tick.

```python
z_independent = np.random.standard_normal(n)
z_correlated = self._cholesky @ z_independent
```

### The correlation structure

```python
CORRELATION_GROUPS = {
    "tech":    {"AAPL", "GOOGL", "MSFT", "AMZN", "META", "NVDA", "NFLX"},
    "finance": {"JPM", "V"},
}

INTRA_TECH_CORR    = 0.6   # tech names move together
INTRA_FINANCE_CORR = 0.5   # banks/payments move together
CROSS_GROUP_CORR   = 0.3   # broad market beta
TSLA_CORR          = 0.3   # TSLA does its own thing
```

These are roughly right against real daily-return correlations: large-cap tech pairs sit
around 0.5–0.7, financials around 0.4–0.6, cross-sector around 0.2–0.4. TSLA is carved out at
0.3 because it genuinely trades on its own news more than on the sector's.

`CROSS_GROUP_CORR = 0.3` also does quiet work as the **default for unknown tickers**. Anything
a user adds gets broad-market beta, so a newly added symbol participates in market-wide moves
instead of visibly wandering off on its own.

### Positive-definiteness — the failure mode to guard

`np.linalg.cholesky` raises `LinAlgError` on a matrix that is not positive-definite. The
current block structure is safe, but it is safe by construction rather than by check, and one
future edit to the constants could break it at runtime — on the `add_ticker` path, where a
user typing a symbol crashes the tick loop.

```python
def _rebuild_cholesky(self) -> None:
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
    except np.linalg.LinAlgError:
        # Not positive-definite. Nudge the diagonal ("ridge") until it factors.
        # Degrading to uncorrelated moves is far better than killing the tick loop.
        logger.warning("Correlation matrix not positive-definite; applying ridge")
        for eps in (1e-6, 1e-4, 1e-2):
            try:
                self._cholesky = np.linalg.cholesky(corr + eps * np.eye(n))
                return
            except np.linalg.LinAlgError:
                continue
        logger.error("Cholesky failed; falling back to uncorrelated moves")
        self._cholesky = None
```

`self._cholesky = None` is already handled in `step()` as "use independent draws", so the
worst case is a slightly less convincing demo rather than a dead price feed.

Cost is `O(n³)`, rebuilt only on add/remove. At n = 30 (the watchlist cap) that is
microseconds, and it never touches the hot path.

---

## 4. Per-Ticker Parameters

```python
SEED_PRICES = {
    "AAPL": 190.00, "GOOGL": 175.00, "MSFT": 420.00, "AMZN": 185.00, "TSLA": 250.00,
    "NVDA": 800.00, "META": 500.00, "JPM": 195.00,  "V": 280.00,   "NFLX": 600.00,
}

TICKER_PARAMS = {
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

DEFAULT_PARAMS = {"sigma": 0.25, "mu": 0.05}
```

The σ spread from 0.17 (V) to 0.50 (TSLA) is what makes the grid look heterogeneous — a
roughly 3× difference in tick size between the calmest and busiest rows, which is visible
within seconds.

μ barely matters at demo timescales. Over ten minutes, drift contributes
`0.05 × (600/5,896,800) ≈ 5e-6` — five thousandths of a percent, swamped by diffusion. It is
there for correctness of the model, not for visible effect. Do not be tempted to inflate μ to
"make the portfolio go up"; that produces a suspiciously monotonic chart. Volatility is what
creates the impression of a live market, and it already does.

### Unknown tickers

```python
self._prices[ticker] = SEED_PRICES.get(ticker, random.uniform(50.0, 300.0))
self._params[ticker] = TICKER_PARAMS.get(ticker, dict(DEFAULT_PARAMS))
```

`dict(DEFAULT_PARAMS)` copies deliberately — sharing the dict would let a future per-ticker
parameter tweak mutate the default for every unknown ticker at once.

A random seed price in a plausible range is the right call. Anything more clever (hashing the
symbol to a "stable" price) invents a fact about a real company, and getting AAPL's price
wrong in a demo is worse than getting an unfamiliar ticker's price arbitrary.

---

## 5. Random Events

```python
if random.random() < self._event_prob:          # 0.001 per ticker per tick
    shock = random.uniform(0.02, 0.05)
    self._prices[ticker] *= 1 + shock * random.choice([-1, 1])
```

Rate arithmetic: 10 tickers × 2 ticks/sec × 0.001 = **one event every ~50 seconds** somewhere
on the grid. Frequent enough that a viewer sees one during a short demo; rare enough that it
still reads as an event rather than as noise.

A 2–5% jump on one ticker while its correlated peers stay put reads correctly as
single-stock news — an earnings surprise or an analyst note — which is precisely the impression
wanted. The shock is applied *after* the GBM step and outside the correlated draw, so it does
not propagate through the sector, and that isolation is what makes it legible.

Note that the shock permanently repricess the stock; it does not revert. That is realistic (news
is repriced, not un-priced) and simple. Mean reversion would be more sophisticated and would
make the sparkline harder to read, not easier.

---

## 6. Code Structure

```
backend/app/market/
├── seed_prices.py    # data only: seeds, params, correlation groups & coefficients
├── simulator.py      # GBMSimulator (pure math) + SimulatorDataSource (async plumbing)
├── models.py         # PriceUpdate            — shared with Massive
├── cache.py          # PriceCache             — shared with Massive
├── interface.py      # MarketDataSource ABC   — shared with Massive
└── factory.py        # env-var selection      — shared with Massive
```

### The split that matters: `GBMSimulator` vs `SimulatorDataSource`

**`GBMSimulator`** is pure, synchronous math. No asyncio, no cache, no I/O. It holds prices
and parameters and exposes `step() -> dict[str, float]`.

**`SimulatorDataSource`** is the `MarketDataSource` implementation. It owns the asyncio task,
calls `step()` on a timer, and writes results to the `PriceCache`.

This separation is the most valuable structural decision in the module, for one concrete
reason: **the math is testable without a running event loop.**

```python
def test_prices_stay_positive_over_a_long_run():
    sim = GBMSimulator(["AAPL"], event_probability=0.0)
    for _ in range(100_000):
        sim.step()
    assert sim.get_price("AAPL") > 0
```

100,000 ticks in milliseconds. The same coverage through the async source would require ~14
hours of wall clock or an elaborate clock mock. Statistical properties — drift, volatility,
realized correlation — need tens of thousands of samples to test at all, so this is the
difference between having those tests and not.

### `GBMSimulator` surface

```python
class GBMSimulator:
    def __init__(self, tickers: list[str], dt: float = DEFAULT_DT,
                 event_probability: float = 0.001) -> None: ...

    def step(self) -> dict[str, float]:
        """Advance every ticker one step. Returns {ticker: rounded_price}.
        Hot path — called every 500ms. No allocation beyond the result dict."""

    def add_ticker(self, ticker: str) -> None:      # rebuilds Cholesky
    def remove_ticker(self, ticker: str) -> None:   # rebuilds Cholesky
    def get_price(self, ticker: str) -> float | None:
    def get_seed_price(self, ticker: str) -> float | None:   # <- new, see §7
    def get_tickers(self) -> list[str]:
```

### `step()` — the hot path

```python
def step(self) -> dict[str, float]:
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

        drift = (mu - 0.5 * sigma ** 2) * self._dt
        diffusion = sigma * math.sqrt(self._dt) * z[i]
        self._prices[ticker] *= math.exp(drift + diffusion)

        if random.random() < self._event_prob:
            shock = random.uniform(0.02, 0.05) * random.choice([-1, 1])
            self._prices[ticker] *= 1 + shock

        result[ticker] = round(self._prices[ticker], 2)

    return result
```

Two things it gets right and should keep:

- **Unrounded internal state, rounded output.** `self._prices` holds full precision; only the
  returned value is rounded to cents. Rounding the state instead would let sub-cent moves get
  quantized away and, at low volatility, could freeze a price permanently.
- **One vectorized draw, then a Python loop.** Full vectorization is possible but would
  complicate the per-ticker event logic for no gain — at n ≤ 30 twice a second, this is
  nowhere near a bottleneck.

### `SimulatorDataSource` — the async wrapper

```python
class SimulatorDataSource(MarketDataSource):
    def __init__(self, price_cache, update_interval=0.5, event_probability=0.001): ...

    @property
    def name(self) -> str:
        return "simulator"

    async def start(self, tickers: list[str]) -> None:
        self._sim = GBMSimulator(tickers, event_probability=self._event_prob)
        # Warm the cache BEFORE returning: MARKET_INTERFACE.md requires that a
        # client connecting immediately after startup sees prices, not an empty grid.
        for ticker in tickers:
            price = self._sim.get_price(ticker)
            if price is not None:
                self._cache.update(ticker=ticker, price=price, session_open=price)
        self._task = asyncio.create_task(self._run_loop(), name="simulator-loop")

    async def supports_ticker(self, ticker: str) -> bool:
        """The simulator can price any well-formed symbol. It must still reject
        malformed ones — otherwise POST /api/watchlist {"ticker": "BANANA"} silently
        succeeds and streams an invented price under a name that does not exist."""
        return bool(re.fullmatch(r"[A-Z]{1,5}", ticker.strip().upper()))

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
its place: an unhandled exception here silently kills the task, and the app keeps serving a
frozen price grid with no error anywhere. Logging and continuing is strictly better.

---

## 7. Session Open

`PLAN.md` §6 specifies: *"Simulator — the ticker's seed price, captured when the ticker is
first tracked. Fixed for the process lifetime."*

The mechanism is already in place. `PriceCache.update()` captures `session_open` on the first
write for a ticker and preserves it thereafter (`MARKET_INTERFACE.md` §4), so the simulator's
only job is to pass the seed price on that first call — which `start()` above does.

Because `GBMSimulator.__init__` seeds prices and `start()` writes them to the cache before the
first `step()`, the anchor is the seed price by construction. `_run_loop` then calls
`update()` without `session_open`, and the cache ignores it.

One addition is needed for `add_ticker`:

```python
async def add_ticker(self, ticker: str) -> None:
    if not self._sim:
        return
    ticker = ticker.strip().upper()
    self._sim.add_ticker(ticker)
    price = self._sim.get_price(ticker)
    if price is not None:
        # Anchor at the seed price so the new row shows 0.00% rather than a
        # percentage against a denominator it never actually traded at.
        self._cache.update(ticker=ticker, price=price, session_open=price)
```

`GBMSimulator.get_seed_price()` is listed in §6 for the case where a ticker is removed and
re-added within one process: `remove_ticker` drops the simulated price, so re-adding re-seeds
from `SEED_PRICES` anyway and the two are equal. The accessor exists so the invariant is
explicit rather than incidental.

Under the simulator, `provider_change_percent` is always `None` — there is no corporate-action
adjustment to preserve — so `change_percent_session` is always derived. That path must
therefore be correct, which is why it carries an explicit zero-denominator guard.

---

## 8. Optional: Seed From Real Prices

The hard-coded `SEED_PRICES` are accurate as of project creation and drift out of date. A
one-call refinement, applicable when a Massive key exists but cannot drive live data — which
is exactly the Basic-tier case in `MASSIVE_API.md` §6:

```python
async def seed_from_massive(api_key: str, tickers: list[str]) -> dict[str, float]:
    """Fetch real previous-day closes to use as simulator seeds. One API call total.

    Uses the grouped-daily endpoint, which is included in ALL plans including free —
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

Value: a demo that opens with AAPL at its genuine last close and then moves plausibly is
markedly more convincing than one anchored to a stale constant, and it costs a single API call
at startup.

**This is a stretch goal, not core.** It adds a network dependency to the startup path of what
is otherwise a zero-dependency component, and it must degrade silently — a slow or failed seed
fetch must never delay or block app startup. Wrap it in a short timeout, and take `{}` as the
answer if it does not return promptly.

---

## 9. Testing

### Statistical properties — the tests that prove the model

Run against `GBMSimulator` directly, with `event_probability=0.0` so shocks do not contaminate
the estimates, and a fixed `np.random.seed` for reproducibility.

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
```

```python
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

Rounding to cents adds quantization noise to these estimates, which is why the tolerance bands
are wide and the sample counts large. Both tests are worth their runtime: they are the only
thing standing between "the simulator is correlated" as a claim and as a fact.

Note the widened band on `test_tech_stocks_are_correlated` — the estimate is a sample
correlation over rounded prices, so ±0.1 around the target is expected, not sloppy.

### Behavioural tests

| Property | Test |
|---|---|
| Prices stay positive | 100k steps, assert `> 0` |
| Prices stay finite | assert not `isnan` / `isinf` after a long run |
| Tick size is plausible | ≥95% of consecutive moves under 0.5% |
| Drift is not visible short-term | 1,000 steps, mean log return within a few σ of 0 |
| Unknown ticker seeded | `add_ticker("ZZZZ")` → price in `[50, 300]`, `DEFAULT_PARAMS` |
| Add rebuilds Cholesky | `_cholesky.shape == (n+1, n+1)` |
| Remove rebuilds Cholesky | shape shrinks; ticker gone from `get_tickers()` |
| Single ticker | `n == 1` → `_cholesky is None`, `step()` still works |
| Empty simulator | `step()` returns `{}`, no exception |
| Events fire | `event_probability=1.0`, one step, price moved ≥2% |
| Events disabled | `event_probability=0.0`, 10k steps, no move >1% |
| Determinism | same `np.random.seed` + `random.seed` → identical price series |

The determinism test is worth calling out: it is what makes every statistical test above
reproducible, and it is what lets an E2E test assert on specific numbers if one ever needs to.
It requires seeding **both** `numpy.random` (the GBM draw) and the stdlib `random` (the shock
logic) — the module uses both, and seeding only one leaves the series non-reproducible in a
way that looks like flakiness.

### Async source tests

Use a tiny `update_interval` (0.01s) so a handful of ticks takes milliseconds:

| Property | Test |
|---|---|
| `start()` warms the cache | assert immediately, **no sleep** |
| Loop writes | sleep 0.05s, assert `cache.version` increased |
| `stop()` halts writes | stop, record version, sleep, assert unchanged |
| `stop()` idempotent | call twice, no exception |
| `add_ticker` prices immediately | assert `cache.get(t)` right after the await |
| `remove_ticker` evicts | ticker absent from cache and `get_tickers()` |
| `session_open` anchoring | equals the seed price after many ticks |
| `supports_ticker` | `"AAPL"` → True; `"BANANA"`, `""`, `"aapl "` handled correctly |
| Step exception survives | patch `step` to raise; assert the task is still alive |

The last one is the one people skip and the one that matters: it verifies the catch-all in
`_run_loop` actually keeps the feed alive rather than letting a single bad tick silently end
the demo.
