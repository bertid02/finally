# FinAlly — AI Trading Workstation

A dark, data-dense trading terminal with live streaming prices, a simulated $10k portfolio, and an LLM copilot that can analyze positions and execute trades from natural language.

Built entirely by coding agents as the capstone for an agentic AI coding course. The full specification lives in [`planning/PLAN.md`](planning/PLAN.md).

## Quick start

```bash
cp .env.example .env          # then add your OPENROUTER_API_KEY for the AI chat
./scripts/start_mac.sh        # builds the image, runs it, opens a browser
```

Windows: `scripts\start_windows.ps1`. Or use Compose directly — `docker compose up -d --build`. Either way the app is at **http://localhost:8000**, and `./scripts/stop_mac.sh` (or `docker compose down`) stops it while keeping your portfolio in the `finally-data` volume.

The scripts and Compose are interchangeable: same image tag, container name and volume. Both bind host port 8000; to run alongside something already there, publish a different port:

```bash
docker run -d --name finally -p 8001:8000 -v finally-data:/app/db --env-file .env finally:latest
```

No `.env`? The app still runs — simulated market data, working portfolio, and a chat panel that tells you the key is missing.

## Status

Complete and verified end to end.

| Component | State |
|---|---|
| `backend/app/market/` — simulator, Massive client, price cache, SSE stream | ✅ 193 tests |
| `backend/app/db/` — schema, lazy init, repository, transactional trades | ✅ 230 tests |
| `backend/app/api/` — portfolio, watchlist, chat, health, error envelope | ✅ 99 tests |
| `backend/app/llm/` — LiteLLM client, structured output, auto-exec, mock mode | ✅ 105 tests |
| `frontend/` — Next.js static export, SSE, charts, heatmap, chat panel | ✅ 50 tests |
| Dockerfile, Compose, start/stop scripts | ✅ image builds, both paths verified |
| `test/` — Playwright E2E against the production container | ✅ 34 tests |
| CI — `.github/workflows/ci.yml` | ✅ backend + frontend + e2e on every push |

**627 backend tests at 100% statement coverage of `app/`, 50 frontend, 34 E2E.**

## Architecture

One container, one port (8000). FastAPI serves the REST API, the SSE price stream, and the statically exported Next.js frontend.

- **Frontend** — Next.js + TypeScript + Tailwind + Recharts, built as a static export
- **Backend** — FastAPI, managed with `uv`
- **Database** — SQLite at `db/finally.db`, lazily initialized and seeded
- **Real-time** — Server-Sent Events, one event per tick carrying every ticker
- **AI** — LiteLLM → OpenRouter (`openai/gpt-oss-120b` on Cerebras) with structured outputs
- **Market data** — GBM simulator by default; Massive (Polygon.io) REST polling when a key is present

## Development

```bash
# Backend
cd backend
uv sync --all-extras              # --all-extras installs pytest; plain `uv sync` does not
uv run pytest                     # 627 tests
uv run pytest --cov=app           # 100% statement coverage of app/
uv run ruff check .
uv run python market_data_demo.py # live simulator in the terminal

# Frontend
cd frontend
npm ci
npm test                          # 50 tests
npm run dev                       # localhost:3000 against NEXT_PUBLIC_USE_MOCK_API
npm run build                     # static export to frontend/out

# E2E — production image + Playwright container
docker compose -f test/docker-compose.test.yml up --build \
  --abort-on-container-exit --exit-code-from playwright
docker compose -f test/docker-compose.test.yml down -v
```

Running the backend outside Docker needs both paths pointed somewhere real, since the image's defaults are absolute:

```bash
cd backend
FINALLY_DB_PATH=../db/finally.db FINALLY_STATIC_DIR=../frontend/out \
  uv run uvicorn app.main:app --port 8000
```

## Environment

Create `.env` in the project root (see [`.env.example`](.env.example)):

```bash
OPENROUTER_API_KEY=   # required for AI chat; without it only the chat panel is dead
MASSIVE_API_KEY=      # optional; omit to use the built-in simulator
LLM_MOCK=false        # true for deterministic mock LLM responses in tests
```

`GET /api/health` reports what the process actually resolved — market source, whether it fell back, ticker count, `llm_configured` (key presence only, never the key) and `llm_mock`.

## Layout

```
backend/    FastAPI uv project — market, db, api, llm
frontend/   Next.js static export
planning/   PLAN.md (the contract), TEAM.md (ownership), TEAM_LOG.md (decisions)
test/       Playwright E2E + docker-compose.test.yml
scripts/    start/stop for macOS/Linux and Windows
db/         SQLite volume mount at runtime
```

## License

See [LICENSE](LICENSE).
