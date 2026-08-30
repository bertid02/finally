"""Chat message persistence, including the actions payload."""

from __future__ import annotations

import pytest

from app.db import DatabaseError, Repository

# PLAN.md section 7, verbatim -- including a failed trade, which the chat panel
# renders as an error and section 9 feeds back to the LLM.
ACTIONS = {
    "trades": [
        {
            "ticker": "AAPL",
            "side": "buy",
            "quantity": 10,
            "status": "executed",
            "price": 190.50,
            "total": 1905.00,
        },
        {
            "ticker": "NVDA",
            "side": "buy",
            "quantity": 100,
            "status": "failed",
            "error_code": "INSUFFICIENT_CASH",
            "error": "Insufficient cash: need $80,000.00, have $8,095.00",
        },
    ],
    "watchlist_changes": [{"ticker": "PYPL", "action": "add", "status": "executed"}],
}


class TestWrite:
    async def test_user_message_has_no_actions(self, repo: Repository) -> None:
        message = await repo.add_chat_message("user", "How am I doing?")
        assert message.role == "user"
        assert message.actions is None

    async def test_assistant_message_with_actions(self, repo: Repository) -> None:
        message = await repo.add_chat_message("assistant", "Bought AAPL.", ACTIONS)
        assert message.actions == ACTIONS

    async def test_actions_round_trip_through_json(self, repo: Repository) -> None:
        await repo.add_chat_message("assistant", "Done.", ACTIONS)
        stored = (await repo.get_chat_messages())[0]
        assert stored.actions == ACTIONS
        assert stored.actions["trades"][1]["error_code"] == "INSUFFICIENT_CASH"

    async def test_empty_actions_dict_survives_as_empty(self, repo: Repository) -> None:
        # {} and None are different statements -- "the assistant acted, and the
        # action list is empty" versus "no action block at all" -- and the chat
        # panel renders them differently. Storing on `is not None` rather than
        # truthiness is what keeps them distinct.
        await repo.add_chat_message("assistant", "Nothing to do.", {})
        assert (await repo.get_chat_messages())[0].actions == {}

    async def test_none_actions_read_back_as_none(self, repo: Repository) -> None:
        await repo.add_chat_message("assistant", "Just chatting.")
        assert (await repo.get_chat_messages())[0].actions is None

    async def test_invalid_role_is_refused(self, repo: Repository) -> None:
        with pytest.raises(DatabaseError):
            await repo.add_chat_message("system", "You are a bot.")

    async def test_invalid_role_writes_nothing(self, repo: Repository) -> None:
        with pytest.raises(DatabaseError):
            await repo.add_chat_message("system", "hi")
        assert await repo.get_chat_messages() == []

    async def test_empty_content_is_allowed(self, repo: Repository) -> None:
        assert (await repo.add_chat_message("assistant", "")).content == ""

    async def test_unicode_content_survives(self, repo: Repository) -> None:
        content = "Portfolio up 3% — nice 中文 \U0001f4c8"
        await repo.add_chat_message("assistant", content)
        assert (await repo.get_chat_messages())[0].content == content


class TestRead:
    async def test_empty_initially(self, repo: Repository) -> None:
        assert await repo.get_chat_messages() == []

    async def test_oldest_first(self, repo: Repository) -> None:
        await repo.add_chat_message("user", "one")
        await repo.add_chat_message("assistant", "two")
        await repo.add_chat_message("user", "three")
        assert [m.content for m in await repo.get_chat_messages()] == ["one", "two", "three"]

    async def test_limit_keeps_the_newest_in_order(self, repo: Repository) -> None:
        for i in range(5):
            await repo.add_chat_message("user", str(i))
        assert [m.content for m in await repo.get_chat_messages(limit=2)] == ["3", "4"]

    @pytest.mark.parametrize("limit,expected", [(0, 1), (-3, 1), (10**9, 3)])
    async def test_limit_is_clamped(self, repo: Repository, limit: int, expected: int) -> None:
        for i in range(3):
            await repo.add_chat_message("user", str(i))
        assert len(await repo.get_chat_messages(limit=limit)) == expected

    async def test_message_shape(self, repo: Repository) -> None:
        await repo.add_chat_message("assistant", "hi", ACTIONS)
        payload = (await repo.get_chat_messages())[0].to_dict()
        assert set(payload) == {"id", "role", "content", "actions", "created_at"}
        assert payload["actions"] == ACTIONS

    async def test_returned_message_matches_stored_message(self, repo: Repository) -> None:
        written = await repo.add_chat_message("user", "hello")
        assert (await repo.get_chat_messages())[0] == written

    async def test_scoped_to_user(self, repo: Repository) -> None:
        await repo.add_chat_message("user", "mine")
        await repo.add_chat_message("user", "theirs", user_id="other")
        assert [m.content for m in await repo.get_chat_messages()] == ["mine"]
        assert [m.content for m in await repo.get_chat_messages(user_id="other")] == ["theirs"]
