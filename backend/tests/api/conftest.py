"""Fixtures for the HTTP layer.

Every test gets its own app, its own in-memory database and its own price cache,
so nothing leaks between tests -- including the process-wide `set_database`
singleton, which is reset on teardown.

The lifespan is entered explicitly. `httpx.ASGITransport` does not run startup
events, and without them there is no market data source on `app.state`, so every
route depending on one would fail with an AttributeError rather than exercising
what it is meant to test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.db import Database, set_database
from app.main import create_app


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Force the simulator and a known LLM_MOCK state regardless of the developer's .env."""
    monkeypatch.setenv("MASSIVE_API_KEY", "")
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.delenv("FINALLY_DB_PATH", raising=False)


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    """A directory that deliberately does not exist -- the local-development case."""
    return tmp_path / "static"


@pytest.fixture
def settings(static_dir: Path) -> Settings:
    return Settings(
        static_dir=static_dir,
        db_path=":memory:",
        massive_api_key="",
        llm_mock=False,
    )


@pytest.fixture
def app(settings: Settings):
    """An unstarted app. Use `client` unless you need to inspect it before startup."""
    application = create_app(settings=settings, database=Database(":memory:"))
    yield application
    set_database(None)


@pytest.fixture
async def client(app) -> AsyncClient:
    """A client with the lifespan running: database seeded, simulator streaming."""
    async with app.router.lifespan_context(app):
        # raise_app_exceptions=False so the registered 500 handler's envelope is
        # what a test observes, exactly as a browser would. A genuine bug still
        # fails loudly -- as a 500 where the test expected a 200.
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            http.app = app  # tests reach app.state through this
            yield http
