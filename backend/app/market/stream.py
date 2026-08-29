"""SSE streaming endpoint for live price updates."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache

logger = logging.getLogger(__name__)

# Emit a comment line if the cache has been idle this long. EventSource ignores
# comments, but intermediaries (nginx, load balancers) close silent connections.
KEEPALIVE_INTERVAL = 15.0


def create_stream_router(price_cache: PriceCache, interval: float = 0.5) -> APIRouter:
    """Create the SSE streaming router bound to a price cache.

    The router is constructed here, not at module scope: a module-level router
    would be shared across calls, so a second call would register /prices twice
    and the first cache passed would win forever.
    """
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        """SSE endpoint for live price updates.

        Streams all tracked ticker prices every ~500ms. The client connects
        with EventSource and receives events in the format:

            data: {"AAPL": {"ticker": "AAPL", "price": 190.50, ...}, ...}

        One event per tick carries every ticker. Events are only emitted when the
        cache version changes, so an idle cache sends nothing — clients must not
        treat silence as a disconnect.

        Includes a retry directive so the browser auto-reconnects on
        disconnection (EventSource built-in behavior).
        """
        return StreamingResponse(
            _generate_events(price_cache, request, interval=interval),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # Disable nginx buffering if proxied
            },
        )

    return router


async def _generate_events(
    price_cache: PriceCache,
    request: Request,
    interval: float = 0.5,
    keepalive: float = KEEPALIVE_INTERVAL,
) -> AsyncGenerator[str, None]:
    """Async generator that yields SSE-formatted price events.

    Emits only when the cache version changes; sends a comment keepalive when the
    cache has been idle. Stops when the client disconnects (detected via
    request.is_disconnected()).
    """
    # Tell the client to retry after 1 second if the connection drops
    yield "retry: 1000\n\n"

    last_version = -1
    idle = 0.0
    client_ip = request.client.host if request.client else "unknown"
    logger.info("SSE client connected: %s", client_ip)

    try:
        while True:
            # Check for client disconnect
            if await request.is_disconnected():
                logger.info("SSE client disconnected: %s", client_ip)
                break

            current_version = price_cache.version
            if current_version != last_version:
                last_version = current_version
                prices = price_cache.get_all()

                if prices:
                    data = {ticker: update.to_dict() for ticker, update in prices.items()}
                    payload = json.dumps(data)
                    idle = 0.0
                    yield f"data: {payload}\n\n"
            else:
                idle += interval
                if idle >= keepalive:
                    idle = 0.0
                    yield ": keepalive\n\n"

            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        # Log and re-raise: swallowing this would leave the task looking like it
        # completed normally and break cooperative shutdown.
        logger.info("SSE stream cancelled for: %s", client_ip)
        raise
