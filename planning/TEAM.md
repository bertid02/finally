# FinAlly Build Team

Six specialist agents defined in `.claude/agents/`. `PLAN.md` is the shared contract; this file is the ownership map and build order.

**All six stages are complete.** Every path in the table below is populated and the whole thing passes: 627 backend tests at 100% statement coverage of `app/`, 50 frontend, 34 Playwright E2E against the production image, plus `.github/workflows/ci.yml` running all three on every push. The build sequence below is now a record of how it was assembled, and of the dependency order any future change still has to respect.

## Roster

| Agent | Owns | Never touches |
|---|---|---|
| `db-engineer` | `backend/app/db/**`, `backend/tests/db/**` | API routes, LLM, market, frontend |
| `backend-api-engineer` | `backend/app/main.py`, `backend/app/api/**`, `backend/app/config.py`, `backend/pyproject.toml`, `backend/tests/api/**` | DB internals, LLM prompts, market module, frontend |
| `llm-engineer` | `backend/app/llm/**`, `backend/app/api/chat.py`, `backend/tests/llm/**` | DB internals, other routers, frontend |
| `frontend-engineer` | `frontend/**` | All Python, Dockerfile, `test/**` |
| `devops-engineer` | `Dockerfile`, `docker-compose.yml`, `scripts/**`, `.env.example`, `.github/**` | Application code |
| `integration-tester` | `test/**` | Application code — **files bugs, does not fix them** |

Paths are exclusive. No two agents write the same file. Cross-boundary requests go through `TEAM_LOG.md`.

## Already built — do not rebuild

`backend/app/market/` is **complete and reviewed**: 8 modules, 193 tests, 100% statement coverage. See `MARKET_DATA_SUMMARY.md`. It provides `PriceCache`, `create_market_data_source()`, and a ready-made SSE router via `create_stream_router()`. Consume it; do not modify it.

## Build sequence

Dependencies are real — later stages import earlier ones.

```
Stage 1 (parallel)   db-engineer          frontend-engineer
                     schema, repos,       Next.js shell, SSE hook,
                     trade transaction    components against mock data
                          │                        │
Stage 2                   ▼                        │
                     backend-api-engineer          │
                     routes, error envelope,       │
                     market wiring, valuation      │
                          │                        │
Stage 3                   ▼                        │
                     llm-engineer                  │
                     chat, structured output,      │
                     auto-exec, LLM_MOCK           │
                          │                        │
Stage 4                   └──────────┬─────────────┘
                                     ▼
                              devops-engineer
                              Dockerfile, compose, scripts
                                     │
Stage 5                              ▼
                              integration-tester
                              Playwright E2E → files failures
                                     │
Stage 6                              ▼
                              owning engineers fix → re-run
```

The frontend runs in parallel from the start because it codes against the documented API contract, not a running server. It cannot *fully* verify until Stage 2 lands.

### What Stage 5 actually found

Worth recording, because it is the argument for the stage existing at all. The first
real E2E run produced three defects and **all three were in the test suite, none in
the application**:

1. A page-object race on the order bar's confirmation notice — it lingers 5s and every
   confirmation matches the same shape, so a sell placed straight after a buy read the
   buy's notice. Fixed by waiting on `POST /api/portfolio/trade` instead of the DOM.
2. A disconnect test built on `context.setOffline(true)`, which cannot pass: Chromium's
   offline emulation refuses new requests but leaves an established socket alone, so the
   SSE stream kept delivering events. Replaced with a stream that ends mid-session.
3. Chromium refusing plain HTTP to the *single-label* host `http://app:8000`, failing
   every navigation in the container while all 34 passed on loopback. Fixed with a dotted
   compose alias, `app.finally.test`.

The third one is the load-bearing lesson: a suite only ever run against `127.0.0.1`
reported green on a harness that could not navigate at all. Run the container path.

## Contracts that cross boundaries

These are the seams where two agents can disagree. Each has one owner and one definition.

| Contract | Defined by | Consumed by |
|---|---|---|
| Error codes + envelope | `backend-api-engineer`, one module | `llm-engineer`, `frontend-engineer`, `integration-tester` |
| Repository API + domain exceptions | `db-engineer` | `backend-api-engineer`, `llm-engineer` |
| Server-side valuation helper | `backend-api-engineer`, one function | `llm-engineer` |
| `chat_messages.actions` JSON shape | `PLAN.md` §7 — already fixed | `llm-engineer`, `frontend-engineer` |
| `LLM_MOCK` keyword → response mapping | `llm-engineer`, documented in `TEAM_LOG.md` | `integration-tester` |
| Static export path `/app/static` | `devops-engineer` + `backend-api-engineer` must agree | — |
| SSE payload shape | `app/market/stream.py` — already shipped | `frontend-engineer` |

## Three things that cause silent, expensive bugs

1. **`change_percent` vs `change_percent_session`.** The first is tick-over-tick (±0.02% noise) and drives the flash animation *only*. The second is versus `session_open` and is the displayed daily change. Rendering the first as a number is wrong and looks plausible.
2. **Stream silence is not a disconnect.** The SSE stream emits only when the price cache version changes. No inactivity timeouts, client-side or in tests.
3. **The watchlist has no push path.** Membership refreshes only from the return values of `POST`/`DELETE /api/watchlist` **and `POST /api/chat`** — the AI can change the watchlist mid-turn.

## Note on PLAN.md §13

§13 is an **archived review pass**, not live specification. Its `[OPEN]` items have since been resolved in the body of the plan — `session_open` (§6), sell semantics and the actions shape (§7), the error envelope and watchlist validation (§8), history pagination (§8). Where §13 and the body disagree, **the body wins**. Its §13.3 simplification proposals (S1–S7) were partly adopted: S2 (snapshot on trades only), S3 (Recharts), S5 (two deliberate valuation sites), S6 (membership-only watchlist). S1 and S4 were **not** adopted — `positions` stays a real table, and the four scripts stay alongside compose.
