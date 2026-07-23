"""Shared building blocks for e2e tests: ID allocation and chat registration."""

from __future__ import annotations

from itertools import count
from typing import TYPE_CHECKING

from aiogram_test_framework.factories import ChatFactory

from sophie_bot.db.models.chat import ChatModel

if TYPE_CHECKING:
    from aiogram.types import Chat, User
    from aiogram_test_framework import TestClient

# Allocated from ranges no hand-written test literal uses, so a test that still pins its own
# IDs cannot collide with a generated one.
_user_ids = count(800_000_001)
_group_ids = count(-1_009_000_000_001, -1)


def next_user_id() -> int:
    """Allocate a Telegram user ID that no other test in this process will reuse."""
    return next(_user_ids)


def next_group_id() -> int:
    """Allocate a Telegram supergroup ID that no other test in this process will reuse."""
    return next(_group_ids)


async def create_test_user_and_group(
    test_client: TestClient,
    *,
    user_id: int | None = None,
    first_name: str = "Tester",
    username: str | None = None,
    chat_id: int | None = None,
    group_title: str = "Test Group",
) -> tuple[User, Chat, ChatModel]:
    """Create a user and a group, and persist both by sending one message through the bot.

    IDs default to freshly allocated ones; pass them explicitly only when the test asserts
    on the literal value.
    """
    user_id = next_user_id() if user_id is None else user_id
    chat_id = next_group_id() if chat_id is None else chat_id

    user_wrapper = test_client.create_user(
        user_id=user_id,
        first_name=first_name,
        username=username if username is not None else f"user_{user_id}",
    )
    group = ChatFactory.create_group(chat_id=chat_id, title=group_title)

    # Registration is a side effect of SaveChatsMiddleware; there is no other way in.
    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group)

    user_model = await ChatModel.get_by_tid(user_id)
    assert user_model is not None, f"ChatModel for user {user_id} should exist after init message"

    return user_wrapper.user, group, user_model
