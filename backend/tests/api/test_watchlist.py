"""Watchlist endpoints -- membership only, and always the complete new list.

The "returns the full list" assertions look redundant until you remember the SSE
stream carries no membership event: these return values are the frontend's only
refresh signal, so an endpoint that returned 204 would leave the grid stale.
"""

from __future__ import annotations

from app.db import DEFAULT_WATCHLIST


async def test_seeded_watchlist(client):
    body = (await client.get("/api/watchlist")).json()
    assert body == {"tickers": list(DEFAULT_WATCHLIST)}


async def test_add_returns_the_full_new_list(client):
    body = (await client.post("/api/watchlist", json={"ticker": "PYPL"})).json()
    assert set(body) == {"tickers"}
    assert body["tickers"] == list(DEFAULT_WATCHLIST) + ["PYPL"]


async def test_add_normalizes_the_symbol(client):
    body = (await client.post("/api/watchlist", json={"ticker": " pypl "})).json()
    assert "PYPL" in body["tickers"]


async def test_add_is_idempotent(client):
    first = (await client.post("/api/watchlist", json={"ticker": "PYPL"})).json()
    response = await client.post("/api/watchlist", json={"ticker": "PYPL"})
    assert response.status_code == 200
    assert response.json() == first


async def test_add_starts_the_source_tracking_it(client):
    await client.post("/api/watchlist", json={"ticker": "PYPL"})
    assert "PYPL" in client.app.state.market_source.get_tickers()
    assert client.app.state.price_cache.get_price("PYPL") is not None


async def test_remove_returns_the_full_new_list(client):
    body = (await client.delete("/api/watchlist/TSLA")).json()
    assert "TSLA" not in body["tickers"]
    assert len(body["tickers"]) == len(DEFAULT_WATCHLIST) - 1


async def test_remove_evicts_from_the_price_cache(client):
    """The repository never touches the cache, so the route must."""
    assert client.app.state.price_cache.get_price("TSLA") is not None
    await client.delete("/api/watchlist/TSLA")
    assert client.app.state.price_cache.get_price("TSLA") is None
    assert "TSLA" not in client.app.state.market_source.get_tickers()


async def test_remove_is_idempotent(client):
    first = (await client.delete("/api/watchlist/TSLA")).json()
    response = await client.delete("/api/watchlist/TSLA")
    assert response.status_code == 200
    assert response.json() == first


async def test_remove_normalizes_the_symbol(client):
    body = (await client.delete("/api/watchlist/tsla")).json()
    assert "TSLA" not in body["tickers"]


async def test_a_re_added_ticker_streams_again(client):
    await client.delete("/api/watchlist/TSLA")
    body = (await client.post("/api/watchlist", json={"ticker": "TSLA"})).json()
    assert body["tickers"][-1] == "TSLA"
    assert client.app.state.price_cache.get_price("TSLA") is not None
