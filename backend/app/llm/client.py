"""The provider call: LiteLLM -> OpenRouter -> Cerebras.

Per the project's `cerebras` skill and PLAN.md section 9: model
`openrouter/openai/gpt-oss-120b`, inference pinned to Cerebras through
`extra_body`, structured outputs via `response_format`. The `openai` SDK is not
used directly.

Two things here are deliberate:

* **The import is lazy.** `import litellm` costs seconds and pulls a large
  dependency tree; doing it at module scope would put that on every `import
  app.main`, including the 500-odd tests that never make a call. `_load_completion`
  also gives tests one seam to patch instead of reaching into a third-party module.
* **Every provider failure becomes `LLMUnavailableError`.** LiteLLM raises a wide
  family of exceptions (auth, rate limit, timeout, transport). The chat turn's job
  is identical for all of them -- apologise in prose and return 200 -- so they are
  flattened here rather than enumerated at the call site.

`completion` is synchronous, so it runs in a worker thread. Blocking the event
loop for the length of an inference call would stall the SSE stream every ticker
on the page is reading from.
"""

from __future__ import annotations

import asyncio
import logging

from .schema import LLMResponse

logger = logging.getLogger(__name__)

MODEL = "openrouter/openai/gpt-oss-120b"
# Pin the inference provider. Cerebras is why the design can get away with no
# token streaming -- the whole response arrives faster than a spinner gets boring.
EXTRA_BODY = {"provider": {"order": ["cerebras"]}}
REASONING_EFFORT = "low"


class LLMUnavailableError(Exception):
    """The provider could not be reached, or refused the request."""


def _load_completion():
    """Import LiteLLM on first use. Patched wholesale in tests."""
    from litellm import completion

    return completion


async def complete(messages: list[dict]) -> str:
    """Send one turn and return the raw response text.

    Raises:
        LLMUnavailableError -- any provider-side failure, including a response
        with no content at all (which is not parseable and not worth a second
        vocabulary).
    """
    completion = _load_completion()

    def _call() -> str:
        response = completion(
            model=MODEL,
            messages=messages,
            response_format=LLMResponse,
            reasoning_effort=REASONING_EFFORT,
            extra_body=EXTRA_BODY,
        )
        return response.choices[0].message.content

    try:
        content = await asyncio.to_thread(_call)
    except Exception as exc:  # noqa: BLE001 -- flattened on purpose, see module docstring
        logger.warning("LLM call failed: %s", exc)
        raise LLMUnavailableError(str(exc)) from exc

    if content is None:
        raise LLMUnavailableError("The model returned no content.")
    return content
