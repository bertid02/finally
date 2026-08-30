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


async def test_health_reports_llm_unconfigured(client):
    """No key and no mock: the state a `docker compose up` demo lands in."""
    body = (await client.get("/api/health")).json()
    assert body["llm_configured"] is False
    assert body["llm_mock"] is False


async def test_health_reports_llm_configured(static_dir):
    settings = Settings(
        static_dir=static_dir,
        db_path=":memory:",
        massive_api_key="",
        openrouter_api_key="sk-or-v1-secret",
    )
    app = create_app(settings=settings, database=Database(":memory:"))
    try:
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as http:
                response = await http.get("/api/health")
    finally:
        set_database(None)

    assert response.json()["llm_configured"] is True
    # The endpoint is unauthenticated and ends up in bug reports and screen
    # shares. Nothing derived from the key may appear: not the key, not a
    # prefix or suffix, not its length.
    raw = response.text
    assert "sk-or-v1-secret" not in raw
    assert "sk-or" not in raw
    assert "secret" not in raw
    assert str(len("sk-or-v1-secret")) not in raw


class TestMissingKeyWarning:
    """The startup warning is the only loud signal a compose user ever gets.

    The start scripts warn about a missing `.env`, compose has no such step, and
    `/api/chat` deliberately answers 200 with "I couldn't reach the AI service
    just now" rather than erroring.
    """

    @staticmethod
    def _warnings(caplog) -> list[str]:
        return [r.message for r in caplog.records if r.levelname == "WARNING"]

    async def test_warns_when_no_key_and_not_mocking(self, app, caplog):
        with caplog.at_level("WARNING", logger="app.main"):
            async with app.router.lifespan_context(app):
                pass
        warning = next(w for w in self._warnings(caplog) if "OPENROUTER_API_KEY" in w)
        assert "chat panel will NOT work" in warning
        # Same shape as the start scripts' message, so the two entry points agree.
        assert "Market data, trading, the portfolio and the charts are unaffected" in warning
        assert ".env" in warning

    async def test_silent_when_a_key_is_present(self, static_dir, caplog):
        settings = Settings(
            static_dir=static_dir,
            db_path=":memory:",
            massive_api_key="",
            openrouter_api_key="sk-or-v1-secret",
        )
        app = create_app(settings=settings, database=Database(":memory:"))
        try:
            with caplog.at_level("WARNING", logger="app.main"):
                async with app.router.lifespan_context(app):
                    pass
        finally:
            set_database(None)
        assert not [w for w in self._warnings(caplog) if "OPENROUTER_API_KEY" in w]
        assert "sk-or-v1-secret" not in caplog.text

    async def test_silent_in_mock_mode(self, static_dir, caplog):
        """Mock mode needs no key. Warning there trains people to ignore warnings."""
        settings = Settings(
            static_dir=static_dir, db_path=":memory:", massive_api_key="", llm_mock=True
        )
        app = create_app(settings=settings, database=Database(":memory:"))
        try:
            with caplog.at_level("WARNING", logger="app.main"):
                async with app.router.lifespan_context(app):
                    pass
        finally:
            set_database(None)
        assert not [w for w in self._warnings(caplog) if "OPENROUTER_API_KEY" in w]
