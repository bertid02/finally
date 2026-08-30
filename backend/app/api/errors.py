"""The one error vocabulary: PLAN.md section 8's envelope, for every non-2xx response.

    {"error": {"code": "INSUFFICIENT_CASH", "message": "Insufficient cash: ..."}}

Seven of the eight codes are raised by `app.db` and arrive here already carrying
`.code`, `.http_status` and `.to_envelope()`. This module adds the eighth --
`UNSUPPORTED_TICKER`, which comes from `MarketDataSource.supports_ticker()` and so
belongs to the API layer -- and installs the handlers that turn all of them, plus
FastAPI's own failures, into the same body.

`message` is user-facing prose. It is rendered verbatim in the order bar and in
chat action chips, so it must read like a sentence, not a log line.
"""

from __future__ import annotations

import logging
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.db import DatabaseError, InvalidTickerError
from app.market.interface import TICKER_PATTERN, normalize_ticker

logger = logging.getLogger(__name__)

_TICKER_RE = re.compile(f"^{TICKER_PATTERN}$")

# A field that failed Pydantic's own parsing maps onto the section 8 code for that
# field, so `{"quantity": "ten"}` and `{"quantity": -1}` report the same way. Any
# other validation failure is a malformed request body, which section 8 has no
# code for -- INVALID_REQUEST keeps the envelope shape universal rather than
# letting FastAPI's default `{"detail": [...]}` leak out of one endpoint.
_FIELD_CODES = {
    "quantity": ("INVALID_QUANTITY", 400),
    "side": ("INVALID_SIDE", 400),
    "ticker": ("INVALID_TICKER", 400),
}


class APIError(Exception):
    """Base for failures the API layer raises itself.

    Mirrors `DatabaseError`'s surface exactly -- same attributes, same envelope --
    because the frontend and the chat panel must not be able to tell which layer
    a failure came from.
    """

    code: str = "API_ERROR"
    http_status: int = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def to_envelope(self) -> dict[str, dict[str, str]]:
        return {"error": {"code": self.code, "message": self.message}}


class UnsupportedTickerError(APIError):
    """`supports_ticker()` returned False -- the data source cannot price this symbol.

    Distinct from `UNKNOWN_TICKER` (404), which means "no price *yet*". This one
    means "never": a typo like APPL under Massive, which would otherwise sit in
    the watchlist permanently priceless.
    """

    code = "UNSUPPORTED_TICKER"
    http_status = 422


def envelope(code: str, message: str) -> dict[str, dict[str, str]]:
    """Build the section 8 body from parts, for callers holding no exception."""
    return {"error": {"code": code, "message": message}}


def validate_ticker(raw: str) -> str:
    """Trim, uppercase, and enforce ^[A-Z]{1,5}$. Returns the normalized symbol.

    Run before touching the price cache: the cache is keyed by normalized symbol,
    so `get_price("aapl")` would miss and report UNKNOWN_TICKER for a ticker that
    is in fact streaming.

    Raises:
        InvalidTickerError -- deliberately the database layer's exception, so the
        format rule has one code no matter which layer notices the violation.
    """
    ticker = normalize_ticker(raw or "")
    if not _TICKER_RE.match(ticker):
        raise InvalidTickerError(f"Invalid ticker symbol: '{raw}'. Expected 1-5 letters.")
    return ticker


def _response(exc: DatabaseError | APIError) -> JSONResponse:
    return JSONResponse(status_code=exc.http_status, content=exc.to_envelope())


def register_error_handlers(app: FastAPI) -> None:
    """Install the handlers that make every non-2xx response share one shape."""

    @app.exception_handler(DatabaseError)
    async def _database_error(request: Request, exc: DatabaseError) -> JSONResponse:
        return _response(exc)

    @app.exception_handler(APIError)
    async def _api_error(request: Request, exc: APIError) -> JSONResponse:
        return _response(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        code, status = "INVALID_REQUEST", 422
        message = "Request body is malformed."
        for error in exc.errors():
            field = str(error["loc"][-1]) if error.get("loc") else ""
            if field in _FIELD_CODES:
                code, status = _FIELD_CODES[field]
                message = f"Invalid {field}: {error.get('msg', 'not acceptable')}."
                break
        return JSONResponse(status_code=status, content=envelope(code, message))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Covers 404s on unrouted /api paths and 405s, which would otherwise be
        # the only responses in the app not wearing the envelope.
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        return JSONResponse(
            status_code=exc.status_code,
            content=envelope(code, str(exc.detail)),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Anything that is not a DatabaseError or an APIError is a bug. Log it
        # with the traceback and tell the user something honest and non-technical.
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=envelope("INTERNAL_ERROR", "An unexpected server error occurred."),
        )
