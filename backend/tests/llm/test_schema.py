"""The structured-output parser: the envelope is strict, the contents are not."""

from __future__ import annotations

import json

import pytest

from app.llm.schema import DEFAULT_MESSAGE, LLMResponseError, parse_response


def test_parses_a_complete_response():
    parsed = parse_response(
        json.dumps(
            {
                "message": "Bought them.",
                "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 10}],
                "watchlist_changes": [{"ticker": "PYPL", "action": "add"}],
            }
        )
    )
    assert parsed.message == "Bought them."
    assert parsed.trades[0].ticker == "AAPL"
    assert parsed.trades[0].quantity == 10.0
    assert parsed.watchlist_changes[0].action == "add"


def test_message_only_response_has_empty_action_lists():
    parsed = parse_response('{"message": "Your portfolio is fine."}')
    assert parsed.trades == []
    assert parsed.watchlist_changes == []


def test_strips_a_markdown_code_fence():
    parsed = parse_response('```json\n{"message": "Hi"}\n```')
    assert parsed.message == "Hi"


def test_strips_a_bare_code_fence_without_a_language():
    parsed = parse_response('```\n{"message": "Hi"}\n```')
    assert parsed.message == "Hi"


def test_a_fence_with_no_newline_leaves_nothing_to_parse():
    with pytest.raises(LLMResponseError):
        parse_response("```")


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_empty_output_is_a_response_error(raw):
    with pytest.raises(LLMResponseError, match="empty"):
        parse_response(raw)


def test_non_json_is_a_response_error():
    with pytest.raises(LLMResponseError, match="invalid JSON"):
        parse_response("Sure, here is your analysis.")


def test_json_that_is_not_an_object_is_a_response_error():
    with pytest.raises(LLMResponseError, match="not an object"):
        parse_response('["message"]')


@pytest.mark.parametrize("payload", ['{"trades": []}', '{"message": ""}', '{"message": 7}'])
def test_a_missing_message_is_substituted_not_fatal(payload):
    assert parse_response(payload).message == DEFAULT_MESSAGE


def test_a_missing_message_keeps_the_trades():
    parsed = parse_response('{"trades": [{"ticker": "AAPL", "side": "buy", "quantity": 1}]}')
    assert parsed.message == DEFAULT_MESSAGE
    assert len(parsed.trades) == 1


def test_action_lists_of_the_wrong_type_are_ignored():
    parsed = parse_response('{"message": "hi", "trades": "AAPL", "watchlist_changes": 3}')
    assert parsed.trades == []
    assert parsed.watchlist_changes == []


def test_non_object_entries_are_dropped():
    parsed = parse_response(
        '{"message": "hi", "trades": ["AAPL", {"ticker": "MSFT", "side": "buy", '
        '"quantity": 2}], "watchlist_changes": [null]}'
    )
    assert [t.ticker for t in parsed.trades] == ["MSFT"]
    assert parsed.watchlist_changes == []


def test_a_malformed_trade_survives_so_it_can_fail_with_a_real_error_code():
    # Dropping it would leave the user reading "bought 10 AAPL" with no chip.
    parsed = parse_response('{"message": "hi", "trades": [{"side": "buy"}]}')
    assert parsed.trades[0].ticker == ""
    assert parsed.trades[0].quantity == 0.0


@pytest.mark.parametrize("quantity", ["ten", None, {}])
def test_an_uncoercible_quantity_becomes_zero(quantity):
    parsed = parse_response(
        json.dumps({"message": "hi", "trades": [{"ticker": "AAPL", "side": "buy",
                                                 "quantity": quantity}]})
    )
    assert parsed.trades[0].quantity == 0.0


def test_a_numeric_string_quantity_is_accepted():
    parsed = parse_response(
        '{"message": "hi", "trades": [{"ticker": "AAPL", "side": "buy", "quantity": "10"}]}'
    )
    assert parsed.trades[0].quantity == 10.0


def test_a_watchlist_change_missing_its_action_survives():
    parsed = parse_response('{"message": "hi", "watchlist_changes": [{"ticker": "PYPL"}]}')
    assert parsed.watchlist_changes[0].action == ""
