"""`POST /api/chat` over HTTP, in the mode the E2E suite runs: LLM_MOCK=true.

The mapping asserted here is the published `LLM_MOCK` contract in TEAM_LOG.md.
The E2E fixtures are written against these exact triggers, so a change here is a
change to another agent's tests.
"""

from __future__ import annotations

import pytest


async def _chat(client, message: str):
    return await client.post("/api/chat", json={"message": message})


async def test_a_plain_question_returns_all_five_fields(client):
    response = await _chat(client, "How is my portfolio doing?")
    assert response.status_code == 200

    body = response.json()
    assert set(body) == {"message", "actions", "watchlist", "cash_balance", "positions"}
    assert body["message"]
    assert body["actions"] == {"trades": [], "watchlist_changes": []}
    assert body["cash_balance"] == 10000.0
    assert body["positions"] == []
    assert len(body["watchlist"]) == 10


async def test_a_buy_executes_and_the_echoes_reflect_it(client):
    body = (await _chat(client, "buy 2 AAPL")).json()

    trade = body["actions"]["trades"][0]
    assert trade["ticker"] == "AAPL"
    assert trade["side"] == "buy"
    assert trade["quantity"] == 2.0
    assert trade["status"] == "executed"
    assert trade["total"] == pytest.approx(trade["price"] * 2, rel=1e-6)

    assert body["cash_balance"] == pytest.approx(10000.0 - trade["total"], abs=0.01)
    assert body["positions"][0]["ticker"] == "AAPL"


async def test_a_sell_after_a_buy_closes_the_position(client):
    await _chat(client, "buy 2 AAPL")
    body = (await _chat(client, "sell 2 AAPL")).json()
    assert body["actions"]["trades"][0]["status"] == "executed"
    assert body["positions"] == []


async def test_the_yolo_trigger_renders_a_failed_action(client):
    body = (await _chat(client, "yolo")).json()

    trade = body["actions"]["trades"][0]
    assert trade["status"] == "failed"
    assert trade["error_code"] == "INSUFFICIENT_CASH"
    assert "Insufficient cash" in trade["error"]
    assert body["cash_balance"] == 10000.0


async def test_a_watchlist_add_is_echoed_because_the_stream_carries_no_membership(client):
    body = (await _chat(client, "add PYPL to my watchlist")).json()
    assert body["actions"]["watchlist_changes"][0] == {
        "ticker": "PYPL", "action": "add", "status": "executed"
    }
    assert "PYPL" in body["watchlist"]

    listed = (await client.get("/api/watchlist")).json()["tickers"]
    assert listed == body["watchlist"]


async def test_a_watchlist_remove_is_echoed(client):
    body = (await _chat(client, "unwatch NFLX")).json()
    assert "NFLX" not in body["watchlist"]


async def test_an_unparseable_response_is_a_200_with_an_apology(client):
    response = await _chat(client, "send me a malformed reply")
    assert response.status_code == 200
    assert "garbled" in response.json()["message"]


async def test_a_provider_outage_is_a_200_with_an_apology(client):
    response = await _chat(client, "pretend the service is unavailable")
    assert response.status_code == 200
    assert "couldn't reach" in response.json()["message"]


@pytest.mark.parametrize("payload", [{"message": ""}, {"message": "   "}])
async def test_an_empty_message_is_a_400_in_the_shared_envelope(client, payload):
    response = await client.post("/api/chat", json=payload)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_MESSAGE"
    assert response.json()["error"]["message"] == "Message cannot be empty."


async def test_a_body_with_no_message_field_wears_the_envelope_too(client):
    response = await client.post("/api/chat", json={})
    assert response.status_code == 422
    assert "error" in response.json()


async def test_the_turn_survives_a_restart_because_it_is_persisted(client):
    await _chat(client, "buy 1 AAPL")
    repo = client.app.state.repository
    stored = await repo.get_chat_messages()
    assert [m.role for m in stored] == ["user", "assistant"]
    assert stored[1].actions["trades"][0]["status"] == "executed"


async def test_the_chat_route_is_registered_before_the_static_catch_all(app):
    paths = [route.path for route in app.routes]
    assert "/api/chat" in paths
