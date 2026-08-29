"""Tests for the SSE price stream.

The payload shape here is the frontend contract (PLAN.md section 6). These tests
exist because stream.py previously had no coverage at all.
"""

import asyncio
import json

import pytest
from fastapi import FastAPI

from app.market.cache import PriceCache
from app.market.stream import _generate_events, create_stream_router


class _FakeRequest:
    """Minimal Request stand-in for driving _generate_events directly.

    Disconnects after `alive_for` checks so the generator terminates.
    """

    def __init__(self, alive_for: int = 3):
        self._remaining = alive_for
        self.client = None

    async def is_disconnected(self) -> bool:
        self._remaining -= 1
        return self._remaining < 0


async def _collect(cache, alive_for=3, interval=0.0, keepalive=1e9, mutate=None):
    """Drain the generator, optionally mutating the cache between ticks."""
    events = []
    request = _FakeRequest(alive_for=alive_for)
    generator = _generate_events(cache, request, interval=interval, keepalive=keepalive)
    tick = 0
    async for chunk in generator:
        events.append(chunk)
        if mutate:
            mutate(tick)
        tick += 1
    return events


class TestRouterConstruction:
    def test_each_call_returns_a_distinct_router(self):
        """A module-level router would register /prices twice on the second call
        and the first cache passed would win forever."""
        first = create_stream_router(PriceCache())
        second = create_stream_router(PriceCache())

        assert first is not second
        assert [r.path for r in first.routes] == ["/api/stream/prices"]
        assert [r.path for r in second.routes] == ["/api/stream/prices"]

    def test_endpoint_path_matches_the_plan(self):
        router = create_stream_router(PriceCache())
        assert router.routes[0].path == "/api/stream/prices"


@pytest.mark.asyncio
class TestEventGeneration:
    async def test_opens_with_a_retry_directive(self):
        """EventSource uses this to auto-reconnect."""
        events = await _collect(PriceCache(), alive_for=1)
        assert events[0] == "retry: 1000\n\n"

    async def test_emits_every_ticker_in_one_event(self):
        """One event per tick carries every ticker, keyed by symbol — not one
        event per ticker."""
        cache = PriceCache()
        cache.update("AAPL", 190.50, session_open=189.00)
        cache.update("GOOGL", 175.25, session_open=175.00)

        events = await _collect(cache, alive_for=1)
        data = json.loads(events[1].removeprefix("data: ").strip())

        assert set(data) == {"AAPL", "GOOGL"}
        assert data["AAPL"]["price"] == 190.50
        assert data["GOOGL"]["price"] == 175.25

    async def test_payload_carries_the_full_frontend_contract(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00, session_open=180.00)
        cache.update("AAPL", 190.50)

        events = await _collect(cache, alive_for=1)
        payload = json.loads(events[1].removeprefix("data: ").strip())["AAPL"]

        assert set(payload) == {
            "ticker",
            "price",
            "previous_price",
            "session_open",
            "timestamp",
            "change",
            "change_percent",
            "change_session",
            "change_percent_session",
            "direction",
        }
        assert payload["direction"] == "up"
        assert payload["session_open"] == 180.00
        assert payload["change_percent_session"] == pytest.approx(5.8333, abs=1e-3)

    async def test_events_are_sse_framed(self):
        cache = PriceCache()
        cache.update("AAPL", 190.50)

        events = await _collect(cache, alive_for=1)
        assert events[1].startswith("data: ")
        assert events[1].endswith("\n\n")

    async def test_idle_cache_emits_nothing(self):
        """Clients must not treat silence as a disconnect."""
        cache = PriceCache()
        cache.update("AAPL", 190.50)

        events = await _collect(cache, alive_for=5)
        data_events = [e for e in events if e.startswith("data: ")]
        assert len(data_events) == 1  # the initial snapshot only

    async def test_empty_cache_emits_no_data_events(self):
        events = await _collect(PriceCache(), alive_for=4)
        assert [e for e in events if e.startswith("data: ")] == []

    async def test_new_version_produces_a_new_event(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00)

        def mutate(tick):
            if tick == 1:
                cache.update("AAPL", 191.00)

        events = await _collect(cache, alive_for=4, mutate=mutate)
        prices = [
            json.loads(e.removeprefix("data: ").strip())["AAPL"]["price"]
            for e in events
            if e.startswith("data: ")
        ]
        assert prices == [190.00, 191.00]

    async def test_removal_reaches_the_client(self):
        """remove() bumps the version, so the browser stops showing a ticker the
        backend has forgotten."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.update("GOOGL", 175.00)

        def mutate(tick):
            if tick == 1:
                cache.remove("GOOGL")

        events = await _collect(cache, alive_for=4, mutate=mutate)
        payloads = [
            set(json.loads(e.removeprefix("data: ").strip()))
            for e in events
            if e.startswith("data: ")
        ]
        assert payloads == [{"AAPL", "GOOGL"}, {"AAPL"}]

    async def test_keepalive_after_idle(self):
        """Intermediaries close silent connections; EventSource ignores comments."""
        cache = PriceCache()
        cache.update("AAPL", 190.50)

        events = await _collect(cache, alive_for=6, interval=0.05, keepalive=0.1)
        assert ": keepalive\n\n" in events

    async def test_keepalive_resets_on_a_real_event(self):
        cache = PriceCache()
        cache.update("AAPL", 190.50)

        def mutate(tick):
            cache.update("AAPL", 190.50 + tick)

        events = await _collect(cache, alive_for=6, interval=0.05, keepalive=0.1, mutate=mutate)
        assert ": keepalive\n\n" not in events

    async def test_generator_stops_on_disconnect(self):
        cache = PriceCache()
        cache.update("AAPL", 190.50)

        events = await _collect(cache, alive_for=2)
        assert len(events) <= 3  # retry + data, then the disconnect check ends it

    async def test_cancelled_task_is_logged_not_raised(self):
        """Server shutdown cancels in-flight streams; that is not an error."""
        cache = PriceCache()
        cache.update("AAPL", 190.50)

        async def consume():
            async for _ in _generate_events(cache, _FakeRequest(alive_for=10**6), interval=0.01):
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_cancellation_is_handled(self):
        cache = PriceCache()
        cache.update("AAPL", 190.50)
        generator = _generate_events(cache, _FakeRequest(alive_for=1000), interval=0.01)

        await generator.__anext__()  # retry directive
        await generator.aclose()  # must not raise


@pytest.mark.asyncio
class TestEndpoint:
    """Drives the real route handler.

    Neither httpx's ASGITransport nor Starlette's TestClient can consume an
    endless SSE body — both buffer the whole response before returning, so they
    deadlock on a stream that never ends. Calling the handler and draining its
    StreamingResponse exercises the same code path without a transport.
    """

    @staticmethod
    def _handler(router):
        return router.routes[0].endpoint

    async def _drain(self, router, alive_for=2):
        request = _FakeRequest(alive_for=alive_for)
        response = await self._handler(router)(request)
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        return response, chunks

    async def test_response_envelope(self):
        cache = PriceCache()
        cache.update("AAPL", 190.50, session_open=189.00)
        router = create_stream_router(cache, interval=0.0)

        response, _ = await self._drain(router)

        assert response.status_code == 200
        assert response.media_type == "text/event-stream"
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"

    async def test_endpoint_streams_prices(self):
        cache = PriceCache()
        cache.update("AAPL", 190.50, session_open=189.00)
        router = create_stream_router(cache, interval=0.0)

        _, chunks = await self._drain(router)
        body = "".join(chunks)

        assert body.startswith("retry: 1000")
        payload = json.loads(body.split("data: ", 1)[1].split("\n\n", 1)[0])
        assert payload["AAPL"]["price"] == 190.50
        assert payload["AAPL"]["session_open"] == 189.00

    async def test_router_can_be_mounted_on_an_app(self):
        app = FastAPI()
        app.include_router(create_stream_router(PriceCache()))
        paths = [r.path for r in app.routes if r.path.startswith("/api")]
        assert paths == ["/api/stream/prices"]

    async def test_two_routers_keep_their_own_caches(self):
        """The regression a shared module-level router would cause."""
        first, second = PriceCache(), PriceCache()
        first.update("AAPL", 100.00)
        second.update("TSLA", 250.00)

        router_first = create_stream_router(first, interval=0.0)
        router_second = create_stream_router(second, interval=0.0)

        _, chunks = await self._drain(router_second)
        payload = json.loads("".join(chunks).split("data: ", 1)[1].split("\n\n", 1)[0])
        assert set(payload) == {"TSLA"}

        _, chunks = await self._drain(router_first)
        payload = json.loads("".join(chunks).split("data: ", 1)[1].split("\n\n", 1)[0])
        assert set(payload) == {"AAPL"}


@pytest.mark.asyncio
class TestConcurrency:
    async def test_multiple_clients_see_the_same_stream(self):
        cache = PriceCache()
        cache.update("AAPL", 190.50)

        results = await asyncio.gather(
            _collect(cache, alive_for=1),
            _collect(cache, alive_for=1),
        )
        first = json.loads(results[0][1].removeprefix("data: ").strip())
        second = json.loads(results[1][1].removeprefix("data: ").strip())
        assert first == second
