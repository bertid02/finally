"""The LLM_MOCK mapping. This is a published contract -- see TEAM_LOG.md.

If a test here changes, the TEAM_LOG entry and the E2E fixtures change with it.
"""

from __future__ import annotations

import json

import pytest

from app.llm.mock import (
    DEFAULT_ADD_TICKER,
    DEFAULT_BUY_TICKER,
    DEFAULT_REMOVE_TICKER,
    FAILING_QUANTITY,
    FAILING_TICKER,
    MockUnavailableError,
    mock_completion,
)
from app.llm.schema import parse_response


def _parsed(message: str):
    return parse_response(mock_completion(message))


def test_a_plain_question_gets_prose_and_no_actions():
    parsed = _parsed("How is my portfolio doing?")
    assert parsed.message
    assert parsed.trades == []
    assert parsed.watchlist_changes == []


def test_buy_executes_a_default_single_share():
    parsed = _parsed("buy")
    assert parsed.trades[0].ticker == DEFAULT_BUY_TICKER
    assert parsed.trades[0].side == "buy"
    assert parsed.trades[0].quantity == 1.0


def test_buy_lifts_the_ticker_and_quantity_out_of_the_message():
    parsed = _parsed("Please buy 5 TSLA for me")
    assert (parsed.trades[0].ticker, parsed.trades[0].quantity) == ("TSLA", 5.0)


def test_sell_mirrors_buy():
    parsed = _parsed("sell 2 MSFT")
    assert (parsed.trades[0].side, parsed.trades[0].ticker) == ("sell", "MSFT")
    assert parsed.trades[0].quantity == 2.0


def test_a_fractional_quantity_is_kept():
    assert _parsed("buy 2.5 NVDA").trades[0].quantity == 2.5


def test_command_words_in_caps_are_not_mistaken_for_tickers():
    assert _parsed("BUY 5 TSLA").trades[0].ticker == "TSLA"


def test_yolo_produces_a_trade_that_must_fail_on_cash():
    parsed = _parsed("yolo")
    assert parsed.trades[0].ticker == FAILING_TICKER
    assert parsed.trades[0].quantity == FAILING_QUANTITY


def test_all_in_is_the_same_trigger():
    assert _parsed("I'm going all in").trades[0].quantity == FAILING_QUANTITY


def test_watch_adds_to_the_watchlist():
    parsed = _parsed("add PYPL to my watchlist")
    assert (parsed.watchlist_changes[0].ticker, parsed.watchlist_changes[0].action) == (
        "PYPL",
        "add",
    )


def test_watch_without_a_ticker_uses_the_default():
    assert _parsed("what should I watch?").watchlist_changes[0].ticker == DEFAULT_ADD_TICKER


def test_unwatch_removes_from_the_watchlist():
    parsed = _parsed("unwatch")
    assert parsed.watchlist_changes[0].ticker == DEFAULT_REMOVE_TICKER
    assert parsed.watchlist_changes[0].action == "remove"


def test_remove_is_the_same_trigger_and_takes_a_ticker():
    assert _parsed("remove TSLA").watchlist_changes[0].ticker == "TSLA"


def test_first_rule_wins_so_watchlist_shadows_sell():
    parsed = _parsed("add NVDA to the watchlist and sell AAPL")
    assert parsed.trades == []
    assert parsed.watchlist_changes[0].ticker == "NVDA"


def test_malformed_returns_something_that_is_not_json():
    with pytest.raises(Exception):
        json.loads(mock_completion("give me a malformed reply"))


def test_unavailable_raises():
    with pytest.raises(MockUnavailableError):
        mock_completion("pretend the service is unavailable")


def test_every_response_carries_both_action_keys():
    payload = json.loads(mock_completion("hello"))
    assert set(payload) == {"message", "trades", "watchlist_changes"}
