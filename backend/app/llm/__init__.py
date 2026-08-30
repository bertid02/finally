"""LLM subsystem: the chat turn, its prompt, its structured-output contract.

Everything a caller needs is re-exported here; the submodule layout is internal.

    from app.llm import run_chat_turn

    body = await run_chat_turn(message=..., repo=..., cache=..., source=...,
                               llm_mock=app.state.settings.llm_mock)

`app/api/chat.py` is the only production caller. Tests reach for `parse_response`
and `mock_completion` directly.
"""

from .client import MODEL, LLMUnavailableError, complete
from .mock import MockUnavailableError, mock_completion
from .prompt import SYSTEM_PROMPT, build_context, build_messages
from .schema import (
    LLMResponse,
    LLMResponseError,
    TradeSpec,
    WatchlistChangeSpec,
    parse_response,
)
from .service import EmptyMessageError, InvalidWatchlistActionError, run_chat_turn

__all__ = [
    "MODEL",
    "LLMUnavailableError",
    "complete",
    "MockUnavailableError",
    "mock_completion",
    "SYSTEM_PROMPT",
    "build_context",
    "build_messages",
    "LLMResponse",
    "LLMResponseError",
    "TradeSpec",
    "WatchlistChangeSpec",
    "parse_response",
    "EmptyMessageError",
    "InvalidWatchlistActionError",
    "run_chat_turn",
]
