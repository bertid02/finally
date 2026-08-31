# FinAlly Project - the Finance Ally

All project documentation is in the `planning` directory.

The key document is PLAN.md, included in full below. It has been kept current: §4's tree, §11's Dockerfile, §12's test and CI story and §13's resolution notes all describe what is actually built rather than what was planned.

**The platform is complete.** Backend (market, db, api, llm), frontend, container, scripts and E2E suite all exist and pass: 627 backend tests at 100% statement coverage of `app/`, 50 frontend, 34 Playwright E2E against the production image. `.github/workflows/ci.yml` runs all three on every push. Treat additions as changes to a working system, not as filling in gaps.

Other documents worth knowing about, consulted only when relevant:

- `planning/TEAM.md` — which agent owns which paths, and the contracts that cross those boundaries
- `planning/TEAM_LOG.md` — append-only record of cross-boundary decisions and handoffs; the `LLM_MOCK` keyword mapping lives here
- `planning/MARKET_DATA_SUMMARY.md` — the market subsystem's shipped surface, with the full design record in `planning/MARKET_DATA_DESIGN.md` and the review that closed it out in `planning/MARKET_DATA_REVIEW.md`. `planning/archive/` holds the *earlier, shorter* drafts of those same five documents — the copies in `planning/` are the current ones
- `backend/CLAUDE.md` — the backend developer guide: each subsystem's public API and the traps in it

PLAN.md §13 is a **historical review pass, not live specification** — where it and the body of the plan disagree, the body wins.

@planning/PLAN.md