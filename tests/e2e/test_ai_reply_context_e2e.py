"""Chatbot trigger coverage for reply titles in the model history."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import Message, RichBlockParagraph, RichMessage, RichTextCustomEmoji, Update, User
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


def _history_capture(
    prompts: list[list[Any]],
    services: object,
) -> HistoryCapture:
    async def capture(
        message: Message,
        connection: object,
        user_text: str | None = None,
        **kwargs: Any,
    ) -> None:
        history = AIMessageHistory(services=services)
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
        stack.enter_context(
            patch(
                "sophie_bot.modules.ai.handlers.ai_cmd.ai_chatbot_reply",
                _history_capture(
                    prompts,
                    test_client.dispatcher.workflow_data["services"],
                ),
            )
        )
        await _feed_message(test_client, command)

    assert prompts
    assert prompts[0][-1] == expected_prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", ("tagged_tip", "tag_only"))
async def test_reply_to_ai_without_command_builds_reply_title(test_client: TestClient, layout: str) -> None:
    group = ChatFactory.create_group(chat_id=-1002900000092, title="AI follow-up context")
    alice = User(id=929000093, is_bot=False, first_name="Alice")
    sophie = User(id=CONFIG.bot_id, is_bot=True, first_name="Sophie")
    await test_client.send_message(text="init", from_user=alice, chat=group)
    rich_text = [
        RichTextCustomEmoji(custom_emoji_id="5325547803936572038", alternative_text="✨"),
        " Earlier answer",
    ]
    if layout == "tagged_tip":
        rich_text.extend(
            [
                "\n",
                RichTextCustomEmoji(custom_emoji_id="5816915599019741395", alternative_text="🔋"),
                " 80% ⚠️ Help mode is available.",
            ]
        )
    rich_message = RichMessage(blocks=[RichBlockParagraph(text=rich_text)])
    ai_message = MessageFactory.create(text="Earlier answer", from_user=sophie, chat=group).model_copy(
        update={
            "rich_message": rich_message,
        }
    )
    follow_up = MessageFactory.create(
        text="follow up",
        from_user=alice,
        chat=group,
        reply_to_message=ai_message,
    )
    prompts: list[list[Any]] = []

    with ExitStack() as stack:
        _apply_ai_patches(stack)
        stack.enter_context(
            patch(
                "sophie_bot.modules.ai.handlers.reply.ai_chatbot_reply",
                _history_capture(
                    prompts,
                    test_client.dispatcher.workflow_data["services"],
                ),
            )
        )
        await _feed_message(test_client, follow_up)

    assert prompts
    assert prompts[0][0] == "Sophie: Earlier answer"
    assert prompts[0][-1] == "Alice (reply to Sophie): follow up"
