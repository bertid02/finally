"""The structured-output contract, and a parser that refuses to raise on bad JSON.

PLAN.md section 9 fixes the shape the model must return:

    {"message": "...",
     "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 10}],
     "watchlist_changes": [{"ticker": "PYPL", "action": "add"}]}

`LLMResponse` is handed to LiteLLM as `response_format`, so the provider enforces
the shape on the way out. `parse_response` enforces it again on the way in --
because a structured-output guarantee is a guarantee about a *successful* call,
and the failure modes (a truncated stream, a provider that quietly ignored the
schema, a model that wrapped its JSON in a code fence) all arrive as text.

The parser is deliberately lenient about the *contents* of a trade and strict
only about the envelope. A trade with a missing ticker or a string quantity is
kept and passed to the execution path, where it fails with a real section 8 code
and lands in `chat_messages.actions` as an error chip the user can read. Dropping
it silently would leave the user watching a message that says "bought 10 AAPL"
with nothing to show for it.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# What the user sees when the model returned actions but no prose. Rare, but a
# blank assistant bubble reads as a bug, and the actions themselves are the news.
DEFAULT_MESSAGE = "Done."

_FENCE = "```"


class LLMResponseError(Exception):
    """The model's output was not a JSON object at all."""


class TradeSpec(BaseModel):
    """One trade the model wants executed. Validation belongs downstream."""

    model_config = ConfigDict(extra="forbid")

    ticker: str = Field(description="Ticker symbol, 1-5 letters, uppercase")
    side: str = Field(description="'buy' or 'sell'")
    quantity: float = Field(description="Number of shares; fractional allowed")


class WatchlistChangeSpec(BaseModel):
    """One watchlist mutation the model wants applied."""

    model_config = ConfigDict(extra="forbid")

    ticker: str = Field(description="Ticker symbol, 1-5 letters, uppercase")
    action: str = Field(description="'add' or 'remove'")


class LLMResponse(BaseModel):
    """The whole structured response. Also the `response_format` schema."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(description="Conversational reply shown to the user")
    trades: list[TradeSpec] = Field(default_factory=list)
    watchlist_changes: list[WatchlistChangeSpec] = Field(default_factory=list)


def _strip_fence(text: str) -> str:
    """Drop a ```json ... ``` wrapper if the model added one."""
    stripped = text.strip()
    if not stripped.startswith(_FENCE):
        return stripped
    body = stripped[len(_FENCE) :]
    body = body.split("\n", 1)[1] if "\n" in body else ""
    return body.rsplit(_FENCE, 1)[0].strip()


def _as_float(value: Any) -> float:
    """Coerce a quantity, mapping anything uncoercible to 0 -- which the repository
    rejects as INVALID_QUANTITY, the same code a literal 0 would earn."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _trade(raw: Any) -> TradeSpec | None:
    if not isinstance(raw, dict):
        return None
    return TradeSpec(
        ticker=str(raw.get("ticker") or ""),
        side=str(raw.get("side") or ""),
        quantity=_as_float(raw.get("quantity")),
    )


def _watchlist_change(raw: Any) -> WatchlistChangeSpec | None:
    if not isinstance(raw, dict):
        return None
    return WatchlistChangeSpec(
        ticker=str(raw.get("ticker") or ""),
        action=str(raw.get("action") or ""),
    )


def _entries(raw: Any) -> list[Any]:
    return raw if isinstance(raw, list) else []


def parse_response(raw: str | None) -> LLMResponse:
    """Turn the model's text into an `LLMResponse`.

    Raises:
        LLMResponseError -- the text was empty, was not JSON, or was JSON that is
        not an object. The caller degrades that into a chat message; it is never
        a 500.
    """
    if not raw or not raw.strip():
        raise LLMResponseError("The model returned an empty response.")

    try:
        data = json.loads(_strip_fence(raw))
    except json.JSONDecodeError as exc:
        raise LLMResponseError(f"The model returned invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise LLMResponseError("The model returned JSON that is not an object.")

    message = data.get("message")
    if not isinstance(message, str) or not message.strip():
        # A missing `message` is a schema violation, but everything else in the
        # payload may still be good -- so substitute prose rather than discarding
        # trades the user asked for.
        message = DEFAULT_MESSAGE

    trades = [t for t in (_trade(e) for e in _entries(data.get("trades"))) if t]
    changes = [
        c
        for c in (_watchlist_change(e) for e in _entries(data.get("watchlist_changes")))
        if c
    ]
    return LLMResponse(message=message.strip(), trades=trades, watchlist_changes=changes)
