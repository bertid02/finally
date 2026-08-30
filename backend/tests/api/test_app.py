"""App assembly: lifespan wiring, the SSE mount, and serving the frontend export."""

from __future__ import annotations

import asyncio
import json

from httpx import ASGITransport, AsyncClient

from app.config import DEFAULT_STATIC_DIR, Settings, load_settings
from app.db import Database, set_database
from app.main import create_app


async def test_lifespan_seeds_the_source_from_the_watchlist(client):
    """A ticker removed last session must not come back streaming after a restart."""
    tickers = (await client.get("/api/watchlist")).json()["tickers"]
    assert sorted(client.app.state.market_source.get_tickers()) == sorted(tickers)


async def test_lifespan_stops_the_source_on_shutdown(app):
    async with app.router.lifespan_context(app):
        source = app.state.market_source
    assert source._task is None


async def _read_sse(app, path: str = "/api/stream/prices") -> tuple[int, dict, str]:
    """Drive the ASGI app directly until the first `data:` frame, then disconnect.

    Not `client.stream(...)`: httpx's ASGITransport buffers the whole response
    before returning, so it can never see the first frame of an endless stream and
    simply hangs. Speaking ASGI is the only way to assert this endpoint from here.
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"test")],
        "client": ("test", 1234),
        "server": ("test", 80),
    }
    disconnect = asyncio.Event()
    status = 0
    headers: dict[str, str] = {}
    body = ""

    async def receive() -> dict:
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        nonlocal status, headers, body
        if message["type"] == "http.response.start":
            status = message["status"]
            headers = {k.decode(): v.decode() for k, v in message["headers"]}
        elif message["type"] == "http.response.body":
            body += message.get("body", b"").decode()
            if "data: " in body:
                disconnect.set()

    await asyncio.wait_for(app(scope, receive, send), timeout=10)
    return status, headers, body


async def test_sse_endpoint_is_mounted_and_streams(client):
    """One event per tick, keyed by ticker -- the market module's shape, unaltered."""
    status, headers, body = await _read_sse(client.app)
    assert status == 200
    assert headers["content-type"].startswith("text/event-stream")
    # The reconnect directive the frontend relies on for EventSource auto-retry.
    assert body.startswith("retry: 1000")

    payload = json.loads(next(line[6:] for line in body.splitlines() if line.startswith("data: ")))
    assert "AAPL" in payload
    assert {"price", "session_open", "change_percent_session", "direction"} <= set(payload["AAPL"])


async def test_sse_stream_is_bound_to_the_app_price_cache(client):
    """A stream wired to a different cache would emit an empty payload forever."""
    await client.delete("/api/watchlist/TSLA")
    _, _, body = await _read_sse(client.app)
    payload = json.loads(next(line[6:] for line in body.splitlines() if line.startswith("data: ")))
    assert "TSLA" not in payload


async def test_root_explains_a_missing_frontend_build(client):
    """`uv run uvicorn app.main:app` in a checkout with no build must not crash."""
    response = await client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_static_export_is_served_when_present(tmp_path):
    static = tmp_path / "static"
    (static / "_next").mkdir(parents=True)
    (static / "index.html").write_text("<html>FinAlly</html>")
    (static / "_next" / "app.js").write_text("console.log(1)")

    settings = Settings(
        static_dir=static, db_path=":memory:", massive_api_key="", llm_mock=False
    )
    app = create_app(settings=settings, database=Database(":memory:"))
    try:
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as http:
                assert (await http.get("/")).text == "<html>FinAlly</html>"
                assert (await http.get("/_next/app.js")).status_code == 200
                # Unknown non-API path falls back to the SPA shell...
                deep = await http.get("/some/client/route")
                assert deep.status_code == 200
                assert deep.text == "<html>FinAlly</html>"
                # ...but an unrouted /api path must stay a JSON 404, or the
                # frontend's fetch fails on JSON.parse instead of on a status.
                missing = await http.get("/api/nope")
                assert missing.status_code == 404
                assert missing.json()["error"]["code"] == "NOT_FOUND"
                # API routes still win over the catch-all mount.
                assert (await http.get("/api/health")).json()["static"] is True
    finally:
        set_database(None)


def test_settings_default_static_dir_matches_the_dockerfile_contract(monkeypatch):
    monkeypatch.delenv("FINALLY_STATIC_DIR", raising=False)
    assert str(load_settings().static_dir) == DEFAULT_STATIC_DIR == "/app/static"


def test_settings_read_the_database_path_for_reporting(monkeypatch):
    monkeypatch.setenv("FINALLY_DB_PATH", "/app/db/finally.db")
    assert load_settings().db_path == "/app/db/finally.db"


def test_requested_source_follows_the_api_key(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "  ")
    assert load_settings().requested_source == "simulator"
    monkeypatch.setenv("MASSIVE_API_KEY", "abc123")
    assert load_settings().requested_source == "massive"
