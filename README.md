# FinAlly — AI Trading Workstation

A dark, data-dense trading terminal with live streaming prices, a simulated $10k portfolio, and an LLM copilot that can analyze positions and execute trades from natural language.

Built entirely by coding agents as the capstone for an agentic AI coding course. The full specification lives in [`planning/PLAN.md`](planning/PLAN.md).

## Status

The market data subsystem is complete ([summary](planning/MARKET_DATA_SUMMARY.md)). The rest — portfolio, chat, frontend, Docker — is still to be built.

| Component | State |
|---|---|
| `backend/app/market/` — simulator, Massive client, price cache, SSE stream | ✅ built, 73 tests |
| Portfolio & watchlist API, SQLite persistence | ⬜ planned |
| LLM chat integration | ⬜ planned |
| Next.js frontend | ⬜ planned |
| Dockerfile, start/stop scripts, E2E tests | ⬜ planned |

## Architecture

One container, one port (8000). FastAPI serves the REST API, the SSE price stream, and the statically exported Next.js frontend.

- **Frontend** — Next.js + TypeScript + Tailwind, built as a static export
- **Backend** — FastAPI, managed with `uv`
- **Database** — SQLite at `db/finally.db`, lazily initialized and seeded
- **Real-time** — Server-Sent Events, one event per tick carrying every ticker
- **AI** — LiteLLM → OpenRouter (`openai/gpt-oss-120b` on Cerebras) with structured outputs
- **Market data** — GBM simulator by default; Massive (Polygon.io) REST polling when a key is present

## Running the backend

```bash
cd backend
uv sync --dev
uv run pytest                    # 73 tests
uv run python market_data_demo.py  # live simulator in the terminal
```

## Environment

Create `.env` in the project root:

```bash
OPENROUTER_API_KEY=   # required for AI chat
MASSIVE_API_KEY=      # optional; omit to use the built-in simulator
LLM_MOCK=false        # true for deterministic mock LLM responses in tests
```

## Layout

```
backend/    FastAPI uv project (app/market/ is complete)
planning/   PLAN.md — the shared contract all agents work from
frontend/   Next.js static export (not yet created)
test/       Playwright E2E tests (not yet created)
db/         SQLite volume mount at runtime
```

## License

See [LICENSE](LICENSE).
