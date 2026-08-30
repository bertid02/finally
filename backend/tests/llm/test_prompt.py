"""What the model sees: live context, and history that carries failures forward."""

from __future__ import annotations

import json

from app.api.valuation import value_portfolio
from app.db import ChatMessage, Portfolio, Position
from app.llm.prompt import SYSTEM_PROMPT, build_context, build_messages, render_history
from app.market import PriceCache


def _valuation(cache: PriceCache):
    portfolio = Portfolio(
        cash_balance=8000.0,
        positions=[Position(ticker="AAPL", quantity=10.0, avg_cost=190.0)],
    )
    return value_portfolio(portfolio, {t: u.price for t, u in cache.get_all().items()})


def _message(role: str, content: str, actions: dict | None = None) -> ChatMessage:
    return ChatMessage(id="x", role=role, content=content, actions=actions,
                       created_at="2026-08-30T00:00:00Z")


def test_context_carries_cash_totals_and_realized_pnl(cache):
    context = build_context(_valuation(cache), 123.456, ["AAPL"], cache)
    assert context["cash_balance"] == 8000.0
    assert context["total_value"] == 10000.0  # 8000 cash + 10 x 200
    assert context["realized_pnl"] == 123.46
    assert context["total_unrealized_pnl"] == 100.0


def test_context_positions_carry_price_pnl_and_weight(cache):
    position = build_context(_valuation(cache), 0.0, [], cache)["positions"][0]
    assert position["ticker"] == "AAPL"
    assert position["price"] == 200.0
    assert position["weight_percent"] == 20.0
    assert position["priced"] is True


def test_an_unpriced_position_is_flagged_rather_than_zeroed():
    portfolio = Portfolio(
        cash_balance=0.0,
        positions=[Position(ticker="ZZ", quantity=2.0, avg_cost=50.0)],
    )
    context = build_context(value_portfolio(portfolio, {}), 0.0, [], PriceCache())
    assert context["positions"][0]["priced"] is False
    assert context["positions"][0]["market_value"] == 100.0


def test_watchlist_entries_use_the_session_change_not_the_tick_change(cache):
    cache.update("AAPL", 220.0)  # session_open stays 200 from the fixture
    entry = build_context(_valuation(cache), 0.0, ["AAPL"], cache)["watchlist"][0]
    assert entry["price"] == 220.0
    assert entry["change_percent_today"] == 10.0


def test_a_watchlist_ticker_with_no_price_yet_reports_none(cache):
    entry = build_context(_valuation(cache), 0.0, ["PYPL"], cache)["watchlist"][0]
    assert entry == {"ticker": "PYPL", "price": None, "change_percent_today": None}


def test_history_passes_user_turns_through_unchanged():
    assert render_history([_message("user", "hello")]) == [
        {"role": "user", "content": "hello"}
    ]


def test_history_appends_executed_actions_to_the_assistant_turn():
    rendered = render_history(
        [
            _message(
                "assistant",
                "Bought them.",
                {"trades": [{"ticker": "AAPL", "side": "buy", "quantity": 10,
                             "status": "executed", "price": 200.0, "total": 2000.0}],
                 "watchlist_changes": []},
            )
        ]
    )
    assert "EXECUTED buy 10 AAPL at 200.0" in rendered[0]["content"]


def test_history_feeds_a_failed_trade_back_with_its_reason():
    rendered = render_history(
        [
            _message(
                "assistant",
                "Buying.",
                {"trades": [{"ticker": "NVDA", "side": "buy", "quantity": 100,
                             "status": "failed", "error_code": "INSUFFICIENT_CASH",
                             "error": "Insufficient cash: need $80,000.00."}],
                 "watchlist_changes": []},
            )
        ]
    )
    content = rendered[0]["content"]
    assert "FAILED buy 100 NVDA (INSUFFICIENT_CASH)" in content
    assert "Insufficient cash" in content


def test_history_renders_watchlist_outcomes_both_ways():
    rendered = render_history(
        [
            _message(
                "assistant",
                "Done.",
                {"trades": [],
                 "watchlist_changes": [
                     {"ticker": "PYPL", "action": "add", "status": "executed"},
                     {"ticker": "APPL", "action": "add", "status": "failed",
                      "error_code": "UNSUPPORTED_TICKER", "error": "Not available."},
                 ]},
            )
        ]
    )
    assert "EXECUTED watchlist add PYPL" in rendered[0]["content"]
    assert "FAILED watchlist add APPL (UNSUPPORTED_TICKER)" in rendered[0]["content"]


def test_an_assistant_turn_with_no_actions_gains_no_suffix():
    assert render_history([_message("assistant", "Hi")])[0]["content"] == "Hi"


def test_an_actions_blob_with_empty_lists_gains_no_suffix():
    actions = {"trades": [], "watchlist_changes": []}
    assert render_history([_message("assistant", "Hi", actions)])[0]["content"] == "Hi"


def test_messages_start_with_the_system_prompt_and_the_context(cache):
    context = build_context(_valuation(cache), 0.0, ["AAPL"], cache)
    messages = build_messages(context, [_message("user", "hi")])
    assert messages[0]["role"] == "system"
    assert SYSTEM_PROMPT in messages[0]["content"]
    assert json.dumps(context, indent=2) in messages[0]["content"]
    assert messages[1:] == [{"role": "user", "content": "hi"}]
