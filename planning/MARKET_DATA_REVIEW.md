# Market Data Backend — Code Review

**Date:** 2026-08-27
**Scope:** `backend/app/market/` (8 modules, 349 statements), `backend/tests/market/` (73 tests), `backend/pyproject.toml`, `backend/market_data_demo.py`
**Reference contracts:** `planning/PLAN.md` §6/§8/§12, `planning/MARKET_INTERFACE.md`, `planning/MARKET_SIMULATOR.md`, `planning/MASSIVE_API.md`

---

## Verdict

**The simulator path is production-quality. The Massive path is non-functional and its tests do not detect that.**

The architecture is genuinely good — the strategy pattern, the cache-as-single-source-of-truth, and the factory-by-env-var are all clean and match the design documents. The GBM mathematics is not just plausible but *verifiably correct* (measured below). But `MASSIVE_API_KEY=<key>` currently produces an application that streams **no prices at all**, silently, at WARNING level. Every snapshot is dropped by an exception handler.

`MARKET_DATA_SUMMARY.md` describes the subsystem as "Complete, tested, reviewed, all issues resolved." That is accurate for the default (simulator) configuration only.

| Area | Assessment |
|---|---|
| Architecture & module boundaries | Strong |
| Simulator (`simulator.py`, `seed_prices.py`) | Strong — math verified |
| Cache (`cache.py`, `models.py`) | Solid; incomplete vs. PLAN §6 |
| Massive client (`massive_client.py`) | **Broken** — see C1/C2 |
| SSE stream (`stream.py`) | Latent defect + untested (33% coverage) |
| Test suite | 73/73 green, but mock design hides C1 |
| PLAN.md §6 conformance | 3 required features absent |

---

## 1. Test & Tooling Results

All commands run from `backend/` on Python 3.14.3 (note: `requires-python = ">=3.12"`, no CI pin).

### `uv run --extra dev pytest --cov=app`

```
73 passed, 73 warnings in 1.21s

Name                           Stmts   Miss  Cover   Missing
app/__init__.py                    0      0   100%
app/market/__init__.py             6      0   100%
app/market/cache.py               39      0   100%
app/market/factory.py             15      0   100%
app/market/interface.py           13      0   100%
app/market/massive_client.py      67      4    94%   85-87, 125
app/market/models.py              26      0   100%
app/market/seed_prices.py          8      0   100%
app/market/simulator.py          139      3    98%   149, 268-269
app/market/stream.py              36     24    33%   26-48, 62-87
TOTAL                            349     31    91%
```

- **All 73 tests pass.**
- Overall coverage is **91%**, not the 84% recorded in `MARKET_DATA_SUMMARY.md`; `massive_client.py` is at **94%**, not 56%. The summary's coverage table is stale.
- 73 `DeprecationWarning`s, all from one source: `tests/conftest.py:11` returns `asyncio.DefaultEventLoopPolicy()`, deprecated for removal in Python 3.16. With `asyncio_mode = "auto"` already set, this fixture is redundant and should simply be deleted.
- `stream.py` at **33%** is the coverage hole that matters — the SSE contract is the frontend's entire data lifeline and no test exercises it.

### `uv run --extra dev ruff check app/ tests/ market_data_demo.py`

```
All checks passed!
```

### `uv run --extra dev ruff format --check app/ tests/`

```
Would reformat: tests/market/test_models.py
Would reformat: tests/market/test_simulator.py
Would reformat: tests/market/test_simulator_source.py
3 files would be reformatted, 16 files already formatted
```

### Independent verification of the GBM mathematics

Not covered by the test suite, so measured directly — 200,000 ticks, events disabled, log-returns compared against target parameters:

```
AAPL: target sigma=0.22, realized=0.2201
TSLA: target sigma=0.50, realized=0.4987
JPM:  target sigma=0.18, realized=0.1798
corr AAPL/GOOGL (target 0.6): 0.603
corr AAPL/JPM   (target 0.3): 0.302
corr AAPL/TSLA  (target 0.3): 0.296
```

The discretisation, the `(mu − σ²/2)` correction, and the Cholesky application are all correct to three decimal places. A 30-ticker correlation matrix (7 tech + 2 finance + TSLA + 20 unknown) decomposes without a `LinAlgError`, so the positive-definiteness of the block structure holds at realistic watchlist sizes.

---

## 2. Critical

### C1. The Massive path writes nothing to the cache, ever

`app/market/massive_client.py:101-103`

```python
price = snap.last_trade.price
timestamp = snap.last_trade.timestamp / 1000.0
```

`LastTrade` in the installed `massive` SDK has no `timestamp` attribute. Its fields are:

```
ticker, trf_timestamp, sequence_number, sip_timestamp, participant_timestamp,
conditions, correction, id, price, trf_id, size, exchange, tape
```

The access raises `AttributeError`, which is caught by the per-snapshot handler at `massive_client.py:110` and logged as a WARNING. Every ticker, every poll. Reproduced against real SDK model objects:

```
WARNING:app.market.massive_client:Skipping snapshot for AAPL: 'LastTrade' object has no attribute 'timestamp'
DEBUG:app.market.massive_client:Massive poll: updated 0/1 tickers
CACHE RESULT: None
```

The failure mode is the worst kind: no crash, no error log, an app that boots cleanly and shows an empty watchlist forever.

Two further defects sit behind the same line:

- **Wrong unit.** The correct field, `sip_timestamp`, is in **nanoseconds**. Dividing by `1000.0` yields a timestamp roughly 31,000 years in the future. The divisor must be `1e9`. (`MASSIVE_API.md` §7 gives the full unit table: `TickerSnapshot.updated` and `LastTrade.sip_timestamp` are nanoseconds, but `Agg.timestamp` on `day`/`prev_day` is milliseconds — the two cannot share a conversion.)
- **`last_trade` is plan-gated.** On Starter and Basic plans it is `None`, so `snap.last_trade.price` raises `AttributeError` too, and the same handler swallows it. `MASSIVE_API.md` §5 specifies the fallback chain (`last_trade.price` → `min.close` → `day.close` → `prev_day.close`); none of it is implemented.

`MASSIVE_API.md` lines 202-214 already document this bug precisely. The fix is written down; it has not been applied.

**Fix:** implement the `_extract_price` fallback chain and `_safe_timestamp` from `MASSIVE_API.md` §5/§7, including the sanity guard that falls back to `time.time()` when a converted timestamp lands outside ±7 days of now.

### C2. The Massive tests cannot fail on C1 — `MagicMock` fabricates the missing field

`backend/tests/market/test_massive.py:11-18`

```python
def _make_snapshot(ticker, price, timestamp_ms) -> MagicMock:
    snap = MagicMock()
    snap.last_trade = MagicMock()
    snap.last_trade.timestamp = timestamp_ms
```

A bare `MagicMock` auto-creates any attribute read from it, so `snap.last_trade.timestamp` succeeds in the test and raises in production. Four tests — `test_poll_updates_cache`, `test_timestamp_conversion`, `test_start_immediate_poll`, `test_malformed_snapshot_skipped` — assert on a field shape that does not exist. `test_timestamp_conversion` explicitly asserts the millisecond divisor, locking in the wrong unit.

This is the root cause of C1 surviving a prior review pass: 13 green tests over a code path that has never once been exercised against a real object.

**Fix:** build fixtures from the real SDK dataclasses (`LastTrade(...)`, `Agg(...)`, `TickerSnapshot(...)`) — as this review did to reproduce C1 — or at minimum use `MagicMock(spec=LastTrade)`, which raises on unknown attributes. Add a test for each plan tier from `MASSIVE_API.md` §9: full data, Starter (`last_trade is None`), pre-market (`day` all zeros), and a corrupt timestamp.

### C3. `test_malformed_snapshot_skipped` enshrines the wrong behaviour

`backend/tests/market/test_massive.py:47-68` sets `last_trade = None` and asserts the ticker is **dropped**. That case is not malformed data — it is the normal shape of a Starter/Basic-plan response. The test codifies "silently show no price for this ticker" as correct, which is exactly the outcome C1 produces. It should assert a fallback to `prev_day.close`.

---

## 3. High

### H1. Three PLAN.md §6 requirements are absent from the shipped module

PLAN.md §6 and `MARKET_INTERFACE.md` §2 specify a price contract the code does not yet implement:

| Required | Location | Status |
|---|---|---|
| `PriceUpdate.session_open` | `models.py:13-16` | Missing |
| `PriceUpdate.change_percent_session` (+ `change_session`) | `models.py:18-37` | Missing |
| `session_open` / `change_percent_session` in `to_dict()` | `models.py:39-49` | Missing from the SSE wire payload |
| `session_open` capture-and-preserve in the cache | `cache.py:23-42` | Missing |
| `MarketDataSource.supports_ticker()` | `interface.py` | Missing (abstract method not declared) |

Two concrete consequences:

- The watchlist's **daily change %** has no data source. The only percentage on the wire, `change_percent`, is tick-over-tick — measured above at roughly ±0.01% per 500ms tick for AAPL. PLAN.md is explicit that this number "must never be displayed."
- **`POST /api/watchlist` cannot be built to spec.** PLAN.md §8 step 5 requires `await source.supports_ticker(t)` before insert, returning `UNSUPPORTED_TICKER` (422). Without it, the simulator invents a price for `BANANA` (see H3) and Massive leaves a permanently priceless row.

PLAN.md flags this as known follow-on work ("additions to the already-built `app/market/` module"), so this is scheduling, not oversight — but the subsystem cannot be called complete against §6 until it lands. `MARKET_INTERFACE.md` §5 and `MARKET_SIMULATOR.md` contain working implementations ready to transcribe.

### H2. `create_stream_router()` mutates a module-level router — a second call double-registers the route

`app/market/stream.py:17` declares `router = APIRouter(...)` at module scope, and the factory at `stream.py:20-48` decorates onto that shared object rather than a fresh one. Verified:

```
create_stream_router(cache_a) is create_stream_router(cache_b)  ->  True
router.routes  ->  ['/api/stream/prices', '/api/stream/prices']
```

Two failures follow. The factory's docstring claim — *"This factory pattern lets us inject the PriceCache without globals"* — is false; the router *is* the global. And because FastAPI resolves the first matching route, the **first** cache ever passed wins permanently; every later cache is silently dead. This will bite the moment a test creates an app per test function, which is precisely what writing the missing SSE tests requires.

**Fix:** move the `APIRouter(...)` construction inside `create_stream_router`.

### H3. The two sources disagree on ticker normalisation

`MassiveDataSource.add_ticker` (`massive_client.py:66-70`) applies `.upper().strip()`. `SimulatorDataSource.add_ticker` (`simulator.py:242-249`) applies neither. Verified:

```
GBMSimulator(["AAPL"]).add_ticker("aapl")
  -> tickers: ['AAPL', 'aapl']   price of 'aapl': 73.41   (AAPL is 190.00)
```

The same input produces one ticker under Massive and two divergent ones under the simulator — a direct violation of the design's central promise that nothing downstream can tell which source is running. It also means the same lowercase symbol streams a fabricated price under the default configuration.

**Fix:** normalise once, at the interface boundary, so both implementations inherit identical behaviour. `MARKET_INTERFACE.md` §4 places normalisation and the `^[A-Z]{1,5}$` check in the API layer ahead of `supports_ticker` — either location works, but it must be one location.

### H4. No tests exist for the SSE stream

`stream.py` is at 33% coverage; lines 26-48 and 62-87 — the endpoint and the entire event generator — are unexecuted. Untested and load-bearing:

- the `retry: 1000` preamble the frontend depends on for auto-reconnect,
- one-event-per-tick-keyed-by-ticker framing (PLAN.md §6 pins the literal payload),
- version-based change detection, including the "idle cache emits nothing" rule that PLAN.md warns clients not to read as a disconnect,
- disconnect detection via `request.is_disconnected()`.

`httpx.ASGITransport` plus a hand-driven `PriceCache` makes all four testable without a live server.

---

## 4. Medium

### M1. `stop()` does not actually stop the simulator source

`interface.py:34-39` contracts that "After `stop()`, the source will not write to the cache again." `SimulatorDataSource.stop()` (`simulator.py:232-240`) cancels the task but leaves `self._sim` populated, so `add_ticker()` afterwards still seeds the cache. Verified: `after stop(), cache got TSLA: True`. Set `self._sim = None` in `stop()`, or guard the mutators.

### M2. A second `start()` orphans the first background task

`simulator.py:229` overwrites `self._task` unconditionally. Verified: after `start(); start(); stop()`, the first task is **still running** and still writing to the cache — `stop()` can only cancel the handle it can see. `interface.py:30` calls double-start "undefined behavior," but a leaked ticking task is a worse outcome than an exception. Raise `RuntimeError` if `self._task is not None`. `MassiveDataSource.start` (`massive_client.py:41-53`) has the identical shape.

### M3. `_fetch_snapshots` reads mutable state from a worker thread

`massive_client.py:123-128` runs in an `asyncio.to_thread` worker and reads `self._tickers`, which `add_ticker`/`remove_ticker` mutate from the event loop. `add_ticker` appends in place (`massive_client.py:69`), so a concurrent append during the request build is a genuine race. Snapshot the list in `_poll_once` before the `to_thread` hop and pass it as an argument.

### M4. `RESTClient` is dropped without being closed

`massive_client.py:63` sets `self._client = None`. The underlying urllib3 connection pool is never released — a leak across repeated start/stop cycles (tests, reloads). Close the client if the SDK exposes a `close()`.

### M5. No SSE keep-alive

`_generate_events` (`stream.py:51-87`) emits nothing while the cache version is unchanged. That is correct per PLAN.md §6 for the browser, but intermediaries (nginx, load balancers, App Runner's idle timeout) will close a silent connection. A `: keepalive\n\n` comment every ~15s costs nothing and is invisible to `EventSource`.

### M6. `rich` is a production dependency for a demo script

`pyproject.toml:12` lists `rich>=13.0.0` in `[project.dependencies]`, used only by `market_data_demo.py`. Move it to the `dev` extra so it stays out of the runtime image (PLAN.md §11).

---

## 5. Low / Nits

- **L1.** `PriceCache.version` (`cache.py:64-67`) reads `self._version` outside the lock while every other accessor takes it. Benign under CPython, inconsistent by inspection.
- **L2.** `cache.py:30` uses `timestamp or time.time()`, so an explicit `timestamp=0.0` is silently replaced. Use `if timestamp is None`.
- **L3.** The simulator seeds unknown tickers via the global `random` module (`simulator.py:151`) and draws normals from the global numpy RNG (`simulator.py:84`) — two unseedable RNGs, so simulator behaviour cannot be made deterministic for tests. Inject a `np.random.Generator`.
- **L4.** `test_exception_resilience` (`test_simulator_source.py`) injects no exception; it only asserts the task is alive. `test_custom_event_probability` asserts nothing at all. Both are misnamed placeholders.
- **L5.** No test asserts the GBM distribution or the realised correlations — the properties that make the simulator worth having, and the ones PLAN.md §12 names ("GBM math is correct"). The measurements in §1 above are straightforward to encode with a fixed seed and loose tolerances.
- **L6.** `.env.example` still does not exist at the repo root, as PLAN.md §13.1 already noted.
- **L7.** `massive` is an unconditional import (`factory.py:10` → `massive_client.py:8`), so the default simulator-only deployment still requires the SDK installed. Deliberate per the prior review, but it means a packaging failure in `massive` takes down the simulator too.
- **L8.** `logger.error("Massive poll failed: %s", e)` (`massive_client.py:119`) logs raw exception text. Polygon authenticates via header, not query string, so key leakage is unlikely — worth a glance if the SDK's error strings ever include the request URL.
- **L9.** `MARKET_DATA_SUMMARY.md` needs a refresh: coverage figures are stale (§1), the `PriceUpdate` field list omits the §6 additions, and "all issues resolved" overstates the state of the Massive path.

---

## 6. What Holds Up Well

Worth recording, because it is the majority of the module:

- **The abstraction genuinely abstracts.** There is no `if simulator:` outside `factory.py`. The cache decouples producer cadence (500ms vs 15s) from consumer cadence completely, and swapping sources really is one environment variable.
- **The GBM implementation is correct**, and not merely superficially — drift correction, discretisation, and Cholesky-correlated draws all reproduce their target parameters to three decimals over 200k ticks. The docstring's dt derivation (252 × 6.5 × 3600) is right, and the correlation matrix stays positive-definite at 30 tickers.
- **`PriceCache` is a clean design.** Frozen `PriceUpdate`, derived properties rather than stored ones, a version counter that makes SSE change detection trivial, and 100% coverage.
- **Failure handling in the loops is right in principle.** Both `_run_loop` and `_poll_once` swallow-and-continue rather than letting one bad tick kill the feed, which is exactly what PLAN.md §6 asks for. The problem in C1 is not the pattern — it is that a *permanent* structural error is being handled as if it were transient.
- **Seed data is thoughtful.** Per-ticker sigma/mu that reflect real relative volatility (TSLA 0.50, V 0.17), sector correlation groups, and a deliberate TSLA carve-out.
- **The design documents are unusually good.** `MASSIVE_API.md` in particular diagnoses C1 in full, with the corrected code. The gap here is between documentation and implementation, not a gap in understanding.

---

## 7. Recommended Order of Work

1. **C1 + C2 + C3** — implement the `_extract_price` fallback chain and `_safe_timestamp`, and rebuild the Massive fixtures on real SDK types. These are one change; fixing the code without fixing the mocks leaves the path unverified. Reference implementation: `MASSIVE_API.md` §5, §7, §9.
2. **H2** — one-line fix, and it blocks writing the tests in H4.
3. **H4** — SSE tests: payload shape, `retry:` preamble, idle-cache silence, disconnect.
4. **H1** — `session_open`, `change_percent_session`, `supports_ticker`. Blocks the watchlist API (§8) and the entire frontend watchlist panel (§10). Reference: `MARKET_INTERFACE.md` §5, `MARKET_SIMULATOR.md`.
5. **H3, M1, M2** — normalisation and lifecycle correctness; small, and each closes a contract violation.
6. **M3-M6, L1-L9** — as convenient. `ruff format`, deleting the `conftest.py` fixture, and refreshing `MARKET_DATA_SUMMARY.md` are minutes of work each.

Items 1-3 should land before any code depends on the Massive path or the stream. Item 4 gates frontend work.
