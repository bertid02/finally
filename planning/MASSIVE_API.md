# Massive API — Reference for FinAlly

> Research pass, 2026-08-25. Covers the REST endpoints FinAlly needs for live and
> end-of-day US equity prices, verified against the docs at `massive.com/docs` **and**
> against the `massive` 2.2.0 Python package installed in `backend/.venv`.
>
> Where the two disagree, the installed package wins — it is what the code actually calls.

---

## 1. What Massive Is

Polygon.io rebranded to **Massive** on 2025-10-30. Same API, same keys, same endpoint
semantics; the host moved from `api.polygon.io` to `api.massive.com` and the PyPI package
from `polygon-api-client` to `massive`. Existing Polygon integrations keep working, so
Polygon-era documentation and StackOverflow answers are still substantially correct.

The project already depends on it:

```toml
# backend/pyproject.toml
dependencies = [
    "massive>=1.0.0",   # installed: 2.2.0
]
```

### Client basics

```python
from massive import RESTClient

client = RESTClient(api_key="...")   # or omit: reads MASSIVE_API_KEY from the environment
```

Verified defaults in `massive/rest/__init__.py`:

| Parameter | Default | Note |
|---|---|---|
| `api_key` | `os.getenv("MASSIVE_API_KEY")` | raises `AuthError` if still `None` |
| `base` | `https://api.massive.com` | |
| `connect_timeout` / `read_timeout` | `10.0` s each | |
| `retries` | `3` | urllib3 `Retry`, backoff factor 0.1 |
| `pagination` | `True` | auto-follows `next_url` on list endpoints |
| `trace` | `False` | set `True` to log full request URLs (API key redacted) |

**The client is synchronous.** It is built on `urllib3.PoolManager`, not asyncio. Every call
from FinAlly's event loop must be wrapped in `asyncio.to_thread(...)` or it will stall the
SSE stream for every connected client.

Retries are already handled for `413, 429, 499, 500, 502, 503, 504` with exponential backoff
— so a transient rate-limit blip is absorbed inside the client and never reaches our code.
This means our own retry logic should be *absent*, not layered on top.

---

## 2. Plans and Rate Limits — the constraint that shapes the design

| Plan | Price | Rate limit | Recency | History |
|---|---|---|---|---|
| **Basic** (free) | $0 | **5 calls/min** | **End-of-day only** | 2 years |
| Starter | $29/mo | Unlimited | 15-min delayed | 5 years |
| Developer | $79/mo | Unlimited | 15-min delayed | 10 years |
| Advanced | $199/mo | Unlimited | **Real-time** | 20+ years |
| Business | custom | Unlimited | Real-time | full |

Two consequences that matter more than the rate limit itself:

1. **Only Advanced and above is actually real-time.** Starter and Developer are 15 minutes
   delayed. Basic is end-of-day — the numbers do not move intraday at all.
2. **Rate limiting is only a Basic-tier concern.** Every paid tier is unlimited, so the
   "poll every 15 seconds to stay under 5/min" arithmetic only applies to the free key.

> ### ⚠️ Correction to `PLAN.md` §6
>
> `PLAN.md` states: *"Free tier (5 calls/min): poll every 15 seconds"* using the grouped
> snapshot endpoint. **This does not work.** The snapshot endpoints are documented as
> *"Included in Stocks Starter, Developer, Advanced, and Business plans"* — **not Basic**.
> A free key calling `get_snapshot_all` gets a 403, not data.
>
> The free tier's actual usable endpoint is the **Daily Market Summary** (§4.2), which *is*
> included in all plans and also returns every ticker in one call — but end-of-day only.
> See §6 for the resulting three-mode strategy.

---

## 3. The Primary Endpoint — Full Market Snapshot

This is the workhorse for any paid key.

```
GET /v2/snapshot/locale/us/markets/stocks/tickers
```

**One call returns every requested ticker.** This is the entire reason the call budget works:
10 tickers cost one request, not ten. It is also why adding a ticker to the watchlist adds
zero API cost.

### Query parameters

| Param | Type | Default | Notes |
|---|---|---|---|
| `tickers` | comma-separated string | *(all)* | **Case-sensitive.** Omit to get 10,000+ tickers — always pass it |
| `include_otc` | boolean | `false` | Leave off |

### Python

```python
from massive import RESTClient
from massive.rest.models import SnapshotMarketType

client = RESTClient(api_key=api_key)

snapshots = client.get_snapshot_all(
    market_type=SnapshotMarketType.STOCKS,   # -> "stocks"
    tickers=["AAPL", "GOOGL", "MSFT"],       # a list is joined to "AAPL,GOOGL,MSFT" for you
)

for snap in snapshots:
    print(snap.ticker, snap.todays_change_percent)
```

`get_snapshot_all` returns `list[TickerSnapshot]`. Pass `raw=True` to get the undecoded
`urllib3.HTTPResponse` instead — useful when debugging a field mapping.

### Raw JSON shape

```json
{
  "status": "OK",
  "count": 3,
  "tickers": [
    {
      "ticker": "AAPL",
      "todaysChange": 1.85,
      "todaysChangePerc": 0.97,
      "updated": 1755993600000000000,
      "day":     {"o": 190.10, "h": 192.40, "l": 189.75, "c": 192.05, "v": 41203311, "vw": 191.22},
      "prevDay": {"o": 188.00, "h": 190.90, "l": 187.60, "c": 190.20, "v": 52118400, "vw": 189.44},
      "min":     {"av": 41203311, "o": 191.98, "h": 192.10, "l": 191.90, "c": 192.05,
                  "v": 18422, "vw": 192.01, "t": 1755993540000, "n": 143},
      "lastTrade": {"p": 192.05, "s": 100, "t": 1755993599123456789,
                    "x": 4, "i": "52983575223816", "c": [12, 37], "z": 3},
      "lastQuote": {"P": 192.06, "S": 3, "p": 192.04, "s": 5, "t": 1755993599098765432}
    }
  ]
}
```

### `TickerSnapshot` — the deserialized model

Verified from `massive/rest/models/snapshot.py`. Note the JSON key → attribute renaming;
this is where naive code goes wrong.

| Attribute | JSON key | Type | Meaning |
|---|---|---|---|
| `ticker` | `ticker` | `str` | Symbol |
| `todays_change` | `todaysChange` | `float` | Absolute change **vs previous day's close** |
| `todays_change_percent` | `todaysChangePerc` | `float` | Percent change **vs previous day's close** |
| `updated` | `updated` | `int` | **Nanoseconds** since epoch |
| `day` | `day` | `Agg` | Today's bar so far |
| `prev_day` | `prevDay` | `Agg` | Previous trading day's completed bar |
| `min` | `min` | `MinuteSnapshot` | Most recent one-minute bar |
| `last_trade` | `lastTrade` | `LastTrade` | Latest trade — **plan-dependent, often `None`** |
| `last_quote` | `lastQuote` | `LastQuote` | Latest NBBO quote — **plan-dependent** |
| `fair_market_value` | `fmv` | `float` | Business plans only |

Every field is `Optional` and defaults to `None`. The `@modelclass` decorator builds a
dataclass whose `__init__` silently ignores unknown keyword arguments — so a missing field
is `None`, never an exception, but a **misspelled attribute raises `AttributeError`**.

#### `Agg` (used for `day` and `prev_day`)

| Attribute | JSON key | Meaning |
|---|---|---|
| `open` / `high` / `low` / `close` | `o` / `h` / `l` / `c` | OHLC |
| `volume` | `v` | Share volume |
| `vwap` | `vw` | Volume-weighted average price |
| `timestamp` | `t` | **Milliseconds** |
| `transactions` | `n` | Trade count |

#### `MinuteSnapshot` (used for `min`)

Same OHLCV fields, plus `accumulated_volume` (`av`) and `timestamp` (`t`, **milliseconds**).

#### `LastTrade`

| Attribute | JSON key | Meaning |
|---|---|---|
| `price` | `p` | Trade price |
| `size` | `s` | Share count |
| `sip_timestamp` | `t` | **Nanoseconds** — SIP timestamp |
| `participant_timestamp` | `y` | **Nanoseconds** — exchange timestamp |
| `exchange` | `x` | Exchange ID |
| `conditions` | `c` | Trade condition codes |
| `tape` | `z` | 1=NYSE, 2=NYSE ARCA/American, 3=NASDAQ |

> ### ⚠️ Two bugs this table exposes in `backend/app/market/massive_client.py`
>
> The shipped `_poll_once` does:
>
> ```python
> price = snap.last_trade.price
> timestamp = snap.last_trade.timestamp / 1000.0   # ← wrong on two counts
> ```
>
> 1. **`LastTrade` has no `timestamp` attribute.** The field is `sip_timestamp`. This raises
>    `AttributeError`, which the surrounding `except (AttributeError, TypeError)` swallows —
>    so *every ticker is silently skipped on every poll* and the cache never fills. The
>    failure is invisible: a `logger.warning` per ticker and an empty watchlist.
> 2. **The unit is nanoseconds, not milliseconds.** Even with the name fixed, `/ 1000.0`
>    yields a timestamp roughly 31,000 years in the future. The correct divisor is `1e9`.
>
> Additionally, `snap.last_trade` is `None` on Starter and Basic plans, so
> `snap.last_trade.price` raises `AttributeError` there too. §5 gives the fix.

---

## 4. Supporting Endpoints

### 4.1 Single Ticker Snapshot

```
GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}
```

```python
snap = client.get_snapshot_ticker(SnapshotMarketType.STOCKS, "AAPL")
```

Returns one `TickerSnapshot`, identical shape to §3. Useful for a **ticker-support check**
without pulling the whole watchlist — but it costs a call, and `get_snapshot_all` already
tells us which symbols came back, so FinAlly does not need it.

### 4.2 Daily Market Summary (grouped daily) — the free-tier path

```
GET /v2/aggs/grouped/locale/us/market/stocks/{date}
```

```python
bars = client.get_grouped_daily_aggs(date="2026-08-24", adjusted=True)
for bar in bars:
    print(bar.ticker, bar.open, bar.close)   # bar.ticker comes from JSON key "T"
```

Returns `list[GroupedDailyAgg]` — **every US ticker for that date in a single call**.
Included in **all plans, Basic included**. Fields mirror `Agg` plus `ticker` (`T`).

This is the only one-call-covers-all-tickers endpoint available on a free key, which makes it
FinAlly's end-of-day fallback. Caveats:

- `{date}` must be a **trading day**. Weekends and holidays return `resultsCount: 0`, not an
  error — walk backwards up to ~5 days to find the last session.
- On Basic the current day is not available until after the close settles; anchor on the
  previous trading day.

### 4.3 Previous Day Bar

```
GET /v2/aggs/ticker/{ticker}/prev
```

```python
prev = client.get_previous_close_agg("AAPL", adjusted=True)
```

Included in **all plans**, but **one call per ticker** — 10 tickers costs 10 of the free
tier's 5-per-minute budget. Prefer §4.2 whenever more than one ticker is needed. Worth
knowing for a single-symbol support check.

### 4.4 Custom Bars — for a real historical chart

```
GET /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}
```

```python
bars = client.get_aggs(
    ticker="AAPL", multiplier=5, timespan="minute",
    from_="2026-08-24", to="2026-08-25", adjusted=True, limit=50000,
)
# or client.list_aggs(...) for an auto-paginating iterator
```

Not needed for the FinAlly MVP — §10 of `PLAN.md` builds the detail chart from
SSE-accumulated ticks. Listed here because "show me a real 1-month chart" is the obvious
next feature request, and this is the endpoint that serves it.

### 4.5 Last Trade — deliberately not used

```
GET /v2/last/trade/{ticker}
```

One call per ticker, and **excluded from Basic and Starter**. The snapshot endpoint already
embeds the same data for plans that have it. No reason to call this separately.

### 4.6 Unified Snapshot — noted, not used

```
GET /v3/snapshot?ticker.any_of=AAPL,GOOGL,MSFT
```

Newer multi-asset-class endpoint (stocks + options + FX + crypto), up to 250 tickers per
call, exposed as `client.list_universal_snapshots(...)`. It carries an explicit
`market_status` field (`open` / `closed` / `early_trading` / `late_trading`) that `/v2` lacks,
which is genuinely nicer. Not adopted because it has the same plan exclusions as `/v2` and
FinAlly is equities-only — a second code path for no new capability.

---

## 5. Resolving a Usable Price — the fallback chain

Because `last_trade` and `last_quote` are plan-gated and can be `None`, and because bar
fields are `0` or `None` before the session opens, a single field access is not enough.
Resolve in descending order of freshness:

```python
def resolve_price(snap) -> float | None:
    """Best available current price from a TickerSnapshot, freshest first.

    Ordered by staleness, not preference:
      last_trade  - real-time, Developer/Advanced/Business only
      min.close   - up to 60s stale, available on Starter+
      day.close   - today's running close; 0/None before the open
      prev_day.close - always present once the ticker has ever traded
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

The truthiness checks (`if ... .price`, not `is not None`) are deliberate: Massive returns
`0` — not `null` — for bar fields on a ticker that has not traded yet today. `0` is not a
tradeable price, and `0.0` is falsy, so one check handles both cases.

### The session-open anchor

`PLAN.md` §6 asks for a `session_open` to compute the watchlist's daily change %, and
specifies *"the day's opening price from the snapshot response"* — i.e. `day.open`.

**Use `prev_day.close` instead.** Three reasons:

1. `day.open` is `0` before the market opens and during pre-market, so the percentage is
   undefined or infinite for a meaningful part of the day.
2. Massive's own `todaysChangePerc` is computed against the **previous close**, so anchoring
   on `day.open` makes our number disagree with the provider's on the same screen.
3. Previous close is what every finance site means by "daily change %". Open-to-current is a
   different statistic that happens to look similar.

Best of all: don't compute it when Massive already did.

```python
def resolve_session_change(snap) -> tuple[float | None, float | None]:
    """(session_open_anchor, change_percent_session) — prefer the provider's own numbers."""
    anchor = snap.prev_day.close if snap.prev_day and snap.prev_day.close else None
    if snap.todays_change_percent is not None:
        return anchor, snap.todays_change_percent   # authoritative, split-adjusted
    return anchor, None                             # caller derives from anchor
```

`todays_change_percent` is split- and dividend-adjusted by Massive. A locally computed
`(price - prev_close) / prev_close` is not, so it prints a spurious −50% on the morning of a
2-for-1 split. Prefer the provider's field; fall back to local arithmetic only when it's
absent.

### Timestamp units — one table, because they are inconsistent

| Source | Unit | Convert to seconds |
|---|---|---|
| `TickerSnapshot.updated` | nanoseconds | `/ 1e9` |
| `LastTrade.sip_timestamp` | nanoseconds | `/ 1e9` |
| `LastQuote.sip_timestamp` | nanoseconds | `/ 1e9` |
| `Agg.timestamp` (`day`, `prev_day`) | **milliseconds** | `/ 1e3` |
| `MinuteSnapshot.timestamp` | **milliseconds** | `/ 1e3` |
| `GroupedDailyAgg.timestamp` | **milliseconds** | `/ 1e3` |

Mixing these up is the single most likely bug in this module — and it already happened once
(§3). Suggested guard: assert any converted timestamp lands within ±7 days of `time.time()`,
and fall back to `time.time()` if not. A wrong-by-a-millennium timestamp should degrade to
"now", not poison the chart's x-axis.

---

## 6. Poll Cadence

Since paid tiers are unlimited, cadence is about not hammering the API for data that isn't
changing, rather than about staying under a quota.

| Plan | Actual data freshness | Recommended interval | Rationale |
|---|---|---|---|
| Basic (free) | End of day | **n/a — see below** | Snapshot endpoint unavailable |
| Starter / Developer | 15-min delayed | **15 s** | The data is stale anyway; faster only burns quota headroom |
| Advanced / Business | Real-time | **2 s** | Genuinely fresh; matches the UI's 500 ms flash cadence closely enough |

`15.0` seconds is the right default: it is correct for the paid tiers most users will hold,
and harmless on Advanced.

### The free-tier problem, stated plainly

A Basic key cannot drive a live trading terminal. It has no snapshot access and no intraday
data at all. FinAlly has three honest options:

1. **Reject it.** Probe once at startup; if the snapshot call returns 403, log a clear
   message and fall back to the simulator. *Recommended* — the simulator is a better demo
   than a frozen screen, and the failure is explained rather than mysterious.
2. **Degrade to end-of-day.** Use `get_grouped_daily_aggs` (§4.2) once at startup for real
   previous-close prices, then hold them flat. Honest, but a trading terminal where nothing
   ticks is a broken-looking demo.
3. **Hybrid.** Seed the simulator from real end-of-day closes via §4.2, then let GBM walk
   from there. Real anchor prices, live-looking motion, one API call total.

Option 3 is the most appealing and costs little — it is written up as an optional
`seed_from_massive()` hook in `MARKET_SIMULATOR.md` §8. Option 1 is the required baseline.

---

## 7. Failure Modes

| Symptom | Cause | Handling |
|---|---|---|
| `AuthError` at construction | `api_key` is `None` | Factory already checks for a non-empty key |
| HTTP 401 | Key invalid or revoked | **Fatal** — log loudly, fall back to simulator |
| HTTP 403 | Endpoint not in plan (Basic + snapshot) | **Fatal for this source** — §6 fallback |
| HTTP 429 | Rate limited | urllib3 retries 3× automatically; if it still surfaces, back off |
| Empty `tickers` array | All symbols invalid, or pre-4 AM ET | Not an error — log and retry next interval |
| Ticker silently absent | That one symbol is unknown/delisted | Leave the cache entry stale; surface via `supports_ticker()` |
| `last_trade is None` | Plan lacks trade data | §5 fallback chain — **not** an error |
| Timeout | Network | urllib3 retries; then swallow and retry next interval |

Two distinctions worth encoding in the client:

- **Fatal vs transient.** 401 and 403 will fail identically forever. Retrying them every 15
  seconds for the life of the process produces an infinite error log and never recovers. The
  poller should detect them on the *first* poll and stop, so the factory can fall back.
- **Snapshot data is cleared daily at ~3:30 AM ET** and repopulates from ~4:00 AM ET. A poll
  in that window legitimately returns nothing. Log at `debug`, not `error`.

### Checking whether a ticker is supported

`PLAN.md` §6 requires `async def supports_ticker(self, ticker: str) -> bool`. The cheapest
correct implementation piggybacks on data we already fetch:

```python
async def supports_ticker(self, ticker: str) -> bool:
    """True if Massive returns a snapshot for this symbol.

    Costs one extra API call, on a user-initiated action only (watchlist add),
    so it does not compete with the polling budget in any meaningful way.
    """
    ticker = ticker.upper().strip()
    try:
        snaps = await asyncio.to_thread(
            self._client.get_snapshot_all,
            market_type=SnapshotMarketType.STOCKS,
            tickers=[ticker],
        )
    except Exception:
        logger.warning("supports_ticker(%s) failed; assuming unsupported", ticker)
        return False
    return any(s.ticker == ticker for s in snaps)
```

Returning `False` on exception is the right default: `PLAN.md` maps it to a 422
`UNSUPPORTED_TICKER`, which is a recoverable, explicable outcome for the user. Admitting a
symbol we cannot price produces a permanently blank watchlist row instead.

---

## 8. A Corrected Poller

Consolidating §3, §5, and §7 — this is the shape `massive_client.py` should take. The full
interface it implements is specified in `MARKET_INTERFACE.md`.

```python
import asyncio
import logging
import time

from massive import RESTClient
from massive.rest.models import SnapshotMarketType

logger = logging.getLogger(__name__)

NS_PER_SEC = 1_000_000_000
MAX_TIMESTAMP_SKEW = 7 * 24 * 3600   # 7 days


class MassiveDataSource:
    def __init__(self, api_key, price_cache, poll_interval=15.0):
        self._api_key = api_key
        self._cache = price_cache
        self._interval = poll_interval
        self._tickers: list[str] = []
        self._client: RESTClient | None = None
        self._task: asyncio.Task | None = None
        self._fatal = False          # 401/403 latch — stop retrying forever

    async def start(self, tickers):
        self._client = RESTClient(api_key=self._api_key)
        self._tickers = [t.upper().strip() for t in tickers]

        await self._poll_once()      # fail fast, before the app claims to be live
        if self._fatal:
            raise RuntimeError("Massive unavailable for this API key; see log")

        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")

    async def _poll_loop(self):
        while not self._fatal:
            await asyncio.sleep(self._interval)
            await self._poll_once()
        logger.error("Massive poller halted: unrecoverable error")

    async def _poll_once(self):
        if not self._tickers or not self._client:
            return
        try:
            snaps = await asyncio.to_thread(
                self._client.get_snapshot_all,
                market_type=SnapshotMarketType.STOCKS,
                tickers=self._tickers,
            )
        except Exception as e:
            status = getattr(e, "status", None) or getattr(e, "code", None)
            if status in (401, 403):
                self._fatal = True
                logger.error("Massive rejected the API key (HTTP %s). "
                             "Snapshots require Starter or above.", status)
            else:
                logger.error("Massive poll failed: %s", e)
            return

        if not snaps:
            logger.debug("Massive returned no snapshots (pre-market or unknown symbols)")
            return

        for snap in snaps:
            price = resolve_price(snap)
            if price is None:
                logger.debug("No usable price for %s; leaving cache entry stale", snap.ticker)
                continue
            anchor, pct = resolve_session_change(snap)
            self._cache.update(
                ticker=snap.ticker,
                price=price,
                timestamp=self._safe_timestamp(snap.updated),
                session_open=anchor,
                change_percent_session=pct,
            )

    @staticmethod
    def _safe_timestamp(raw_ns) -> float:
        """Nanoseconds -> seconds, with a sanity guard. Never poisons the chart axis."""
        now = time.time()
        if not raw_ns:
            return now
        ts = raw_ns / NS_PER_SEC
        return ts if abs(ts - now) < MAX_TIMESTAMP_SKEW else now
```

Three properties worth naming, because they're the difference between this and the shipped
version:

- **A failed poll leaves the previous price in place.** The cache is never cleared on error,
  so a network blip shows a briefly stale price rather than an empty terminal.
- **`_fatal` latches.** 401/403 stops the loop instead of logging forever.
- **`start()` raises on a fatal first poll**, giving the factory a clean place to fall back
  to the simulator rather than starting a source that will never produce data.

---

## 9. Testing Against Massive Without an API Key

`RESTClient` is instantiated inside `start()`, so tests inject a fake by assigning
`source._client` directly — which is what `backend/tests/market/test_massive.py` already
does. Build fixtures from the real model classes rather than `Mock()`, so that the
`AttributeError` in §3 would have been caught:

```python
from massive.rest.models.snapshot import TickerSnapshot

def make_snapshot(ticker="AAPL", price=192.05, prev_close=190.20):
    """Build a TickerSnapshot from raw JSON, exercising the real from_dict mapping."""
    return TickerSnapshot.from_dict({
        "ticker": ticker,
        "todaysChange": round(price - prev_close, 4),
        "todaysChangePerc": round((price - prev_close) / prev_close * 100, 4),
        "updated": 1755993600123456789,
        "day":     {"o": 190.10, "h": 192.40, "l": 189.75, "c": price, "v": 41203311},
        "prevDay": {"o": 188.00, "h": 190.90, "l": 187.60, "c": prev_close, "v": 52118400},
        "min":     {"o": 191.98, "h": 192.10, "l": 191.90, "c": price,
                    "v": 18422, "t": 1755993540000},
        "lastTrade": {"p": price, "s": 100, "t": 1755993599123456789, "x": 4, "z": 3},
    })
```

`TickerSnapshot.from_dict` is the same function the client uses, so a fixture built this way
fails loudly if we misname a field. `Mock()` — which answers *any* attribute access with a
new `Mock` — is what let `.timestamp` pass code review in the first place.

Cases worth covering explicitly:

| Case | Fixture | Asserts |
|---|---|---|
| Full data | all fields | `last_trade.price` is used |
| Starter plan | drop `lastTrade` / `lastQuote` | falls back to `min.close` |
| Pre-market | `day` all zeros, no `min` | falls back to `prev_day.close` |
| Never traded | `prevDay` zeros too | `resolve_price` returns `None`, cache untouched |
| Bad timestamp | `updated: 1` | `_safe_timestamp` returns ~`time.time()` |
| 403 | client raises | `_fatal` set, loop stops, `start()` raises |
| Empty response | `[]` | logs at debug, does not clear the cache |

---

## Sources

- [Massive Python client (`massive-com/client-python`)](https://github.com/massive-com/client-python)
- [Massive API docs](https://massive.com/docs)
- [Full Market Snapshot](https://massive.com/docs/rest/stocks/snapshots/full-market-snapshot)
- [Unified Snapshot](https://massive.com/docs/rest/stocks/snapshots/unified-snapshot)
- [Daily Market Summary (grouped daily)](https://massive.com/docs/rest/stocks/aggregates/daily-market-summary)
- [Previous Day Bar](https://massive.com/docs/rest/stocks/aggregates/previous-day-bar)
- [Last Trade](https://massive.com/docs/rest/stocks/trades-quotes/last-trade)
- [Massive pricing](https://massive.com/pricing)
- [Polygon.io is Now Massive](https://massive.com/blog/polygon-is-now-massive)
- Installed package: `massive` 2.2.0 — `backend/.venv/lib/python3.14/site-packages/massive/`
