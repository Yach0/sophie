"""Chatbot trigger coverage for reply titles in the model history."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import Message, Update, User
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory, MessageFactory

from sophie_bot.config import CONFIG
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.utils.message_history import AIMessageHistory

HistoryCapture = Callable[..., Awaitable[None]]


def _apply_ai_patches(stack: ExitStack) -> None:
    stack.enter_context(
        patch(
            "sophie_bot.modules.ai.middlewares.cache_user_messages.resolve_chat_mode",
            AsyncMock(return_value=AIMode.support),
        )
    )
    stack.enter_context(
        patch(
            "sophie_bot.modules.ai.filters.quota.check_quota",
            AsyncMock(return_value=SimpleNamespace(allowed=True)),
        )
    )
    stack.enter_context(patch("sophie_bot.modules.ai.filters.quota.get_quota_info", AsyncMock(return_value=None)))
    stack.enter_context(
        patch("sophie_bot.modules.ai.middlewares.ai_moderator.is_enabled", AsyncMock(return_value=False))
    )
    stack.enter_context(patch("sophie_bot.modules.ai.utils.message_history.is_enabled", AsyncMock(return_value=False)))


def _history_capture(prompts: list[list[Any]]) -> HistoryCapture:
    async def capture(
        message: Message,
        connection: object,
        user_text: str | None = None,
        **kwargs: Any,
    ) -> None:
        history = AIMessageHistory()
        await history.add_from_message(message, custom_text=user_text)
        prompts.append(history.prompt)

    return capture


async def _feed_message(test_client: TestClient, message: Message) -> None:
    await test_client.dispatcher.feed_update(
        bot=test_client.bot,
        update=Update(update_id=message.message_id, message=message),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("replied", "expected_prompt"),
    (
        (True, "Alice (reply to Bob): question"),
        (False, "Alice: question"),
    ),
)
async def test_ai_command_builds_reply_title_and_preserves_non_reply(
    test_client: TestClient,
    replied: bool,
    expected_prompt: str,
) -> None:
    group = ChatFactory.create_group(chat_id=-1002900000091, title="AI reply context")
    alice = User(id=929000091, is_bot=False, first_name="Alice")
    bob = User(id=929000092, is_bot=False, first_name="Bob")
    await test_client.send_message(text="init", from_user=alice, chat=group)
    replied_message = MessageFactory.create(text="earlier", from_user=bob, chat=group) if replied else None
    command = MessageFactory.create(
        text="/ai question",
        from_user=alice,
        chat=group,
        reply_to_message=replied_message,
    )
    prompts: list[list[Any]] = []

    with ExitStack() as stack:
        _apply_ai_patches(stack)
        stack.enter_context(patch("sophie_bot.modules.ai.handlers.ai_cmd.ai_chatbot_reply", _history_capture(prompts)))
        await _feed_message(test_client, command)

    assert prompts
    assert prompts[0][-1] == expected_prompt


@pytest.mark.asyncio
async def test_reply_to_ai_without_command_builds_reply_title(test_client: TestClient) -> None:
    group = ChatFactory.create_group(chat_id=-1002900000092, title="AI follow-up context")
    alice = User(id=929000093, is_bot=False, first_name="Alice")
    sophie = User(id=CONFIG.bot_id, is_bot=True, first_name="Sophie")
    await test_client.send_message(text="init", from_user=alice, chat=group)
    ai_message = MessageFactory.create(text="✨ AI | Answer\nEarlier answer", from_user=sophie, chat=group)
    follow_up = MessageFactory.create(
        text="follow up",
        from_user=alice,
        chat=group,
        reply_to_message=ai_message,
    )
    prompts: list[list[Any]] = []

    with ExitStack() as stack:
        _apply_ai_patches(stack)
        stack.enter_context(patch("sophie_bot.modules.ai.handlers.reply.ai_chatbot_reply", _history_capture(prompts)))
        await _feed_message(test_client, follow_up)

    assert prompts
    assert prompts[0][-1] == "Alice (reply to Sophie): follow up"
