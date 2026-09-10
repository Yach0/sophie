from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SetMessageReaction
from aiogram.types import Chat, Message, User
from redis.asyncio import Redis

from sophie_bot.db.models import ChatModel
from sophie_bot.modules.ai.utils import proactive_replies
from sophie_bot.modules.ai.utils.cache_messages import MessageType, cache_message
from sophie_bot.modules.ai.utils.message_history import AIMessageHistory
from sophie_bot.modules.ai.utils.proactive_replies import (
    ProactiveAction,
    ProactiveDecision,
    ProactiveReplySettings,
    _build_answer_history,
    _get_recent_candidates,
    _limit_actions,
    _normalize_reaction_emoji,
)
from sophie_bot.modules.ai.utils.proactive_tracking import is_candidate
from sophie_bot.services.application import ApplicationServices


@pytest.mark.parametrize(
    ("message", "expected"),
    (
        (
            MessageType(user_id=1, message_id=1, text="hello"),
            True,
        ),
        (
            MessageType(user_id=1, message_id=2, text="/ai hello", has_ai_command=True),
            False,
        ),
        (
            MessageType(user_id=1, message_id=3, text="hello", handled_by_ai=True),
            False,
        ),
        (
            MessageType(user_id=1, message_id=4, text="hello", reply_to_is_sophie_ai=True),
            False,
        ),
        (
            MessageType(user_id=1, message_id=5, text="hello", eligible_for_proactive_ai=False),
            False,
        ),
    ),
)
def test_is_candidate_filters_already_handled_ai_messages(message: MessageType, expected: bool) -> None:
    assert is_candidate(message) is expected


def test_normalize_reaction_emoji_keeps_supported_telegram_reactions() -> None:
    assert _normalize_reaction_emoji("🤣") == "🤣"


def test_normalize_reaction_emoji_falls_back_for_invalid_reactions() -> None:
    assert _normalize_reaction_emoji("😊") == "👍"


def test_limit_actions_respects_answer_and_reaction_caps() -> None:
    decision = ProactiveDecision(
        actions=[
            ProactiveAction(action="answer", message_id=1),
            ProactiveAction(action="react", message_id=2, emoji="👍"),
            ProactiveAction(action="answer", message_id=3),
            ProactiveAction(action="react", message_id=4, emoji="😂"),
            ProactiveAction(action="answer", message_id=5),
            ProactiveAction(action="none"),
        ]
    )
    settings = ProactiveReplySettings(max_answers=2, max_reactions=1)

    actions = _limit_actions(decision, settings)

    assert [(action.action, action.message_id) for action in actions] == [
        ("answer", 1),
        ("react", 2),
        ("answer", 3),
    ]


@pytest.mark.asyncio
async def test_proactive_answer_history_keeps_reply_title(
    monkeypatch: pytest.MonkeyPatch,
    test_redis: object,
) -> None:
    monkeypatch.setattr(AIMessageHistory, "add_from_cache", AsyncMock())
    target = MessageType(
        user_id=1,
        message_id=2,
        text="hello",
        username="Alice",
        reply_to_user_id=3,
        reply_to_username="Bob",
    )

    history = await _build_answer_history(
        -100,
        target,
        services=type("Services", (), {"redis": test_redis})(),
    )

    assert history.prompt == ["Alice (reply to Bob): hello"]


@pytest.mark.asyncio
async def test_get_recent_candidates_uses_window_batch_and_eligibility(
    test_redis: object,
) -> None:
    chat_tid = -1001234567890
    now = datetime.now(UTC)
    old_created_at = now - timedelta(minutes=20)
    recent_created_at = now - timedelta(minutes=3)

    await cache_message(
        "too old",
        chat_tid,
        10,
        10,
        old_created_at,
        "old_user",
        redis=test_redis,
    )
    await cache_message(
        "/ai already handled",
        chat_tid,
        11,
        11,
        recent_created_at,
        "ai_user",
        has_ai_command=True,
        eligible_for_proactive_ai=False,
        redis=test_redis,
    )
    for message_id in range(12, 18):
        await cache_message(
            f"message {message_id}",
            chat_tid,
            message_id,
            message_id,
            recent_created_at + timedelta(seconds=message_id),
            f"user_{message_id}",
            redis=test_redis,
        )

    settings = ProactiveReplySettings(batch_size=3, window_seconds=600, min_messages=3)

    candidates = await _get_recent_candidates(
        chat_tid,
        settings,
        redis=test_redis,
    )

    assert [message.message_id for message in candidates] == [15, 16, 17]


async def test_failed_proactive_action_retries_on_the_next_message(
    monkeypatch: pytest.MonkeyPatch,
    test_redis: Redis,
) -> None:
    chat_tid = -1001234567890
    chat = cast(ChatModel, SimpleNamespace(tid=chat_tid, iid="chat-iid"))
    telegram_chat = Chat(id=chat_tid, type="supergroup", title="Group")
    settings = ProactiveReplySettings(min_messages=3, batch_size=3, window_seconds=300)
    failure = TelegramNetworkError(
        method=SetMessageReaction(chat_id=chat_tid, message_id=3),
        message="Temporary network failure",
    )
    reaction = AsyncMock(side_effect=[failure, True])
    services = cast(
        ApplicationServices,
        SimpleNamespace(redis=test_redis, bot=SimpleNamespace(set_message_reaction=reaction)),
    )
    decision = ProactiveDecision(actions=[ProactiveAction(action="react", message_id=3, emoji=chr(0x1F44D))])
    monkeypatch.setattr(proactive_replies, "is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(proactive_replies, "check_quota", AsyncMock(return_value=SimpleNamespace(allowed=True)))
    monkeypatch.setattr(proactive_replies, "_get_settings", AsyncMock(return_value=settings))
    monkeypatch.setattr(proactive_replies, "_generate_decision", AsyncMock(return_value=decision))

    for message_id in range(1, 6):
        now = datetime.now(UTC)
        await cache_message("hello", chat_tid, 42, message_id, now, "sender", redis=test_redis)
        message = Message(
            message_id=message_id,
            date=now,
            chat=telegram_chat,
            from_user=User(id=42, is_bot=False, first_name="Sender"),
            text="hello",
        )
        if message_id == 3:
            with pytest.raises(TelegramNetworkError):
                await proactive_replies.maybe_run_proactive_reply(message, chat, services=services)
        else:
            await proactive_replies.maybe_run_proactive_reply(message, chat, services=services)

    assert reaction.await_count == 2
    assert reaction.await_args.kwargs["message_id"] == 3
