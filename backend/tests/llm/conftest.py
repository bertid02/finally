"""Fixtures for the chat layer.

Nothing here ever reaches the network. The real provider call is exercised only
through a patched `_load_completion`, and every end-to-end fixture runs with
`llm_mock=True` -- the same switch the E2E suite uses.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.db import Database, Repository, set_database
from app.main import create_app
from app.market import PriceCache


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """A developer's real .env must not decide what these tests exercise."""
    monkeypatch.setenv("MASSIVE_API_KEY", "")
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.delenv("FINALLY_DB_PATH", raising=False)


@pytest.fixture
async def repo() -> Repository:
    """A seeded repository on its own in-memory database."""
    db = Database(":memory:")
    set_database(db)
    repository = Repository(db=db)
    await repository.initialize()
    yield repository
    db.close()
    set_database(None)


@pytest.fixture
def cache() -> PriceCache:
    """A price cache carrying the default watchlist at round numbers.

    Round prices keep the assertions readable: 10 AAPL costs exactly $2,000.
    """
    price_cache = PriceCache()
    for ticker, price in (
        ("AAPL", 200.0),
        ("GOOGL", 175.0),
        ("MSFT", 400.0),
        ("NVDA", 120.0),
        ("TSLA", 250.0),
    ):
        price_cache.update(ticker, price)
    return price_cache


class FakeSource:
    """A market source that supports everything except the symbols told to reject.

    The real sources are already covered in `tests/market/`; what the chat turn
    needs is control over `supports_ticker` and a record of what it was asked to
    track.
    """

    name = "fake"

    def __init__(self, unsupported: set[str] | None = None) -> None:
        self.unsupported = unsupported or set()
        self.added: list[str] = []
        self.removed: list[str] = []

    async def supports_ticker(self, ticker: str) -> bool:
        return ticker not in self.unsupported

    async def add_ticker(self, ticker: str) -> None:
        self.added.append(ticker)

    async def remove_ticker(self, ticker: str) -> None:
        self.removed.append(ticker)


@pytest.fixture
def source() -> FakeSource:
    return FakeSource()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Mock mode on, no static build -- the local-development shape."""
    return Settings(
        static_dir=tmp_path / "static",
        db_path=":memory:",
        massive_api_key="",
        llm_mock=True,
    )


@pytest.fixture
def app(settings: Settings):
    application = create_app(settings=settings, database=Database(":memory:"))
    yield application
    set_database(None)


@pytest.fixture
async def client(app) -> AsyncClient:
    """A client with the lifespan running: seeded database, simulator streaming."""
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            http.app = app
            yield http
