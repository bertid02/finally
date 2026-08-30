"""GET /api/portfolio, POST /api/portfolio/trade, GET /api/portfolio/history."""

from __future__ import annotations

import pytest


async def test_fresh_portfolio_is_ten_thousand_in_cash(client):
    body = (await client.get("/api/portfolio")).json()
    assert body == {"cash_balance": 10000.0, "positions": []}


async def test_portfolio_carries_no_server_valuation(client):
    """PLAN.md section 8/10: the client is authoritative for displayed numbers."""
    body = (await client.get("/api/portfolio")).json()
    assert set(body) == {"cash_balance", "positions"}


async def test_buy_fills_at_the_cached_price(client):
    price = client.app.state.price_cache.get_price("AAPL")
    response = await client.post(
        "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 10, "side": "buy"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["trade"]["price"] == pytest.approx(price, abs=0.5)
    assert body["trade"]["ticker"] == "AAPL"
    assert body["trade"]["side"] == "buy"
    assert body["trade"]["total"] == pytest.approx(body["trade"]["price"] * 10, abs=0.01)
    assert body["position"] == {
        "ticker": "AAPL",
        "quantity": 10,
        "avg_cost": body["trade"]["price"],
    }
    assert body["cash_balance"] == pytest.approx(10000.0 - body["trade"]["total"], abs=0.01)


async def test_trade_response_has_exactly_the_contracted_keys(client):
    body = (
        await client.post(
            "/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "buy"}
        )
    ).json()
    assert set(body) == {"trade", "cash_balance", "position"}
    assert set(body["trade"]) == {
        "id",
        "ticker",
        "side",
        "quantity",
        "price",
        "total",
        "executed_at",
    }


async def test_client_cannot_supply_the_fill_price(client):
    """An extra `price` field is ignored -- section 8 fills at the cached price only."""
    body = (
        await client.post(
            "/api/portfolio/trade",
            json={"ticker": "AAPL", "quantity": 1, "side": "buy", "price": 0.01},
        )
    ).json()
    assert body["trade"]["price"] > 1.0


async def test_lowercase_ticker_is_normalized_not_rejected(client):
    response = await client.post(
        "/api/portfolio/trade", json={"ticker": " aapl ", "quantity": 1, "side": "buy"}
    )
    assert response.status_code == 200
    assert response.json()["trade"]["ticker"] == "AAPL"


async def test_selling_to_zero_closes_the_position(client):
    await client.post(
        "/api/portfolio/trade", json={"ticker": "MSFT", "quantity": 5, "side": "buy"}
    )
    body = (
        await client.post(
            "/api/portfolio/trade", json={"ticker": "MSFT", "quantity": 5, "side": "sell"}
        )
    ).json()
    assert body["position"] is None
    assert (await client.get("/api/portfolio")).json()["positions"] == []


async def test_history_is_empty_before_any_trade(client):
    assert (await client.get("/api/portfolio/history")).json() == {"snapshots": []}


async def test_history_records_a_point_per_trade(client):
    for _ in range(3):
        await client.post(
            "/api/portfolio/trade", json={"ticker": "NVDA", "quantity": 1, "side": "buy"}
        )
    snapshots = (await client.get("/api/portfolio/history")).json()["snapshots"]
    assert len(snapshots) == 3
    assert {"total_value", "recorded_at"} <= set(snapshots[0])
    assert snapshots == sorted(snapshots, key=lambda s: s["recorded_at"])


async def test_history_limit_is_clamped_not_rejected(client):
    """An out-of-range limit is a harmless query string, not an error the chart handles."""
    assert (await client.get("/api/portfolio/history?limit=999999")).status_code == 200
    assert (await client.get("/api/portfolio/history?limit=0")).status_code == 200


async def test_history_since_filters(client):
    await client.post(
        "/api/portfolio/trade", json={"ticker": "META", "quantity": 1, "side": "buy"}
    )
    all_points = (await client.get("/api/portfolio/history")).json()["snapshots"]
    future = "2099-01-01T00:00:00Z"
    assert all_points
    assert (await client.get(f"/api/portfolio/history?since={future}")).json()["snapshots"] == []
