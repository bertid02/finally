# FinAlly Backend

FastAPI backend for the FinAlly AI Trading Workstation. `uv` project, Python ≥ 3.12.

For the developer guide — subsystem-by-subsystem public API, the contracts that
matter, and the traps — see [`CLAUDE.md`](CLAUDE.md) alongside this file. The
project specification is [`../planning/PLAN.md`](../planning/PLAN.md).

## Structure

- `app/`
  - `market/` — market data subsystem (193 tests)
    - `models.py` — `PriceUpdate`; `cache.py` — thread-safe price cache
    - `interface.py` — `MarketDataSource`; `factory.py` — selection by env var
    - `simulator.py` — GBM simulator; `massive_client.py` — Massive/Polygon.io client
    - `stream.py` — SSE router; `seed_prices.py` — default tickers and params
  - `db/` — persistence (230 tests)
    - `schema.py` — DDL, seed data, constants; `connection.py` — `Database`
    - `repository.py` — all reads and writes, incl. the trade transaction
    - `models.py` — `Portfolio`, `Position`, `Trade`, `TradeResult`, …
    - `exceptions.py` — typed failures carrying PLAN.md §8 codes
  - `api/` — HTTP surface (99 tests)
    - `portfolio.py`, `watchlist.py`, `chat.py`, `health.py`
    - `errors.py` — the one error envelope; `valuation.py` — the one valuation helper
    - `deps.py` — request-scoped dependencies
  - `llm/` — chat turn (105 tests)
    - `client.py` — LiteLLM → OpenRouter; `prompt.py` — system prompt and context
    - `schema.py` — structured-output contract; `mock.py` — `LLM_MOCK` responses
    - `service.py` — `run_chat_turn()`, auto-execution
  - `config.py` — `Settings` / `load_settings()`; `main.py` — `create_app()`, lifespan
- `tests/` — mirrors `app/` package-for-package (`market/`, `db/`, `api/`, `llm/`)

## Running Tests

```bash
uv sync --all-extras                     # --all-extras installs pytest and ruff
uv run pytest                            # 627 tests
uv run pytest --cov=app --cov-report=html
uv run pytest tests/market/test_simulator.py
uv run pytest -v
```

100% statement coverage of `app/` today, enforced in CI at `--cov-fail-under=100`.

## Environment Variables

| Variable | Effect |
|---|---|
| `MASSIVE_API_KEY` | Set → real market data from Massive. Absent or empty → built-in simulator. |
| `OPENROUTER_API_KEY` | Required for AI chat. Absent → the app runs and the chat panel says so. |
| `LLM_MOCK` | `true` → deterministic canned LLM responses; no key needed. |
| `FINALLY_DB_PATH` | SQLite file. Defaults to `db/finally.db`; the image sets `/app/db/finally.db`. |
| `FINALLY_STATIC_DIR` | Static export to serve. Defaults to `/app/static`; absent is fine locally. |

All five are read from the project-root `.env` as well as the process environment,
once, at app construction. An exported variable beats the file.

## Development

```bash
uv sync --all-extras
uv run ruff check .        # the CI gate
uv run python market_data_demo.py   # live simulator dashboard in the terminal

# Serve the API (and a built frontend, if there is one)
FINALLY_DB_PATH=../db/finally.db FINALLY_STATIC_DIR=../frontend/out \
  uv run uvicorn app.main:app --port 8000
```

`uv run ruff format .` is deliberately **not** part of the workflow: it is not a CI
gate and 15 files do not currently satisfy it, so running it buries your diff in
unrelated reformatting.
