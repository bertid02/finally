"""GET /api/health -- liveness for Docker, and the one place a silent fallback shows."""

from __future__ import annotations

from fastapi import APIRouter, Request

from .deps import MarketSourceDep, PriceCacheDep

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
async def health(
    request: Request,
    source: MarketSourceDep,
    cache: PriceCacheDep,
) -> dict:
    """Health check.

    `market_source` is what is actually running; `requested_source` is what the
    environment asked for. They differ when MASSIVE_API_KEY was set but the plan
    does not include the snapshot endpoint and startup fell back to the
    simulator -- PLAN.md section 6 wants that visible rather than quietly lied
    about, so `fallback` states it outright.
    """
    settings = request.app.state.settings
    running = source.name
    return {
        "status": "ok",
        "market_source": running,
        "requested_source": settings.requested_source,
        "fallback": running != settings.requested_source,
        "tickers": len(cache),
        # Presence of an OpenRouter key, and nothing more about it -- this
        # endpoint is unauthenticated and ends up in bug reports.
        "llm_configured": settings.llm_configured,
        "llm_mock": settings.llm_mock,
        "static": settings.has_static,
    }
