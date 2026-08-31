# FinAlly — AI Trading Workstation

## Project Specification

## 1. Vision

FinAlly (Finance Ally) is a visually stunning AI-powered trading workstation that streams live market data, lets users trade a simulated portfolio, and integrates an LLM chat assistant that can analyze positions and execute trades on the user's behalf. It looks and feels like a modern Bloomberg terminal with an AI copilot.

This is the capstone project for an agentic AI coding course. It is built entirely by Coding Agents demonstrating how orchestrated AI agents can produce a production-quality full-stack application. Agents interact through files in `planning/`.

## 2. User Experience

### First Launch

The user runs a single Docker command (or a provided start script). A browser opens to `http://localhost:8000`. No login, no signup. They immediately see:

- A watchlist of 10 default tickers with live-updating prices in a grid
- $10,000 in virtual cash
- A dark, data-rich trading terminal aesthetic
- An AI chat panel ready to assist

### What the User Can Do

- **Watch prices stream** — prices flash green (uptick) or red (downtick) with subtle CSS animations that fade
- **View sparkline mini-charts** — price action beside each ticker in the watchlist, accumulated on the frontend from the SSE stream since page load (sparklines fill in progressively)
- **Click a ticker** to see a larger detailed chart in the main chart area
- **Buy and sell shares** — market orders only, instant fill at current price, no fees, no confirmation dialog
- **Monitor their portfolio** — a heatmap (treemap) showing positions sized by weight and colored by P&L, plus a P&L chart tracking total portfolio value over time
- **View a positions table** — ticker, quantity, average cost, current price, unrealized P&L, % change
- **Chat with the AI assistant** — ask about their portfolio, get analysis, and have the AI execute trades and manage the watchlist through natural language
- **Manage the watchlist** — add/remove tickers manually or via the AI chat

### Visual Design

- **Dark theme**: backgrounds around `#0d1117` or `#1a1a2e`, muted gray borders, no pure black
- **Price flash animations**: brief green/red background highlight on price change, fading over ~500ms via CSS transitions
- **Connection status indicator**: a small colored dot (green = connected, yellow = reconnecting, red = disconnected) visible in the header
- **Professional, data-dense layout**: inspired by Bloomberg/trading terminals — every pixel earns its place
- **Responsive but desktop-first**: optimized for wide screens, functional on tablet

### Color Scheme
- Accent Yellow: `#ecad0a`
- Blue Primary: `#209dd7`
- Purple Secondary: `#753991` (submit buttons)

## 3. Architecture Overview

### Single Container, Single Port

```
┌─────────────────────────────────────────────────┐
│  Docker Container (port 8000)                   │
│                                                 │
│  FastAPI (Python/uv)                            │
│  ├── /api/*          REST endpoints             │
│  ├── /api/stream/*   SSE streaming              │
│  └── /*              Static file serving         │
│                      (Next.js export)            │
│                                                 │
│  SQLite database (volume-mounted)               │
│  Background task: market data polling/sim        │
└─────────────────────────────────────────────────┘
```

- **Frontend**: Next.js with TypeScript, built as a static export (`output: 'export'`), served by FastAPI as static files
- **Backend**: FastAPI (Python), managed as a `uv` project
- **Database**: SQLite, single file at `db/finally.db`, volume-mounted for persistence
- **Real-time data**: Server-Sent Events (SSE) — simpler than WebSockets, one-way server→client push, works everywhere
- **AI integration**: LiteLLM → OpenRouter (Cerebras for fast inference), with structured outputs for trade execution
- **Market data**: Environment-variable driven — simulator by default, real data via Massive API if key provided

### Why These Choices

| Decision | Rationale |
|---|---|
| SSE over WebSockets | One-way push is all we need; simpler, no bidirectional complexity, universal browser support |
| Static Next.js export | Single origin, no CORS issues, one port, one container, simple deployment |
| SQLite over Postgres | No auth = no multi-user = no need for a database server; self-contained, zero config |
| Single Docker container | Students run one command; no docker-compose for production, no service orchestration |
| uv for Python | Fast, modern Python project management; reproducible lockfile; what students should learn |
| Market orders only | Eliminates order book, limit order logic, partial fills — dramatically simpler portfolio math |

---

## 4. Directory Structure

**Every path below now exists and is built.** The `✅` markers that used to distinguish "built" from "planned" have been dropped because they no longer distinguish anything — the whole tree is populated. Test counts are the current suite, not targets.

```
finally/
├── frontend/                 # Next.js TypeScript project (static export) — 50 vitest tests
│   ├── src/app/              # Layout, page, globals.css (Tailwind dark theme)
│   ├── src/components/       # Watchlist, charts, Heatmap, PositionsTable, TradeBar, ChatPanel
│   ├── src/state/            # TerminalProvider — SSE subscription and derived portfolio state
│   └── src/lib/              # api.ts, format.ts, types.ts, mockApi.ts
├── backend/                  # FastAPI uv project (Python) — 627 pytest tests, 100% coverage of app/
│   ├── app/market/           # Market data subsystem (193 tests — see MARKET_DATA_SUMMARY.md)
│   ├── app/db/               # Schema DDL, lazy init, seed data, repository, trade transaction (230)
│   ├── app/api/              # Routes, error envelope, valuation helper, deps (99)
│   ├── app/llm/              # LiteLLM client, prompt, structured-output schema, mock mode (105)
│   ├── app/config.py         # Settings + .env loading, read once at app construction
│   ├── app/main.py           # create_app(), lifespan, static mount
│   └── tests/                # pytest suite, mirroring app/ package-for-package
├── planning/                 # Project-wide documentation for agents
│   ├── PLAN.md               # This document
│   ├── TEAM.md               # Agent ownership map and build order
│   ├── TEAM_LOG.md           # Cross-boundary decisions and handoffs, append-only
│   ├── MARKET_DATA_SUMMARY.md
│   └── archive/              # Superseded design docs
├── scripts/
│   ├── start_mac.sh          # Launch Docker container (macOS/Linux)
│   ├── stop_mac.sh           # Stop Docker container (macOS/Linux)
│   ├── start_windows.ps1     # Launch Docker container (Windows PowerShell)
│   └── stop_windows.ps1      # Stop Docker container (Windows PowerShell)
├── test/                     # Playwright E2E — 34 tests, 7 specs, + docker-compose.test.yml
│   ├── e2e/                  # 01-fresh-start … 07-sse-resilience, run in filename order
│   └── fixtures/terminal.ts  # The single page object every spec drives the UI through
├── db/                       # Volume mount target (SQLite file lives here at runtime)
│   └── .gitkeep              # Directory exists in repo; finally.db is gitignored
├── .github/workflows/
│   ├── ci.yml                # backend / frontend / e2e — see §12
│   ├── claude.yml            # @claude mention handler
│   └── claude-code-review.yml
├── Dockerfile                # Multi-stage build (Node 20 → Python 3.12), non-root uid 10001
├── docker-compose.yml        # Convenience wrapper — same image, container and volume as scripts/
├── .dockerignore
├── .env                      # Environment variables (gitignored)
├── .env.example              # Committed template — mirrors §5, no real keys
└── .gitignore
```

### Key Boundaries

- **`frontend/`** is a self-contained Next.js project. It knows nothing about Python. It talks to the backend via `/api/*` endpoints and `/api/stream/*` SSE endpoints. Internal structure is up to the Frontend Engineer agent.
- **`backend/`** is a self-contained uv project with its own `pyproject.toml`. It owns all server logic including database initialization, schema, seed data, API routes, SSE streaming, market data, and LLM integration. Internal structure is up to the Backend/Market Data agents.
- **`backend/app/db/`** contains the schema DDL, seed logic, the repository layer and the transactional trade execution. (This section originally called it `backend/db/`; the code landed inside the `app` package, which is where an importable module belongs.) The backend initializes the database in the app lifespan — creating tables and seeding default data if the SQLite file doesn't exist or is empty.
- **`db/`** at the top level is the runtime volume mount point. The SQLite file (`db/finally.db`) is created here by the backend and persists across container restarts via Docker volume.
- **`planning/`** contains project-wide documentation, including this plan. All agents reference files here as the shared contract.
- **`test/`** contains Playwright E2E tests and supporting infrastructure (e.g., `docker-compose.test.yml`). Unit tests live within `frontend/` and `backend/` respectively, following each framework's conventions.
- **`scripts/`** contains start/stop scripts that wrap Docker commands.

---

## 5. Environment Variables

```bash
# Required: OpenRouter API key for LLM chat functionality
OPENROUTER_API_KEY=your-openrouter-api-key-here

# Optional: Massive (Polygon.io) API key for real market data
# If not set, the built-in market simulator is used (recommended for most users)
MASSIVE_API_KEY=

# Optional: Set to "true" for deterministic mock LLM responses (testing)
LLM_MOCK=false
```

### Behavior

- If `MASSIVE_API_KEY` is set and non-empty → backend uses Massive REST API for market data
- If `MASSIVE_API_KEY` is absent or empty → backend uses the built-in market simulator
- If `LLM_MOCK=true` → backend returns deterministic mock LLM responses (for E2E tests)
- The backend reads `.env` from the project root (mounted into the container or read via docker `--env-file`)

---

## 6. Market Data

### Two Implementations, One Interface

Both the simulator and the Massive client implement the same abstract interface. The backend selects which to use based on the environment variable. All downstream code (SSE streaming, price cache, frontend) is agnostic to the source.

### Simulator (Default)

- Generates prices using geometric Brownian motion (GBM) with configurable drift and volatility per ticker
- Updates at ~500ms intervals
- Correlated moves across tickers (e.g., tech stocks move together)
- Occasional random "events" — sudden 2-5% moves on a ticker for drama
- Starts from realistic seed prices (e.g., AAPL ~$190, GOOGL ~$175, etc.)
- Runs as an in-process background task — no external dependencies

### Massive API (Optional)

- REST API polling (not WebSocket) — simpler, works on all tiers
- **One grouped snapshot call per poll covers every watched ticker** (`get_snapshot_all` against `/v2/snapshot/locale/us/markets/stocks/tickers`). This is why the call budget works: 10+ tickers cost one call, not one call each
- Free tier (5 calls/min): poll every 15 seconds
- Paid tiers: poll every 2-15 seconds depending on tier
- Parses REST response into the same format as the simulator
- A poll failure is logged and swallowed — the loop retries on the next interval rather than crashing the app

### Ticker Support Check

Adding an arbitrary ticker behaves differently per source, so the interface exposes an explicit check:

```python
async def supports_ticker(self, ticker: str) -> bool
```

- **Simulator** — returns `True` for any well-formed symbol; unknown tickers get `DEFAULT_PARAMS` (sigma 0.25, mu 0.05) and a synthetic seed price
- **Massive** — returns `True` only if the symbol appears in a snapshot fetch

`POST /api/watchlist` calls this before inserting (see §8). Without it, a typo like `APPL` silently streams an invented price under the simulator and sits permanently priceless under Massive.

### Shared Price Cache

- A single background task (simulator or Massive poller) writes to an in-memory price cache
- The cache holds the latest price, previous price, session open, and timestamp for each ticker
- SSE streams read from this cache and push updates to connected clients
- This architecture supports future multi-user scenarios without changes to the data layer

### Session Open — the anchor for "daily" change

`PriceUpdate.previous_price` is the price from the *previous tick* (~500ms ago). It drives the flash animation and nothing else — as a displayed percentage it is meaningless noise (±0.02%).

For the watchlist's daily change %, each `PriceUpdate` also carries a **`session_open`**:

- **Simulator** — the ticker's seed price, captured when the ticker is first tracked. Fixed for the process lifetime
- **Massive** — the day's opening price from the snapshot response
- Newly added tickers anchor at their first observed price

Two distinct percentages result, and they must not be confused:

| Field | Basis | Used for |
|---|---|---|
| `change_percent` | vs. previous tick | Price flash direction only — never displayed as a number |
| `change_percent_session` | vs. `session_open` | The watchlist "daily change %" column, positions table % change |

### SSE Streaming

- Endpoint: `GET /api/stream/prices`
- Long-lived SSE connection; client uses native `EventSource` API
- Server pushes updates for all tickers known to the system at ~500ms cadence — in the single-user model this is the user's watchlist
- **One event per tick carries every ticker**, as a JSON object keyed by symbol — *not* one event per ticker:

```
retry: 1000

data: {"AAPL": {"ticker":"AAPL","price":190.50,"previous_price":190.40,
                "session_open":190.00,"timestamp":1755993600.12,
                "change":0.10,"change_percent":0.05,
                "change_percent_session":0.26,"direction":"up"},
       "GOOGL": {...}}
```

- The stream **only emits when the price cache version changes** — an idle cache sends nothing, so clients must not treat silence as a disconnect
- The connection opens with a `retry: 1000` directive; `EventSource` reconnects automatically
- The stream carries **prices only**. Watchlist membership changes are never pushed here — see §8

> **Implementation note:** `session_open` and `change_percent_session` are additions to the already-built `app/market/` module. They touch `models.py` (field + property + `to_dict`), `cache.py` (capture on first update for a ticker, preserve thereafter), and both data sources. `supports_ticker()` is a new abstract method on `MarketDataSource` with an implementation in each source. Existing tests in `backend/tests/market/` need updating alongside.

---

## 7. Database

### SQLite with Lazy Initialization

The backend checks for the SQLite database on startup (or first request). If the file doesn't exist or tables are missing, it creates the schema and seeds default data. This means:

- No separate migration step
- No manual database setup
- Fresh Docker volumes start with a clean, seeded database automatically

### Schema

All tables include a `user_id` column defaulting to `"default"`. This is hardcoded for now (single-user) but enables future multi-user support without schema migration.

**users_profile** — User state (cash balance)
- `id` TEXT PRIMARY KEY (default: `"default"`)
- `cash_balance` REAL (default: `10000.0`)
- `created_at` TEXT (ISO timestamp)

**watchlist** — Tickers the user is watching
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT
- `added_at` TEXT (ISO timestamp)
- UNIQUE constraint on `(user_id, ticker)`

**positions** — Current holdings (one row per ticker per user)
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT
- `quantity` REAL (fractional shares supported)
- `avg_cost` REAL
- `updated_at` TEXT (ISO timestamp)
- UNIQUE constraint on `(user_id, ticker)`

**trades** — Trade history (append-only log)
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT
- `side` TEXT (`"buy"` or `"sell"`)
- `quantity` REAL (fractional shares supported)
- `price` REAL
- `executed_at` TEXT (ISO timestamp)

**portfolio_snapshots** — Portfolio value over time (for P&L chart). Written **on trade execution only** — there is no periodic snapshot task.
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `total_value` REAL
- `recorded_at` TEXT (ISO timestamp)

**chat_messages** — Conversation history with LLM
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `role` TEXT (`"user"` or `"assistant"`)
- `content` TEXT
- `actions` TEXT (JSON — see shape below; null for user messages)
- `created_at` TEXT (ISO timestamp)

### `chat_messages.actions` Shape

Stored as a JSON string; rendered inline in the chat panel as confirmation chips. **Failed actions are recorded too** — the frontend shows them as errors, and §9 feeds them back to the LLM.

```json
{
  "trades": [
    {"ticker": "AAPL", "side": "buy", "quantity": 10,
     "status": "executed", "price": 190.50, "total": 1905.00},
    {"ticker": "NVDA", "side": "buy", "quantity": 100,
     "status": "failed", "error_code": "INSUFFICIENT_CASH",
     "error": "Insufficient cash: need $80,000.00, have $8,095.00"}
  ],
  "watchlist_changes": [
    {"ticker": "PYPL", "action": "add", "status": "executed"}
  ]
}
```

`status` is `"executed"` or `"failed"`. Failed entries carry `error_code` and `error` verbatim from the trade endpoint (§8) — one error vocabulary, not two.

### Write Ordering for a Trade

A trade touches three tables and **must be a single SQLite transaction**:

1. Insert into `trades`
2. Upsert `positions` (or delete the row — see below)
3. Update `users_profile.cash_balance`
4. Insert into `portfolio_snapshots`

If any step fails, the whole trade rolls back. Nothing partially applies.

### Position and Cost-Basis Rules

- **Buy** — new `avg_cost` is the weighted average: `(old_qty × old_avg + buy_qty × price) / (old_qty + buy_qty)`
- **Sell** — `avg_cost` is **unchanged**. Selling never alters cost basis
- **Sell to zero** — the `positions` row is **deleted**, not left at `quantity = 0`. The positions table and heatmap show only held positions
- **Realized P&L** is not stored. It is derived when needed as `Σ((sell_price − avg_cost_at_sale) × qty)` from `trades`. It is **not** shown in the positions table (which is unrealized only); it appears only in the LLM's portfolio context (§9)
- Floating-point dust: treat a residual quantity below `1e-9` as zero and delete the row

### Default Seed Data

- One user profile: `id="default"`, `cash_balance=10000.0`
- Ten watchlist entries: AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX

---

## 8. API Endpoints

### Market Data
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/stream/prices` | SSE stream of live price updates |

### Portfolio
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/portfolio` | Current positions and cash balance |
| POST | `/api/portfolio/trade` | Execute a trade: `{ticker, quantity, side}` |
| GET | `/api/portfolio/history` | Portfolio value snapshots (for P&L chart) |

### Watchlist
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/watchlist` | Current watchlist tickers (membership only — prices come from the stream) |
| POST | `/api/watchlist` | Add a ticker: `{ticker}` → returns the full new watchlist |
| DELETE | `/api/watchlist/{ticker}` | Remove a ticker → returns the full new watchlist |

### Chat
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/chat` | Send a message, receive complete JSON response (message + executed actions) |

### System
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check (for Docker/deployment) |

### Error Envelope

Every non-2xx response from every endpoint uses one shape. The `message` is **user-facing prose** and is reused verbatim in the chat panel when the LLM's auto-executed action fails (§9) — so write it for a human, not a log.

```json
{"error": {"code": "INSUFFICIENT_CASH",
           "message": "Insufficient cash: need $80,000.00, have $8,095.00"}}
```

| Code | HTTP | Raised when |
|---|---|---|
| `INVALID_QUANTITY` | 400 | quantity ≤ 0, NaN, or infinite |
| `INVALID_SIDE` | 400 | `side` is not `"buy"` or `"sell"` |
| `INVALID_TICKER` | 400 | Symbol fails the format rule below |
| `UNKNOWN_TICKER` | 404 | No price in the cache for this symbol |
| `UNSUPPORTED_TICKER` | 422 | `supports_ticker()` returned False (§6) |
| `INSUFFICIENT_CASH` | 409 | Buy cost exceeds cash balance |
| `INSUFFICIENT_SHARES` | 409 | Sell quantity exceeds held quantity |
| `WATCHLIST_FULL` | 409 | Watchlist already holds 30 tickers |

### `POST /api/portfolio/trade`

Request: `{"ticker": "AAPL", "quantity": 10, "side": "buy"}`

Fills at the **current cached price** at the moment of execution — never a price supplied by the client. A ticker with no cached price cannot be traded (`UNKNOWN_TICKER`); this is reachable for a just-added ticker under Massive's 15s poll, so the frontend should disable the trade button until a price arrives.

Success (200):

```json
{"trade": {"id": "…", "ticker": "AAPL", "side": "buy",
           "quantity": 10, "price": 190.50, "total": 1905.00,
           "executed_at": "2026-08-24T04:11:00Z"},
 "cash_balance": 8095.00,
 "position": {"ticker": "AAPL", "quantity": 10, "avg_cost": 190.50}}
```

`position` is `null` when a sell closed the position entirely.

### `GET /api/portfolio`

Returns positions and cash only. **Valuation is not computed here** — see §10 for why.

```json
{"cash_balance": 8095.00,
 "positions": [{"ticker": "AAPL", "quantity": 10, "avg_cost": 190.50}]}
```

### `GET /api/portfolio/history`

Query params: `?since=<ISO timestamp>` and `?limit=<int, default 500, max 5000>`, newest-last. Because snapshots are written on trades only (§7), this returns a sparse series — the frontend joins it with the live stream to draw the current segment of the curve.

### `GET|POST|DELETE /api/watchlist`

**Membership only.** Prices come exclusively from the SSE stream, so there is one source of price truth rather than two that can disagree.

```json
{"tickers": ["AAPL", "GOOGL", "MSFT"]}
```

Validation on `POST`:

1. Trim, uppercase
2. Must match `^[A-Z]{1,5}$` → else `INVALID_TICKER`
3. Already present → **200 with the unchanged list** (idempotent, not an error)
4. Watchlist at 30 tickers → `WATCHLIST_FULL`
5. `await source.supports_ticker(t)` is False → `UNSUPPORTED_TICKER`
6. Insert, then `await source.add_ticker(t)`

`DELETE` on an absent ticker is also idempotent (200, unchanged list), and calls `source.remove_ticker(t)`, which drops it from the price cache too.

**Both mutating endpoints return the complete new watchlist.** This is the frontend's only refresh signal — the SSE stream never carries membership. The same applies to watchlist changes made by the LLM, which is why the `/api/chat` response echoes the resulting watchlist (§9).

---

## 9. LLM Integration

When writing code to make calls to LLMs, use cerebras-inference skill to use LiteLLM via OpenRouter to the `openrouter/openai/gpt-oss-120b` model with Cerebras as the inference provider. Structured Outputs should be used to interpret the results.

There is an OPENROUTER_API_KEY in the .env file in the project root.

> Add `litellm` to `backend/pyproject.toml` dependencies — it is not there yet. Do not reach for the `openai` SDK directly.

### How It Works

When the user sends a chat message, the backend:

1. Loads the user's current portfolio context (cash, positions with P&L, watchlist with live prices, total portfolio value)
2. Loads recent conversation history from the `chat_messages` table
3. Constructs a prompt with a system message, portfolio context, conversation history, and the user's new message
4. Calls the LLM via LiteLLM → OpenRouter, requesting structured output, using the cerebras-inference skill
5. Parses the complete structured JSON response
6. Auto-executes any trades or watchlist changes specified in the response
7. Stores the message and executed actions in `chat_messages`
8. Returns the complete JSON response to the frontend (no token-by-token streaming — Cerebras inference is fast enough that a loading indicator is sufficient)

### `POST /api/chat` Response — five fields, always

```json
{"message": "Buying 2 shares of AAPL at the current market price.",
 "actions": {"trades": [{"ticker": "AAPL", "side": "buy", "quantity": 2.0,
                         "status": "executed", "price": 189.95, "total": 379.90}],
             "watchlist_changes": []},
 "watchlist": ["AAPL", "GOOGL", "MSFT", "…"],
 "cash_balance": 9620.10,
 "positions": [{"ticker": "AAPL", "quantity": 2.0, "avg_cost": 189.95}]}
```

`actions` uses the shape defined in §7, failures included. The `watchlist` /
`cash_balance` / `positions` echoes are **not decoration**: the assistant can
change the watchlist and the portfolio mid-turn, and the SSE stream carries no
membership event, so without them the frontend has no signal that either changed
(§8).

**Only an empty message is rejected.** Every other failure — a provider outage, an
unparseable model response, a trade that fails validation — comes back **200**
with the explanation in `message` or in a failed `actions` entry. A chat panel that
renders an error banner instead of a reply is a worse experience than one that says
what went wrong, and §12's E2E suite asserts both degradation paths.

### Structured Output Schema

The LLM is instructed to respond with JSON matching this schema:

```json
{
  "message": "Your conversational response to the user",
  "trades": [
    {"ticker": "AAPL", "side": "buy", "quantity": 10}
  ],
  "watchlist_changes": [
    {"ticker": "PYPL", "action": "add"}
  ]
}
```

- `message` (required): The conversational text shown to the user
- `trades` (optional): Array of trades to auto-execute. Each trade goes through the same validation as manual trades (sufficient cash for buys, sufficient shares for sells)
- `watchlist_changes` (optional): Array of watchlist modifications

### Auto-Execution

Trades specified by the LLM execute automatically — no confirmation dialog. This is a deliberate design choice:
- It's a simulated environment with fake money, so the stakes are zero
- It creates an impressive, fluid demo experience
- It demonstrates agentic AI capabilities — the core theme of the course

If a trade fails validation (e.g., insufficient cash), the error is included in the chat response so the LLM can inform the user.

### System Prompt Guidance

The LLM should be prompted as "FinAlly, an AI trading assistant" with instructions to:
- Analyze portfolio composition, risk concentration, and P&L
- Suggest trades with reasoning
- Execute trades when the user asks or agrees
- Manage the watchlist proactively
- Be concise and data-driven in responses
- Always respond with valid structured JSON

### LLM Mock Mode

When `LLM_MOCK=true`, the backend returns deterministic mock responses instead of calling OpenRouter. This enables:
- Fast, free, reproducible E2E tests
- Development without an API key
- CI/CD pipelines

---

## 10. Frontend Design

### Layout

The frontend is a single-page application with a dense, terminal-inspired layout. The specific component architecture and layout system is up to the Frontend Engineer, but the UI should include these elements:

- **Watchlist panel** — grid/table of watched tickers with: ticker symbol, current price (flashing green/red on change), daily change %, and a sparkline mini-chart (accumulated from SSE since page load)
- **Main chart area** — larger chart for the currently selected ticker, with at minimum price over time. Clicking a ticker in the watchlist selects it here.
- **Portfolio heatmap** — treemap visualization where each rectangle is a position, sized by portfolio weight, colored by P&L (green = profit, red = loss)
- **P&L chart** — line chart showing total portfolio value over time, using data from `portfolio_snapshots`
- **Positions table** — tabular view of all positions: ticker, quantity, avg cost, current price, unrealized P&L, % change
- **Trade bar** — simple input area: ticker field, quantity field, buy button, sell button. Market orders, instant fill.
- **AI chat panel** — docked/collapsible sidebar. Message input, scrolling conversation history, loading indicator while waiting for LLM response. Trade executions and watchlist changes shown inline as confirmations.
- **Header** — portfolio total value (updating live), connection status indicator, cash balance

### Technical Notes

- Use `EventSource` for SSE connection to `/api/stream/prices`
- **Recharts** is the charting library, and it is the only one. §13.3 S3 is the reasoning: the UI needs a sparkline, a detail chart, a line chart *and* a treemap, and Recharts covers all four — Lightweight Charts has no treemap, so picking it would have meant shipping two libraries. Do not add a second one.
- Price flash effect: on receiving a new price, briefly apply a CSS class with background color transition, then remove it
- All API calls go to the same origin (`/api/*`) — no CORS configuration needed
- Tailwind CSS for styling with a custom dark theme

---

## 11. Docker & Deployment

### Multi-Stage Dockerfile

```
Stage 1: node:20-slim
  - COPY frontend/package.json + package-lock.json, then npm ci
  - COPY frontend/, then npm run build  →  static export at /build/out

Stage 2: python:3.12-slim
  - pip install uv, then COPY pyproject.toml + uv.lock
  - uv sync --locked --no-dev --no-install-project
  - COPY backend/app → /app/app,  COPY --from=frontend /build/out → /app/static
  - useradd uid 10001, chown /app, USER finally  (non-root)
  - ENV FINALLY_DB_PATH=/app/db/finally.db  FINALLY_STATIC_DIR=/app/static
  - EXPOSE 8000, HEALTHCHECK on /api/health via urllib
  - CMD uvicorn app.main:app --host 0.0.0.0 --port 8000
```

FastAPI serves the static frontend files and all API routes on port 8000. Layer order in both stages is *manifests, install, then source*, so editing a component or a route rebuilds only the last layer.

Three details are load-bearing and should not be "tidied":

- **`--locked`** fails the build if `uv.lock` is out of date with `pyproject.toml`. That is the failure you want when a dependency was added without relocking.
- **`/app/db` is created and chowned before `USER finally`** so the named volume Docker mounts over it inherits that ownership on first use. Without it the volume lands root-owned and SQLite cannot create its journal.
- **The healthcheck uses `urllib`, not `curl`** — python is already in the image, `curl` is not in `-slim`.

### Docker Volume

The SQLite database persists via a named Docker volume:

```bash
docker run -v finally-data:/app/db -p 8000:8000 --env-file .env finally
```

The `db/` directory in the project root maps to `/app/db` in the container. The backend writes `finally.db` to this path.

### Start/Stop Scripts

**`scripts/start_mac.sh`** (macOS/Linux):
- Builds the Docker image if not already built (or if `--build` flag passed)
- Runs the container with the volume mount, port mapping, and `.env` file
- Prints the URL to access the app
- Optionally opens the browser

**`scripts/stop_mac.sh`** (macOS/Linux):
- Stops and removes the running container
- Does NOT remove the volume (data persists)

**`scripts/start_windows.ps1`** / **`scripts/stop_windows.ps1`**: PowerShell equivalents for Windows.

All scripts are idempotent — safe to run multiple times.

The scripts and `docker compose up` are **interchangeable**: the image tag (`finally:latest`), container name (`finally`) and volume name (`finally-data`) are pinned identically in both, so starting with one and stopping with the other works. `docker-compose.yml` declares the volume with an explicit `name:` for exactly this reason — without it Compose would prefix the project name and the two paths would keep separate databases. `env_file` there is the long form with `required: false`, so a fresh clone with no `.env` still starts (simulator prices, dead chat panel) rather than dying on a stat error.

Both paths bind host port **8000**, so they collide with anything else already on it. To run alongside another service, publish a different host port: `docker run -d --name finally -p 8001:8000 -v finally-data:/app/db --env-file .env finally:latest`.

### Optional Cloud Deployment

The container is designed to deploy to AWS App Runner, Render, or any container platform. A Terraform configuration for App Runner may be provided in a `deploy/` directory as a stretch goal, but is not part of the core build.

---

## 12. Testing Strategy

### What exists today

| Suite | Where | Count | How to run |
|---|---|---|---|
| Backend unit | `backend/tests/` | **627**, 100% statement coverage of `app/` | `cd backend && uv sync --all-extras && uv run pytest` |
| Frontend unit | `frontend/src/**/*.test.ts(x)` | **50** | `cd frontend && npm test` |
| E2E | `test/e2e/` | **34** across 7 specs | see *Running the E2E suite* below |

`uv sync` **without** `--all-extras` (or `--dev`) does not install pytest, and `uv run pytest` then fails with a bare "command not found" that looks like a broken checkout.

### Unit Tests (within `frontend/` and `backend/`)

**Backend (pytest)**:
- Market data: simulator generates valid prices, GBM math is correct, Massive API response parsing works, both implementations conform to the abstract interface
- Portfolio: trade execution logic, P&L calculations, edge cases (selling more than owned, buying with insufficient cash, selling at a loss)
- LLM: structured output parsing handles all valid schemas, graceful handling of malformed responses, trade validation within chat flow
- API routes: correct status codes, response shapes, error handling

The suite is **hermetic by construction**: an autouse fixture in `backend/tests/conftest.py` deletes `OPENROUTER_API_KEY`, `MASSIVE_API_KEY`, `LLM_MOCK`, `FINALLY_DB_PATH` and `FINALLY_STATIC_DIR` before every test. This is not tidiness — `load_dotenv()` (ours, or the one LiteLLM fires at import) copies the developer's real `.env` into `os.environ` for the rest of the session, so without it any assertion about an *absent* variable passes in CI and fails only for the person whose chat panel actually works.

**Frontend (React Testing Library or similar)**:
- Component rendering with mock data
- Price flash animation triggers correctly on price changes
- Watchlist CRUD operations
- Portfolio display calculations
- Chat message rendering and loading state

### E2E Tests (in `test/`)

**Infrastructure**: `test/docker-compose.test.yml` spins up the **production image, unmodified**, plus a Playwright container. This keeps ~500MB of Chromium out of the artifact users download. Every spec drives the UI through one page object, `test/fixtures/terminal.ts`.

**Environment**: `LLM_MOCK=true`, an empty `MASSIVE_API_KEY` (so the in-process simulator ticks at ~500ms rather than waiting on a 15s poll), and a tmpfs over `/app/db` so every run starts from a freshly seeded database. Stated inline in the compose file rather than read from `.env`, because a developer's `.env` may carry a real key or `LLM_MOCK=false` — either turns the suite into a slow, non-deterministic, occasionally-charged mess whose failures look like application bugs.

**Running the E2E suite**:

```bash
# The documented path — production image + Playwright container
docker compose -f test/docker-compose.test.yml up --build \
  --abort-on-container-exit --exit-code-from playwright
docker compose -f test/docker-compose.test.yml down -v

# Against a server you started yourself (no Docker)
E2E_BASE_URL=http://127.0.0.1:8010 npx playwright test    # from test/
```

**Two things that will silently break this harness if "simplified":**

1. **The app service's dotted network alias, `app.finally.test`.** Chromium upgrades plain HTTP to a *single-label* hostname to HTTPS, so `http://app:8000` — the obvious compose service name — fails **every** navigation with `ERR_SSL_PROTOCOL_ERROR` while `curl` to the same URL returns 200. No `--disable-features` flag reaches that code path. Loopback is the one host Chromium exempts, which is why running the suite against `127.0.0.1` hides this completely.
2. **`workers: 1`, `fullyParallel: false`, `retries: 0`.** The app is single-user by design — one cash balance, one watchlist, one position book — so two workers trade against each other's money. And specs mutate persistent state, so a retry re-runs a test whose first attempt already spent cash; a failure here should be read, not re-rolled.

**Key Scenarios** — the spec files, in the order they run:

| Spec | Covers |
|---|---|
| `01-fresh-start` | Seeded watchlist, $10,000 cash, prices streaming, net liquidation equals cash while flat, `/api/health` reports the simulator + mock LLM the suite assumes |
| `02-watchlist` | Add / remove / stream a ticker; idempotent add and remove; a malformed symbol rejected with the backend's own message; removing the selected ticker moves the detail chart |
| `03-trade-buy` | Cash debited, position opened, header repainted; button disabled until a price arrives; zero quantity disables both sides; a buy over the balance rejected with the server's message |
| `04-trade-sell` | Partial sell credits cash and leaves `avg_cost` alone; a sell to zero **deletes** the row; overselling and selling an unheld symbol rejected |
| `05-portfolio-viz` | Heatmap cell per position coloured by P&L; equity curve joins persisted snapshots to the live mark; positions table shows unrealized P&L and a session-based day %; net liquidation equals cash plus every position at the live price |
| `06-chat` | Reply with no action; a trade auto-executed and shown inline as a chip; an unaffordable trade as a failed chip **verbatim**; a watchlist change reaching the panel; provider outage and unparseable response both degrading to prose, not an error |
| `07-sse-resilience` | Live ticks; a stream that **ends mid-session** goes non-live, keeps its prices and reconnects unaided; a refused endpoint recovering; a reload rebuilding the sparkline series; one event carrying every tracked ticker |

`context.setOffline(true)` is deliberately **not** used for the disconnect scenario. Measured against this app: Chromium's offline emulation refuses *new* requests but leaves an already-established socket alone — an open `/api/stream/prices` kept delivering events for the whole offline window while `fetch()` threw. An offline-based test asserts a transition the browser never makes and fails on a healthy app. A stream that ends is the real-world drop (redeploy, proxy timeout, container restart) and is reproducible.

### CI

`.github/workflows/ci.yml`, on every push and pull request. Three jobs, split by what each can prove:

| Job | Runs |
|---|---|
| `backend` | `ruff check`, then pytest at a hard `--cov-fail-under=100` |
| `frontend` | `tsc --noEmit`, vitest, and the real static export (asserting `out/index.html` exists) |
| `e2e` | The compose harness above — the **only** job that proves the Dockerfile builds |

No secrets are needed: the harness pins `LLM_MOCK=true`, so CI never calls a paid provider and never depends on one being up. The Playwright HTML report and traces upload as an artifact on failure.

The e2e job runs on every push rather than being reserved for releases, because the container is the documented way a user starts this project — a broken image is a broken product no matter how green the unit tests are. That is not hypothetical: the single-label-hostname failure above made all 34 E2E tests fail in the container while every one of them passed on loopback.

---

## 13. Review Notes — Questions, Clarifications & Simplifications

> Appended 2026-08-24 by a documentation review pass. **This section is now a historical record, not live specification.** Every `[DRIFT]` and `[OPEN]` item below was resolved in the body of this document before the corresponding code was written, so where §13 and the body disagree, **the body wins** — do not implement anything from §13 directly. It is kept because it records *why* several settled decisions are what they are.
>
> Resolutions, for the reader who arrives here first:
>
> - `session_open` / `change_percent_session` — §6 and the `PriceUpdate` contract
> - SSE event shape and the `retry: 1000` directive — §6
> - Grouped snapshot call and the poll budget — §6
> - Watchlist validation, size cap and `supports_ticker()` — §6, §8
> - Trade success and error contract, one shared error vocabulary — §8
> - Sell semantics: `avg_cost` untouched, sell-to-zero deletes the row — §7
> - `chat_messages.actions` shape, failures included — §7
> - Watchlist membership refresh path (mutating endpoints and `/api/chat` return the full list) — §8
> - `LLM_MOCK` keyword mapping — implemented in `app/llm/mock.py`, documented in `TEAM_LOG.md`
> - Snapshot lifecycle: written on trade execution only, `?since=`/`?limit=` on history — §7, §8
> - Static asset path pinned to `/app/static` — §11
> - `.env.example` — created; `litellm` — added to `backend/pyproject.toml`
>
> Of the §13.3 simplifications: **S2** (snapshot on trades only), **S3** (Recharts alone), **S5** (two deliberate valuation sites) and **S6** (membership-only watchlist) were adopted. **S1** (derive positions from the trade log) and **S4** (drop the scripts for compose) were **not** — `positions` stays a real table, and the four scripts ship alongside `docker-compose.yml`. **S7** is a note, not a change.

### 13.1 Corrections — plan contradicts shipped code

**[DRIFT] SSE event shape (§6).** The plan says "Each SSE event contains ticker, price, previous price, timestamp, and change direction", which reads as one event per ticker. The implementation (`backend/app/market/stream.py`) emits one event per tick whose payload is a JSON object keyed by *every* tracked ticker:

```
retry: 1000

data: {"AAPL": {"ticker":"AAPL","price":190.50,"previous_price":190.40,
                "timestamp":1755993600.12,"change":0.10,"change_percent":0.05,
                "direction":"up"},
       "GOOGL": {...}}
```

Also undocumented and load-bearing for the frontend: the stream only emits when `PriceCache.version` changes (so idle ticks send nothing), and it opens with a `retry: 1000` directive. §6 should carry the literal payload example above.

**[DRIFT] "Daily change %" has no data source (§10).** The watchlist panel specifies a daily change %. `PriceUpdate.change_percent` is **tick-over-tick** — the delta versus the price 500ms ago — and will render as a meaningless ±0.02%. Neither the price cache nor the DB schema stores a previous close or session open.

**[OPEN]** Pick one: (a) add `session_open` to the price contract, anchored at process start, and define % change against it; (b) add a real `previous_close` (trivial for the simulator, available from the Massive snapshot); or (c) restate §10 as "% change since page load" and compute it client-side from the first observed price, matching how sparklines already work.

**[DRIFT] Massive poll budget (§6).** The plan gives "free tier (5 calls/min): poll every 15 seconds" without saying whether that is one call for all tickers or one per ticker — which is the whole question of whether the budget works. The implementation uses a single grouped snapshot call (`get_snapshot_all`) covering every watched ticker per poll. Worth stating, since it is the reason 10+ tickers fit in 5 calls/min.

**[DRIFT] `.env.example` (§4).** The tree comment says ".env # gitignored, .env.example committed". `.env` is correctly gitignored, but no `.env.example` exists in the repo. Create it from §5 or drop the claim.

**[DRIFT] Directory tree is aspirational (§4).** `frontend/`, `scripts/`, `test/`, `db/`, `Dockerfile`, and `docker-compose.yml` are all listed but none exist yet. The section reads as descriptive. Add a one-line "built so far" marker so agents don't assume those paths are populated.

### 13.2 Under-specified contracts — agents will diverge here

**[OPEN] Watchlist add validation (§8).** `POST /api/watchlist {ticker}` doesn't specify symbol normalization (trim/uppercase), duplicate handling (409 vs idempotent 200), a maximum watchlist size, or invalid-symbol rejection. This behaves *differently per data source*: the simulator invents a plausible price for any string via `DEFAULT_PARAMS`, so `POST {"ticker": "BANANA"}` silently succeeds and starts streaming; Massive returns no snapshot, leaving a permanently priceless row in the watchlist. Also undefined: what the UI shows between "added" and the first price tick.

**[OPEN] Trade endpoint response and error contract (§8).** Only the request body is specified. §12 requires tests for insufficient cash, overselling, and quantity edge cases, and §9 requires those same errors to be surfaced back into the chat transcript. Define the success shape and the error codes/messages **once** in §8 so the manual path and the LLM auto-execution path share them rather than inventing two vocabularies. Add explicitly: what happens when a trade targets a ticker with no cached price yet.

**[OPEN] Sell semantics (§7/§10).** The schema has `positions.avg_cost` and an append-only `trades` log, but no realized-P&L field anywhere, and §10's positions table shows only unrealized P&L. State that sells leave `avg_cost` unchanged and that realized P&L is derived from `trades` (or is not surfaced at all). Also resolve §12's "position updates or disappears" — does a sell-to-zero delete the row or leave `quantity = 0`? Two agents will pick two answers.

**[OPEN] `chat_messages.actions` JSON shape (§7/§9).** Described only as "trades executed, watchlist changes made". §10 renders these inline in the chat panel and §9 requires failed trades to carry their error. Give it a concrete schema including the failure case, or the chat backend and the chat UI will not agree.

**[OPEN] Watchlist membership changes have no push path (§6/§8).** The SSE stream carries prices only, never membership. After an add/remove — especially one the *LLM* performed inside a chat turn — the frontend has no specified signal to refetch. Either state that add/remove/chat responses return the full new watchlist, or add a membership event type to the stream.

**[OPEN] `LLM_MOCK` is an unwritten test contract (§9/§12).** §12's E2E scenario asserts that a trade execution appears inline in chat, which means the mock must return an actual populated `trades` array. §9 says only "deterministic mock responses". Specify the mapping (e.g. keyword in user message → canned structured response) or that E2E test cannot be written against it.

**[OPEN] Snapshot task lifecycle (§7/§8).** "Every 30 seconds" — does the task run when no client is connected? As written it does, growing the table ~2,880 rows/day forever. And `/api/portfolio/history` has no `?since=` or `?limit=` parameter, so the P&L chart eventually fetches the entire history on every page load. Add a retention rule or a range parameter.

**[OPEN] Static asset path (§11).** Stage 2 says "copy frontend build output into a `static/` directory" without pinning it relative to the container. The FastAPI mount depends on it — pin it (e.g. `/app/static`).

**Note (§9).** `backend/pyproject.toml` has no `litellm` dependency yet. Expected at this stage, but naming it in §9 will stop the Backend agent reaching for the `openai` SDK instead.

### 13.3 Opportunities to simplify

These are genuine reductions in scope, not style preferences. Each trades something — the trade is named.

**S1. Make `trades` the only mutable table.** Both `positions` and `users_profile.cash_balance` are derivable from the append-only `trades` log (`cash = 10000 − Σbuys + Σsells`; position quantity and avg cost fold out of the same log). As specified, every trade is a multi-row write that must stay transactionally consistent, and any bug there corrupts state permanently. Deriving instead deletes a table, a class of drift bugs, and the "does a zeroed position get deleted" question in §13.2 — a demo portfolio will never hold enough trades for the recompute cost to matter. *Trade-off:* an O(n) fold per portfolio read instead of an O(1) lookup, and `users_profile` shrinks to almost nothing.

**S2. Let the client own the P&L curve, like it already owns sparklines.** §2 already establishes that sparklines accumulate on the frontend from the SSE stream since page load. Total portfolio value is the same computation over the same stream. Adopting that pattern for the P&L chart removes the `portfolio_snapshots` table, the 30-second background task, and `GET /api/portfolio/history` — three moving parts. *Trade-off:* the P&L curve resets on reload. If persistence across reloads matters for the demo, keep the table but drop the timer and snapshot on trades only, which still removes the background task.

**S3. Pick one charting library.** §10 offers "Lightweight Charts or Recharts". The UI needs a sparkline, a detail chart, a line chart, **and a treemap** — Lightweight Charts does not do treemaps, so choosing it means shipping two libraries. Recharts covers all four. Naming Recharts outright removes a bundle and a decision.

**S4. Drop the four start/stop scripts in favour of `docker compose`.** §4 specifies `start_mac.sh`, `stop_mac.sh`, `start_windows.ps1`, `stop_windows.ps1`, and §11 requires all four to be idempotent — while §4 *also* lists a `docker-compose.yml` as an "optional convenience wrapper". `docker compose up` / `docker compose down` is already idempotent, already handles the volume, port, and `--env-file` wiring, and is byte-identical across macOS, Linux, and Windows. Promoting compose to the documented path removes four scripts and two platform-specific maintenance surfaces. *Trade-off:* loses the "open the browser automatically" nicety, and assumes students have Compose (bundled with Docker Desktop, so effectively free).

**S5. Compute valuation math in exactly two places, deliberately.** `/api/portfolio` returns server-computed total value and unrealized P&L, but the header is specified as "updating live", so the client must recompute the same numbers between fetches anyway — and the LLM context in §9 needs them server-side too. That is three implementations of one formula unless it's called out. Specify: one server-side helper shared by `/api/portfolio` and the chat context builder, one client-side live calculation, and make the client authoritative for anything displayed.

**S6. Simplify `GET /api/watchlist` to tickers only.** It currently returns "tickers with latest prices", duplicating what the SSE stream pushes 500ms later and creating a second place prices can be stale or disagree. Returning membership only makes the split clean: REST owns membership, SSE owns prices.

**S7. Note what `user_id` actually buys.** Present on all five tables and described as enabling "future multi-user support without schema migration". Fair, and the cost is low — but with no auth, no sessions, and a single hardcoded `"default"`, real multi-user support needs far more than these columns, so the columns are not the constraint they appear to guard against. Worth keeping (cheap, harmless); worth not treating the UNIQUE `(user_id, ticker)` constraints as if multi-user is nearly free.

### 13.4 What holds up well

- §6 matches the built subsystem closely — the abstract interface, cache-as-single-source-of-truth, factory-by-env-var, and GBM with correlated moves are all implemented as described.
- The decision table in §3 is the strongest part of this document: it records *why*, so agents extend the architecture consistently instead of re-litigating settled choices.
- The schema in §7 is coherent, and lazy initialization (§7) genuinely removes a migration step rather than hiding one.
- Market-orders-only (§3) is doing a lot of load-bearing simplification work and should be defended if scope creep arrives.
