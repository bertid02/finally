"""One chat turn: auto-execution, failure recording, and the degradation paths.

These drive `run_chat_turn` directly with a stubbed provider, so each response
shape can be pinned exactly. `tests/llm/test_chat_api.py` covers the same turn
over HTTP in mock mode.
"""

from __future__ import annotations

import json

import pytest

from app.llm import service as service_module
from app.llm.client import LLMUnavailableError
from app.llm.service import (
    UNAVAILABLE_MESSAGE,
    UNPARSEABLE_MESSAGE,
    EmptyMessageError,
    run_chat_turn,
)

from .conftest import FakeSource


@pytest.fixture
def reply(monkeypatch):
    """Make the provider return whatever a test hands to `reply.set(...)`."""

    class _Reply:
        raw = '{"message": "ok"}'
        messages: list[dict] = []

        def set(self, payload):
            self.raw = payload if isinstance(payload, str) else json.dumps(payload)

    holder = _Reply()

    async def _fake_complete(messages):
        holder.messages = messages
        if isinstance(holder.raw, Exception):
            raise holder.raw
        return holder.raw

    monkeypatch.setattr(service_module, "complete", _fake_complete)
    return holder


async def _turn(message, repo, cache, source, llm_mock=False):
    return await run_chat_turn(
        message=message, repo=repo, cache=cache, source=source, llm_mock=llm_mock
    )


# --- the envelope ------------------------------------------------------------


@pytest.mark.parametrize("message", ["", "   ", None])
async def test_an_empty_message_is_the_only_rejection(message, repo, cache, source):
    with pytest.raises(EmptyMessageError) as exc:
        await _turn(message, repo, cache, source)
    assert exc.value.code == "INVALID_MESSAGE"
    assert exc.value.http_status == 400


async def test_the_response_carries_all_five_fields(reply, repo, cache, source):
    body = await _turn("hello", repo, cache, source)
    assert set(body) == {"message", "actions", "watchlist", "cash_balance", "positions"}
    assert body["actions"] == {"trades": [], "watchlist_changes": []}
    assert body["cash_balance"] == 10000.0
    assert "AAPL" in body["watchlist"]


async def test_both_turns_are_persisted(reply, repo, cache, source):
    await _turn("hello", repo, cache, source)
    stored = await repo.get_chat_messages()
    assert [(m.role, m.content) for m in stored] == [
        ("user", "hello"),
        ("assistant", "ok"),
    ]
    assert stored[1].actions is None  # nothing happened, so nothing recorded


async def test_the_transcript_reaches_the_model_with_the_new_message_last(
    reply, repo, cache, source
):
    await _turn("first", repo, cache, source)
    await _turn("second", repo, cache, source)
    assert reply.messages[0]["role"] == "system"
    assert reply.messages[-1] == {"role": "user", "content": "second"}
    assert {"role": "user", "content": "first"} in reply.messages


async def test_the_prompt_carries_live_prices(reply, repo, cache, source):
    await _turn("hello", repo, cache, source)
    assert '"price": 200.0' in reply.messages[0]["content"]


# --- trades ------------------------------------------------------------------


async def test_a_requested_trade_executes_and_is_echoed(reply, repo, cache, source):
    reply.set({"message": "Buying.",
               "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 10}]})
    body = await _turn("buy 10 AAPL", repo, cache, source)

    trade = body["actions"]["trades"][0]
    assert trade == {"ticker": "AAPL", "side": "buy", "quantity": 10.0,
                     "status": "executed", "price": 200.0, "total": 2000.0}
    assert body["cash_balance"] == 8000.0
    assert body["positions"] == [{"ticker": "AAPL", "quantity": 10.0, "avg_cost": 200.0}]


async def test_the_fill_uses_the_cached_price_not_anything_the_model_said(
    reply, repo, cache, source
):
    reply.set({"message": "Buying.",
               "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 1,
                           "price": 1.0}]})
    body = await _turn("buy", repo, cache, source)
    assert body["actions"]["trades"][0]["price"] == 200.0


async def test_a_lowercase_ticker_is_normalized_before_the_cache_lookup(
    reply, repo, cache, source
):
    reply.set({"message": "Buying.",
               "trades": [{"ticker": " aapl ", "side": "buy", "quantity": 1}]})
    assert (await _turn("buy", repo, cache, source))["actions"]["trades"][0]["ticker"] == "AAPL"


async def test_an_unaffordable_trade_is_recorded_as_failed_not_raised(
    reply, repo, cache, source
):
    reply.set({"message": "Buying.",
               "trades": [{"ticker": "NVDA", "side": "buy", "quantity": 100000}]})
    body = await _turn("yolo", repo, cache, source)

    trade = body["actions"]["trades"][0]
    assert trade["status"] == "failed"
    assert trade["error_code"] == "INSUFFICIENT_CASH"
    assert "Insufficient cash" in trade["error"]
    assert body["cash_balance"] == 10000.0  # nothing moved


async def test_a_failed_trade_keeps_the_error_message_the_http_path_would_give(
    reply, repo, cache, source
):
    from app.db import InsufficientCashError

    reply.set({"message": "Buying.",
               "trades": [{"ticker": "NVDA", "side": "buy", "quantity": 100000}]})
    body = await _turn("yolo", repo, cache, source)
    try:
        await repo.execute_trade(ticker="NVDA", side="buy", quantity=100000, price=120.0)
    except InsufficientCashError as exc:
        assert body["actions"]["trades"][0]["error"] == exc.message


@pytest.mark.parametrize(
    ("spec", "code"),
    [
        ({"ticker": "BANANA", "side": "buy", "quantity": 1}, "INVALID_TICKER"),
        ({"ticker": "AAPL", "side": "hold", "quantity": 1}, "INVALID_SIDE"),
        ({"ticker": "AAPL", "side": "buy", "quantity": 0}, "INVALID_QUANTITY"),
        ({"ticker": "AAPL", "side": "buy", "quantity": "ten"}, "INVALID_QUANTITY"),
        ({"ticker": "ZZZ", "side": "buy", "quantity": 1}, "UNKNOWN_TICKER"),
        ({"ticker": "AAPL", "side": "sell", "quantity": 5}, "INSUFFICIENT_SHARES"),
    ],
)
async def test_every_trade_rejection_reuses_the_section_8_vocabulary(
    spec, code, reply, repo, cache, source
):
    reply.set({"message": "Trying.", "trades": [spec]})
    body = await _turn("do it", repo, cache, source)
    assert body["actions"]["trades"][0]["error_code"] == code


async def test_a_failed_trade_is_persisted_so_the_next_turn_sees_it(
    reply, repo, cache, source
):
    reply.set({"message": "Buying.",
               "trades": [{"ticker": "NVDA", "side": "buy", "quantity": 100000}]})
    await _turn("yolo", repo, cache, source)

    stored = (await repo.get_chat_messages())[-1]
    assert stored.actions["trades"][0]["status"] == "failed"

    reply.set({"message": "Understood."})
    await _turn("what happened?", repo, cache, source)
    assert "FAILED buy 100000.0 NVDA (INSUFFICIENT_CASH)" in reply.messages[-2]["content"]


async def test_several_trades_run_in_order(reply, repo, cache, source):
    reply.set(
        {"message": "Building a position.",
         "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 10},
                    {"ticker": "MSFT", "side": "buy", "quantity": 1},
                    {"ticker": "TSLA", "side": "buy", "quantity": 1000}]}
    )
    body = await _turn("go", repo, cache, source)
    statuses = [t["status"] for t in body["actions"]["trades"]]
    assert statuses == ["executed", "executed", "failed"]
    assert [p["ticker"] for p in body["positions"]] == ["AAPL", "MSFT"]


# --- watchlist ---------------------------------------------------------------


async def test_an_add_inserts_and_starts_tracking(reply, repo, cache, source):
    reply.set({"message": "Watching it.",
               "watchlist_changes": [{"ticker": "pypl", "action": "add"}]})
    body = await _turn("watch PYPL", repo, cache, source)

    assert body["actions"]["watchlist_changes"][0] == {
        "ticker": "PYPL", "action": "add", "status": "executed"
    }
    assert "PYPL" in body["watchlist"]
    assert source.added == ["PYPL"]


async def test_adding_a_ticker_already_present_is_idempotent(reply, repo, cache, source):
    reply.set({"message": "Already there.",
               "watchlist_changes": [{"ticker": "AAPL", "action": "add"}]})
    body = await _turn("watch AAPL", repo, cache, source)
    assert body["actions"]["watchlist_changes"][0]["status"] == "executed"
    assert body["watchlist"].count("AAPL") == 1
    assert source.added == []  # no second subscription


async def test_a_remove_deletes_and_evicts_from_the_cache(reply, repo, cache, source):
    reply.set({"message": "Dropped.",
               "watchlist_changes": [{"ticker": "TSLA", "action": "remove"}]})
    body = await _turn("unwatch TSLA", repo, cache, source)
    assert "TSLA" not in body["watchlist"]
    assert source.removed == ["TSLA"]


async def test_removing_something_absent_is_idempotent(reply, repo, cache, source):
    reply.set({"message": "Dropped.",
               "watchlist_changes": [{"ticker": "PYPL", "action": "remove"}]})
    body = await _turn("unwatch PYPL", repo, cache, source)
    assert body["actions"]["watchlist_changes"][0]["status"] == "executed"


async def test_an_unsupported_ticker_fails_with_422s_code(reply, repo, cache):
    picky = FakeSource(unsupported={"APPL"})
    reply.set({"message": "Adding.",
               "watchlist_changes": [{"ticker": "APPL", "action": "add"}]})
    body = await _turn("watch APPL", repo, cache, picky)

    change = body["actions"]["watchlist_changes"][0]
    assert change["status"] == "failed"
    assert change["error_code"] == "UNSUPPORTED_TICKER"
    assert "APPL" not in body["watchlist"]


async def test_a_malformed_symbol_fails_with_invalid_ticker(reply, repo, cache, source):
    reply.set({"message": "Adding.",
               "watchlist_changes": [{"ticker": "BANANA", "action": "add"}]})
    change = (await _turn("watch BANANA", repo, cache, source))["actions"]["watchlist_changes"][0]
    assert change["error_code"] == "INVALID_TICKER"


async def test_a_full_watchlist_rejects_the_add(reply, repo, cache, source):
    from itertools import product

    from app.db import MAX_WATCHLIST_SIZE

    filler = ["".join(p) for p in product("ABCDEF", repeat=2)]
    for ticker in filler[: MAX_WATCHLIST_SIZE - len(await repo.get_watchlist())]:
        await repo.add_to_watchlist(ticker)
    assert len(await repo.get_watchlist()) >= MAX_WATCHLIST_SIZE

    reply.set({"message": "Adding.",
               "watchlist_changes": [{"ticker": "PYPL", "action": "add"}]})
    change = (await _turn("watch PYPL", repo, cache, source))["actions"]["watchlist_changes"][0]
    assert change["error_code"] == "WATCHLIST_FULL"


async def test_an_action_that_is_neither_add_nor_remove_fails_cleanly(
    reply, repo, cache, source
):
    reply.set({"message": "Hmm.",
               "watchlist_changes": [{"ticker": "AAPL", "action": "star"}]})
    change = (await _turn("star it", repo, cache, source))["actions"]["watchlist_changes"][0]
    assert change == {"ticker": "AAPL", "action": "star", "status": "failed",
                      "error_code": "INVALID_ACTION",
                      "error": "'star' is not a watchlist action. Expected 'add' or 'remove'."}


async def test_a_missing_action_reports_the_ticker_it_was_given(reply, repo, cache, source):
    reply.set({"message": "Hmm.", "watchlist_changes": [{"ticker": "AAPL"}]})
    change = (await _turn("do", repo, cache, source))["actions"]["watchlist_changes"][0]
    assert change["error_code"] == "INVALID_ACTION"
    assert change["action"] == ""


async def test_trades_and_watchlist_changes_run_in_the_same_turn(
    reply, repo, cache, source
):
    reply.set({"message": "Both.",
               "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 1}],
               "watchlist_changes": [{"ticker": "PYPL", "action": "add"}]})
    body = await _turn("both", repo, cache, source)
    assert body["actions"]["trades"][0]["status"] == "executed"
    assert body["actions"]["watchlist_changes"][0]["status"] == "executed"
    assert body["cash_balance"] == 9800.0
    assert "PYPL" in body["watchlist"]


# --- degradation -------------------------------------------------------------


async def test_a_provider_outage_apologises_in_prose(reply, repo, cache, source):
    reply.raw = LLMUnavailableError("connection reset")
    body = await _turn("hello", repo, cache, source)
    assert body["message"] == UNAVAILABLE_MESSAGE
    assert body["actions"] == {"trades": [], "watchlist_changes": []}
    assert body["cash_balance"] == 10000.0


async def test_unparseable_output_apologises_in_prose(reply, repo, cache, source):
    reply.set("Sure! Here is your analysis.")
    body = await _turn("hello", repo, cache, source)
    assert body["message"] == UNPARSEABLE_MESSAGE


async def test_a_degraded_turn_is_still_persisted(reply, repo, cache, source):
    reply.set("not json")
    await _turn("hello", repo, cache, source)
    stored = await repo.get_chat_messages()
    assert stored[-1].content == UNPARSEABLE_MESSAGE


async def test_a_missing_message_still_executes_the_trades(reply, repo, cache, source):
    reply.set({"trades": [{"ticker": "AAPL", "side": "buy", "quantity": 1}]})
    body = await _turn("buy one", repo, cache, source)
    assert body["message"] == "Done."
    assert body["actions"]["trades"][0]["status"] == "executed"


async def test_mock_mode_bypasses_the_provider(repo, cache, source, monkeypatch):
    async def _explode(messages):
        raise AssertionError("the provider must not be called in mock mode")

    monkeypatch.setattr(service_module, "complete", _explode)
    body = await run_chat_turn(
        message="buy 2 AAPL", repo=repo, cache=cache, source=source, llm_mock=True
    )
    assert body["actions"]["trades"][0] == {
        "ticker": "AAPL", "side": "buy", "quantity": 2.0,
        "status": "executed", "price": 200.0, "total": 400.0,
    }


async def test_the_mocks_outage_trigger_degrades_like_a_real_one(repo, cache, source):
    body = await run_chat_turn(
        message="pretend the service is unavailable", repo=repo, cache=cache,
        source=source, llm_mock=True,
    )
    assert body["message"] == UNAVAILABLE_MESSAGE


async def test_the_mocks_malformed_trigger_degrades_like_real_bad_json(repo, cache, source):
    body = await run_chat_turn(
        message="give me a malformed reply", repo=repo, cache=cache,
        source=source, llm_mock=True,
    )
    assert body["message"] == UNPARSEABLE_MESSAGE
