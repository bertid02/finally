"""GET /api/health -- and the startup fallback it is there to make visible."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.db import Database, set_database
from app.main import create_app


async def test_health_reports_ok(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["market_source"] == "simulator"
    assert body["requested_source"] == "simulator"
    assert body["fallback"] is False


async def test_health_reports_streaming_tickers(client):
    """start() warms the cache before returning, so the count is the seeded watchlist."""
    body = (await client.get("/api/health")).json()
    assert body["tickers"] == 10


async def test_health_flags_a_silent_fallback(monkeypatch, static_dir):
    """A Massive key whose plan lacks snapshots must land on the simulator, and say so."""
    from app.market.simulator import SimulatorDataSource

    class Unusable(SimulatorDataSource):
        @property
        def name(self) -> str:
            return "massive"

        async def start(self, tickers):
            raise RuntimeError("snapshot endpoint not in plan")

    monkeypatch.setattr("app.main.create_market_data_source", lambda cache: Unusable(cache))
    settings = Settings(
        static_dir=static_dir, db_path=":memory:", massive_api_key="key", llm_mock=False
    )
    app = create_app(settings=settings, database=Database(":memory:"))
    try:
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as http:
                body = (await http.get("/api/health")).json()
        assert body["market_source"] == "simulator"
        assert body["requested_source"] == "massive"
        assert body["fallback"] is True
    finally:
        set_database(None)


@pytest.mark.parametrize("value,expected", [("true", True), ("0", False)])
async def test_health_echoes_llm_mock(monkeypatch, static_dir, value, expected):
    from app.config import load_settings

    monkeypatch.setenv("LLM_MOCK", value)
    monkeypatch.setenv("FINALLY_STATIC_DIR", str(static_dir))
    assert load_settings().llm_mock is expected
