"""The error vocabulary shared by the HTTP routes and the chat turn."""

from __future__ import annotations

import pytest

from app.db import (
    DatabaseError,
    InsufficientCashError,
    InsufficientSharesError,
    InvalidQuantityError,
    InvalidSideError,
    InvalidTickerError,
    UnknownTickerError,
    WatchlistFullError,
)

# PLAN.md section 8's table, as data. If a code or status drifts, this fails
# before the frontend and the LLM start disagreeing about what happened.
EXPECTED = [
    (InvalidQuantityError, "INVALID_QUANTITY", 400),
    (InvalidSideError, "INVALID_SIDE", 400),
    (InvalidTickerError, "INVALID_TICKER", 400),
    (UnknownTickerError, "UNKNOWN_TICKER", 404),
    (InsufficientCashError, "INSUFFICIENT_CASH", 409),
    (InsufficientSharesError, "INSUFFICIENT_SHARES", 409),
    (WatchlistFullError, "WATCHLIST_FULL", 409),
]


@pytest.mark.parametrize("cls,code,status", EXPECTED)
def test_code_and_status(cls: type[DatabaseError], code: str, status: int) -> None:
    error = cls("something went wrong")
    assert error.code == code
    assert error.http_status == status


@pytest.mark.parametrize("cls,code,status", EXPECTED)
def test_all_are_database_errors(cls: type[DatabaseError], code: str, status: int) -> None:
    assert issubclass(cls, DatabaseError)


@pytest.mark.parametrize("cls,code,status", EXPECTED)
def test_envelope_shape(cls: type[DatabaseError], code: str, status: int) -> None:
    assert cls("boom").to_envelope() == {"error": {"code": code, "message": "boom"}}


def test_message_is_the_str_value() -> None:
    assert str(InsufficientCashError("need more")) == "need more"


def test_base_error_defaults() -> None:
    error = DatabaseError("generic")
    assert error.code == "DATABASE_ERROR"
    assert error.http_status == 500
    assert error.message == "generic"


def test_codes_are_unique() -> None:
    codes = [code for _, code, _ in EXPECTED]
    assert len(set(codes)) == len(codes)
