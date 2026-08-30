"""Every one of the eight PLAN.md section 8 error codes, reachable over HTTP.

The point of this module is not that each code exists in a constant somewhere --
it is that a real request produces it, at the documented status, wearing the
documented envelope. Seven come from `app/db`; UNSUPPORTED_TICKER is the API
layer's own.
"""

from __future__ import annotations

import pytest

from app.db import MAX_WATCHLIST_SIZE


def assert_envelope(response, code: str, status: int) -> str:
    """Assert the section 8 body and return the user-facing message."""
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    return body["error"]["message"]


@pytest.mark.parametrize("quantity", [0, -5])
async def test_invalid_quantity(client, quantity):
    response = await client.post(
        "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": quantity, "side": "buy"}
    )
    assert_envelope(response, "INVALID_QUANTITY", 400)


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
async def test_invalid_quantity_from_a_non_finite_literal(client, literal):
    """Sent as raw text: httpx's JSON encoder refuses these, but a browser can send
    them and Python's json.loads accepts them, so the route must refuse them itself."""
    response = await client.post(
        "/api/portfolio/trade",
        content=f'{{"ticker": "AAPL", "quantity": {literal}, "side": "buy"}}',
        headers={"content-type": "application/json"},
    )
    assert_envelope(response, "INVALID_QUANTITY", 400)


async def test_invalid_quantity_from_a_non_numeric_body(client):
    """Pydantic's own failure maps onto the same code, not a bare `detail` list."""
    response = await client.post(
        "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": "ten", "side": "buy"}
    )
    assert_envelope(response, "INVALID_QUANTITY", 400)


async def test_invalid_side(client):
    response = await client.post(
        "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "hold"}
    )
    assert_envelope(response, "INVALID_SIDE", 400)


@pytest.mark.parametrize("ticker", ["TOOLONG", "12345", "", "AA PL"])
async def test_invalid_ticker_on_trade(client, ticker):
    response = await client.post(
        "/api/portfolio/trade", json={"ticker": ticker, "quantity": 1, "side": "buy"}
    )
    assert_envelope(response, "INVALID_TICKER", 400)


async def test_invalid_ticker_on_watchlist_add(client):
    assert_envelope(
        await client.post("/api/watchlist", json={"ticker": "BANANA"}),
        "INVALID_TICKER",
        400,
    )


async def test_invalid_ticker_on_watchlist_delete(client):
    assert_envelope(await client.delete("/api/watchlist/TOOLONG"), "INVALID_TICKER", 400)


async def test_unknown_ticker_when_the_cache_has_no_price(client):
    """Well-formed, but never tracked -- so there is no price to fill against."""
    response = await client.post(
        "/api/portfolio/trade", json={"ticker": "ZZZZ", "quantity": 1, "side": "buy"}
    )
    assert_envelope(response, "UNKNOWN_TICKER", 404)


async def test_unsupported_ticker(client, monkeypatch):
    """The one code the database layer never raises -- it comes from supports_ticker()."""

    async def unsupported(ticker: str) -> bool:
        return False

    monkeypatch.setattr(client.app.state.market_source, "supports_ticker", unsupported)
    message = assert_envelope(
        await client.post("/api/watchlist", json={"ticker": "PYPL"}), "UNSUPPORTED_TICKER", 422
    )
    assert "PYPL" in message
    assert "PYPL" not in (await client.get("/api/watchlist")).json()["tickers"]


async def test_insufficient_cash(client):
    message = assert_envelope(
        await client.post(
            "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 10_000, "side": "buy"}
        ),
        "INSUFFICIENT_CASH",
        409,
    )
    assert "$" in message  # user-facing prose, not a log line
    assert (await client.get("/api/portfolio")).json()["cash_balance"] == 10000.0


async def test_insufficient_shares(client):
    assert_envelope(
        await client.post(
            "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "sell"}
        ),
        "INSUFFICIENT_SHARES",
        409,
    )


async def test_watchlist_full(client):
    """Fill to the ceiling, then prove the 31st is refused and the list is untouched."""
    filler = [
        f"{a}{b}"
        for a in "XYZ"
        for b in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    ]
    added = (await client.get("/api/watchlist")).json()["tickers"]
    for symbol in filler:
        if len(added) >= MAX_WATCHLIST_SIZE:
            break
        added = (await client.post("/api/watchlist", json={"ticker": symbol})).json()["tickers"]
    assert len(added) == MAX_WATCHLIST_SIZE

    assert_envelope(
        await client.post("/api/watchlist", json={"ticker": "PYPL"}), "WATCHLIST_FULL", 409
    )
    assert len((await client.get("/api/watchlist")).json()["tickers"]) == MAX_WATCHLIST_SIZE


async def test_full_is_checked_before_the_support_lookup(client, monkeypatch):
    """A full list fails fast rather than paying a round trip to say no anyway."""
    calls = []

    async def counting(ticker: str) -> bool:
        calls.append(ticker)
        return True

    added = (await client.get("/api/watchlist")).json()["tickers"]
    for symbol in (f"Q{c}" for c in "ABCDEFGHIJKLMNOPQRST"):
        if len(added) >= MAX_WATCHLIST_SIZE:
            break
        added = (await client.post("/api/watchlist", json={"ticker": symbol})).json()["tickers"]

    monkeypatch.setattr(client.app.state.market_source, "supports_ticker", counting)
    assert_envelope(
        await client.post("/api/watchlist", json={"ticker": "PYPL"}), "WATCHLIST_FULL", 409
    )
    assert calls == []


async def test_unknown_api_route_still_wears_the_envelope(client):
    assert_envelope(await client.get("/api/nope"), "NOT_FOUND", 404)


async def test_wrong_method_wears_the_envelope(client):
    assert_envelope(await client.put("/api/portfolio"), "HTTP_ERROR", 405)


async def test_malformed_body_wears_the_envelope(client):
    response = await client.post("/api/portfolio/trade", json={"ticker": "AAPL"})
    assert_envelope(response, "INVALID_QUANTITY", 400)


async def test_missing_ticker_field_maps_to_invalid_ticker(client):
    response = await client.post("/api/watchlist", json={"nonsense": 1})
    assert_envelope(response, "INVALID_TICKER", 400)


async def test_unhandled_exception_becomes_a_500_envelope(client, monkeypatch):
    """Anything that is not a DatabaseError or APIError is a bug, reported honestly."""

    async def boom(*args, **kwargs):
        raise ValueError("kaboom")

    monkeypatch.setattr(client.app.state.repository, "get_portfolio", boom)
    response = await client.get("/api/portfolio")
    assert_envelope(response, "INTERNAL_ERROR", 500)
