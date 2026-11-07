from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import SetMessageReaction
from aiogram.types import Chat, ChatFullInfo, Message, ReactionTypeCustomEmoji, ReactionTypeEmoji
from beanie import PydanticObjectId
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from sophie_bot.db.models import ChatModel
from sophie_bot.modules.ai.utils import proactive_replies
from sophie_bot.modules.ai.utils.cache_messages import MessageType
from sophie_bot.modules.ai.utils.proactive_replies import ProactiveAction, ProactiveDecision, ProactiveReplySettings
from sophie_bot.services.application import ApplicationServices


def make_services(redis: Redis, *, bot_id: int = 123, **chat_fields: object) -> ApplicationServices:
    chat_info = ChatFullInfo(
        id=-100,
        type="supergroup",
        title="Group",
        accent_color_id=0,
        max_reaction_count=1,
        accepted_gift_types={
            "unlimited_gifts": False,
            "limited_gifts": False,
            "unique_gifts": False,
            "premium_subscription": False,
            "gifts_from_channels": False,
        },
        **chat_fields,
    )
    return cast(
        ApplicationServices,
        SimpleNamespace(
            redis=redis,
            bot=SimpleNamespace(
                id=bot_id, get_chat=AsyncMock(return_value=chat_info), set_message_reaction=AsyncMock(return_value=True)
            ),
        ),
    )


@pytest.mark.parametrize(
    "chat_fields, expected",
    [
        ({}, proactive_replies._TELEGRAM_REACTION_EMOJIS),
        ({"available_reactions": None}, proactive_replies._TELEGRAM_REACTION_EMOJIS),
        ({"available_reactions": []}, frozenset()),
        (
            {"available_reactions": [ReactionTypeEmoji(emoji="🔥"), ReactionTypeCustomEmoji(custom_emoji_id="42")]},
            frozenset({"🔥"}),
        ),
        ({"available_reactions": [ReactionTypeCustomEmoji(custom_emoji_id="42")]}, frozenset()),
    ],
)
async def test_capabilities_cache_miss_and_hit(
    test_redis: Redis,
    chat_fields: dict[str, object],
    expected: frozenset[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = make_services(test_redis, **chat_fields)
    cache_set = Mock(wraps=test_redis.set)
    monkeypatch.setattr(test_redis, "set", cache_set)
    assert await proactive_replies._get_allowed_reactions(-100, services=services) == expected
    services.bot.get_chat.assert_awaited_once_with(-100)
    cache_set.assert_called_once()
    cache_key = cache_set.call_args.args[0]
    assert cache_set.call_args.kwargs == {"ex": 3600}
    assert await test_redis.ttl(cache_key) == 3600
    assert await proactive_replies._get_allowed_reactions(-100, services=services) == expected
    services.bot.get_chat.assert_awaited_once()
    cache_set.assert_called_once()


async def test_capability_cache_separates_bots_and_chats(test_redis: Redis) -> None:
    first = make_services(test_redis, available_reactions=[ReactionTypeEmoji(emoji="🔥")])
    second = make_services(test_redis, bot_id=456, available_reactions=[])
    assert await proactive_replies._get_allowed_reactions(-100, services=first) == frozenset({"🔥"})
    assert await proactive_replies._get_allowed_reactions(-100, services=second) == frozenset()
    assert await proactive_replies._get_allowed_reactions(-200, services=first) == frozenset({"🔥"})
    assert first.bot.get_chat.await_count == 2
    assert second.bot.get_chat.await_count == 1
    assert len(await test_redis.keys("*")) == 3


@pytest.mark.parametrize("operation", ["get", "set"])
async def test_cache_errors_propagate(test_redis: Redis, operation: str, monkeypatch: pytest.MonkeyPatch) -> None:
    services = make_services(test_redis)
    failure = RedisConnectionError("offline")
    monkeypatch.setattr(test_redis, operation, AsyncMock(side_effect=failure))
    with pytest.raises(RedisConnectionError) as raised:
        await proactive_replies._get_allowed_reactions(-100, services=services)
    assert raised.value is failure


async def test_get_chat_errors_propagate(test_redis: Redis) -> None:
    services = make_services(test_redis)
    services.bot.get_chat.side_effect = RuntimeError("unexpected")
    with pytest.raises(RuntimeError, match="unexpected"):
        await proactive_replies._get_allowed_reactions(-100, services=services)
    assert await test_redis.keys("*") == []


@pytest.mark.parametrize("allowed", [frozenset({"🔥"}), frozenset(), proactive_replies._TELEGRAM_REACTION_EMOJIS])
async def test_decision_prompt_lists_only_allowed_choices(
    test_redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
    allowed: frozenset[str],
) -> None:
    services = make_services(test_redis)
    chat = cast(ChatModel, SimpleNamespace(iid=PydanticObjectId()))
    monkeypatch.setattr(
        proactive_replies,
        "get_proactive_replies_model_plan",
        AsyncMock(return_value=SimpleNamespace(primary=SimpleNamespace(model_name="offline"))),
    )
    monkeypatch.setattr(proactive_replies, "get_service_tier", AsyncMock(return_value=None))
    monkeypatch.setattr(proactive_replies, "is_enabled", AsyncMock(return_value=False))
    run_task = AsyncMock(return_value=SimpleNamespace(output=ProactiveDecision()))
    monkeypatch.setattr(proactive_replies, "run_structured_task", run_task)
    await proactive_replies._generate_decision(
        chat, -100, (), ProactiveReplySettings(), allowed_reactions=allowed, services=services
    )
    prompt = "\n".join(run_task.await_args.args[2].prompt)
    if allowed:
        choices = next(line for line in prompt.splitlines() if line.startswith("Allowed reaction emoji: "))
        assert frozenset(choices.removeprefix("Allowed reaction emoji: ").split()) == allowed
    else:
        assert "Reactions are disabled; choose only none or answer." in prompt
    assert run_task.await_args.args[0].output_type is ProactiveDecision
    services.bot.get_chat.assert_not_awaited()


@pytest.mark.parametrize(
    "allowed, emoji, expected",
    [
        (frozenset({"🔥"}), "🔥", "🔥"),
        (frozenset({"🔥"}), "👍", None),
        (frozenset({"🔥"}), "😊", None),
        (frozenset(), "👍", None),
        (proactive_replies._TELEGRAM_REACTION_EMOJIS, "😊", "👍"),
        (proactive_replies._TELEGRAM_REACTION_EMOJIS, " 🔥 ", "🔥"),
        (proactive_replies._TELEGRAM_REACTION_EMOJIS, None, None),
    ],
)
async def test_send_validates_resolved_choices(
    test_redis: Redis,
    allowed: frozenset[str],
    emoji: str | None,
    expected: str | None,
) -> None:
    services = make_services(test_redis)
    await proactive_replies._react_to_message(
        -100, MessageType(user_id=42, message_id=1, text="hello"), emoji, allowed_reactions=allowed, services=services
    )
    if expected is None:
        services.bot.set_message_reaction.assert_not_awaited()
    else:
        services.bot.set_message_reaction.assert_awaited_once_with(
            chat_id=-100, message_id=1, reaction=[ReactionTypeEmoji(emoji=expected)]
        )
    services.bot.get_chat.assert_not_awaited()


@pytest.mark.parametrize("description", ["Bad Request: REACTION_INVALID", "Bad Request: REACTION_INVALID (stale)"])
async def test_invalid_reaction_invalidates_only_its_cache_and_continues_run(
    test_redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
    description: str,
) -> None:
    services = make_services(test_redis)
    other_bot = make_services(test_redis, bot_id=456, available_reactions=[])
    await proactive_replies._get_allowed_reactions(-100, services=other_bot)
    await proactive_replies._get_allowed_reactions(-200, services=services)
    messages = tuple(MessageType(user_id=42, message_id=message_id, text="hello") for message_id in (1, 2, 3, 4))
    chat = cast(ChatModel, SimpleNamespace(tid=-100, iid=PydanticObjectId()))
    decision = ProactiveDecision(
        actions=[
            ProactiveAction(action="answer", message_id=1),
            ProactiveAction(action="react", message_id=2, emoji="👍"),
            ProactiveAction(action="react", message_id=3, emoji="🔥"),
            ProactiveAction(action="answer", message_id=4),
        ]
    )
    services.bot.set_message_reaction.side_effect = [
        TelegramBadRequest(method=SetMessageReaction(chat_id=-100, message_id=2), message=description),
        True,
    ]
    generate = AsyncMock(return_value=decision)
    execute = AsyncMock(wraps=proactive_replies._execute_actions)
    answer = AsyncMock()
    clear = AsyncMock()
    monkeypatch.setattr(proactive_replies, "check_quota", AsyncMock(return_value=SimpleNamespace(allowed=True)))
    monkeypatch.setattr(
        proactive_replies,
        "_get_settings",
        AsyncMock(return_value=ProactiveReplySettings(min_messages=3, max_answers=2, max_reactions=2)),
    )
    monkeypatch.setattr(proactive_replies, "_track_eligible_message", AsyncMock(return_value=4))
    monkeypatch.setattr(proactive_replies, "_get_recent_candidates", AsyncMock(return_value=messages))
    monkeypatch.setattr(proactive_replies, "_generate_decision", generate)
    monkeypatch.setattr(proactive_replies, "_execute_actions", execute)
    monkeypatch.setattr(proactive_replies, "_answer_message", answer)
    monkeypatch.setattr(proactive_replies, "_clear_tracked_messages", clear)
    message = Message(message_id=4, date=datetime.now(UTC), chat=Chat(id=-100, type="supergroup"), text="hello")
    await proactive_replies.maybe_run_proactive_reply(message, chat, services=services)
    generate.assert_awaited_once()
    execute.assert_awaited_once()
    assert generate.await_args.kwargs["allowed_reactions"] is execute.await_args.kwargs["allowed_reactions"]
    assert [entry.args[2].message_id for entry in answer.await_args_list] == [1, 4]
    assert [entry.kwargs["message_id"] for entry in services.bot.set_message_reaction.await_args_list] == [2, 3]
    clear.assert_awaited_once()
    assert services.bot.get_chat.await_count == 2  # other chat, then this run; no retry
    assert len(await test_redis.keys("*")) == 2  # other bot and other chat survive
    await proactive_replies._get_allowed_reactions(-100, services=other_bot)
    await proactive_replies._get_allowed_reactions(-200, services=services)
    assert services.bot.get_chat.await_count == 2
    await proactive_replies._get_allowed_reactions(-100, services=services)
    assert services.bot.get_chat.await_count == 3


@pytest.mark.parametrize("error_type", [RuntimeError, TelegramNetworkError, TelegramBadRequest])
async def test_unexpected_send_errors_propagate(test_redis: Redis, error_type: type[Exception]) -> None:
    services = make_services(test_redis)
    failure = (
        RuntimeError("unexpected")
        if error_type is RuntimeError
        else error_type(method=SetMessageReaction(chat_id=-100, message_id=1), message="unexpected")
    )
    services.bot.set_message_reaction.side_effect = failure
    with pytest.raises(error_type) as raised:
        await proactive_replies._react_to_message(
            -100,
            MessageType(user_id=42, message_id=1, text="hello"),
            "👍",
            allowed_reactions=frozenset({"👍"}),
            services=services,
        )
    assert raised.value is failure
