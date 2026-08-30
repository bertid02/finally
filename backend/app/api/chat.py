"""`POST /api/chat` -- the AI assistant endpoint.

Thin by design: the turn itself lives in `app/llm/service.py`, and this module is
only the HTTP shape around it. PLAN.md section 9 specifies a single non-streaming
call -- Cerebras inference returns fast enough that a loading indicator beats
token-by-token plumbing.

The response carries all five fields on every turn (team-lead's ruling in
TEAM_LOG.md): `message`, `actions`, and the `watchlist` / `cash_balance` /
`positions` echoes. The echoes are not decoration -- the assistant can change the
watchlist mid-turn and the SSE stream carries no membership event, so this
response is the frontend's only signal that it did.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from app.llm import run_chat_turn

from .deps import MarketSourceDep, PriceCacheDep, RepositoryDep

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    """`{"message": "how is my portfolio doing?"}`."""

    model_config = ConfigDict(extra="ignore")

    message: str


@router.post("")
async def chat(
    body: ChatRequest,
    request: Request,
    repo: RepositoryDep,
    cache: PriceCacheDep,
    source: MarketSourceDep,
) -> dict:
    """Send one message, get the reply plus whatever it did.

    Only an empty message is rejected. Every other failure -- a provider outage,
    unparseable JSON, a trade the account cannot afford -- comes back 200 with the
    explanation in `message` or in a failed `actions` entry, because a chat panel
    that answers a question with an HTTP error tells the user nothing they can act
    on.

    `llm_mock` is read from `app.state.settings`, the object that already decided
    it at startup, rather than from the environment a second time.
    """
    return await run_chat_turn(
        message=body.message,
        repo=repo,
        cache=cache,
        source=source,
        llm_mock=request.app.state.settings.llm_mock,
    )
