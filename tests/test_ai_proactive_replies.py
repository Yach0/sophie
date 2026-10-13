from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock, call
from xml.etree import ElementTree

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import SetMessageReaction
from aiogram.types import Chat, Message, User
from beanie import PydanticObjectId
from pydantic_ai.messages import ModelRequest, UserPromptPart
from redis.asyncio import Redis

from sophie_bot.db.models import ChatModel
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.db.models.chat import ChatType
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.ai.utils import proactive_replies
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext
from sophie_bot.modules.ai.utils.cache_messages import MessageType, cache_message
from sophie_bot.modules.ai.utils.modern_context import ModernContext
from sophie_bot.modules.ai.utils.old_context import OldContext
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
from tests.e2e.helpers import next_group_id, next_user_id


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
    monkeypatch.setattr(OldContext, "add_from_cache", AsyncMock())
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


@pytest.mark.parametrize("reply_topic", [7, 8, None])
@pytest.mark.parametrize("mode", [AIMode.support, AIMode.entertainment])
async def test_modern_proactive_yes_retains_its_question_reference(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
    reply_topic: int | None,
    mode: AIMode,
) -> None:
    chat_tid = next_group_id()
    alice = User(id=next_user_id(), is_bot=False, first_name="Alice", username="private_alice_handle")
    bob = User(id=next_user_id(), is_bot=False, first_name="Bob", username="private_bob_handle")
    users = [await ChatModel.upsert_user(user) for user in (alice, bob)]
    question = "Should we preserve @authored_handle and user_id=9123 literally?"
    now = datetime.now(UTC)
    context = SophieAIToolContext(
        connection=ChatConnection(
            type=ChatType.group, is_connected=False, tid=chat_tid, title="Group", db_model=None,
        ),
        chat_tid=chat_tid,
        chat_iid=PydanticObjectId(),
        services=test_services,
        mode=mode,
        user_text="yes",
    )
    target = MessageType(
        user_id=alice.id,
        message_id=20,
        text="yes",
        created_at=now,
        username=alice.username,
        message_thread_id=7,
        reply_to_message_id=10,
        reply_to_user_id=bob.id,
        reply_to_username=bob.username,
    )
    reconstructed: list[Message] = []

    async def prepare(
        message: Message,
        context: SophieAIToolContext,
        **kwargs: object,
    ) -> ModernContext:
        reconstructed.append(message)
        return await ModernContext.build(
            message,
            context,
            token_budget=100000,
            instructions="Be helpful.",
            runtime_context="",
            request_context=cast(str, kwargs["request_context"]),
        )

    monkeypatch.setattr(proactive_replies, "is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(proactive_replies, "prepare_chatbot_history", prepare)
    history = None
    try:
        if reply_topic is not None:
            await cache_message(
                question,
                chat_tid,
                bob.id,
                10,
                now - timedelta(seconds=10),
                bob.username,
                redis=test_services.redis,
                message_thread_id=reply_topic,
            )
        history = await _build_answer_history(chat_tid, target, services=test_services, context=context)
        assert isinstance(history, ModernContext)
        message = reconstructed[0]
        assert message.chat.type == "group"
        assert message.reply_to_message is not None
        assert message.reply_to_message.message_id == 10
        assert message.reply_to_message.from_user.id == bob.id
        assert message.reply_to_message.from_user.first_name == ""
        assert message.reply_to_message.from_user.username is None
        requests = [
            *history.message_history,
            ModelRequest(parts=[UserPromptPart(content=history.prompt)], instructions=history.instructions),
        ]
        nodes = []
        for request in requests:
            if not isinstance(request, ModelRequest):
                continue
            for part in request.parts:
                if not isinstance(part, UserPromptPart):
                    continue
                contents = [part.content] if isinstance(part.content, str) else part.content
                for content in contents:
                    if isinstance(content, str) and content.startswith("<message "):
                        nodes.append(ElementTree.fromstring(content))
        current = next(node for node in nodes if node.attrib["context"] == "current")
        references = [node for node in nodes if node.attrib["id"] == current.attrib["reply_to"]]
        assert current.findtext("text") == "yes"
        assert len(references) == 1
        reference = references[0]
        assert reference.attrib["context"] == "reference"
        assert reference.attrib["speaker_id"] != current.attrib["speaker_id"]
        assert reference.findtext("text") == (question if reply_topic == 7 else "")
        for node, first_name in ((current, "Alice"), (reference, "Bob")):
            assert node.attrib["id"].startswith("m")
            assert node.attrib["speaker_id"].startswith("u")
            assert "username" not in node.attrib
            if mode == AIMode.entertainment:
                assert node.attrib["first_name"] == first_name
            else:
                assert "first_name" not in node.attrib
    finally:
        if history is not None:
            await history.abort()
        for user in users:
            await user.delete()



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


async def test_missing_reaction_target_does_not_abort_remaining_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat_tid = -1001234567890
    chat = cast(ChatModel, SimpleNamespace(tid=chat_tid, iid="chat-iid"))
    missing_target = TelegramBadRequest(
        method=SetMessageReaction(chat_id=chat_tid, message_id=1),
        message="Bad Request: message to react not found",
    )
    reaction = AsyncMock(side_effect=[missing_target, True])
    services = cast(ApplicationServices, SimpleNamespace(bot=SimpleNamespace(set_message_reaction=reaction)))
    messages = tuple(MessageType(user_id=42, message_id=message_id, text="hello") for message_id in (1, 2, 3))
    decision = ProactiveDecision(
        actions=[
            ProactiveAction(action="react", message_id=1, emoji="👍"),
            ProactiveAction(action="react", message_id=2, emoji="👍"),
            ProactiveAction(action="answer", message_id=3),
        ]
    )
    answer = AsyncMock()
    metric = Mock()
    monkeypatch.setattr(proactive_replies, "track_ai_proactive_event", metric)
    monkeypatch.setattr(proactive_replies, "_answer_message", answer)

    await proactive_replies._execute_actions(
        chat_tid, chat, messages, decision, ProactiveReplySettings(max_reactions=2), services=services
    )

    assert reaction.await_count == 2
    assert [call.kwargs["message_id"] for call in reaction.await_args_list] == [1, 2]
    answer.assert_awaited_once_with(chat_tid, chat, messages[2], services=services)
    metric.assert_any_call("reaction_skipped", {**proactive_replies._METRIC_ATTRIBUTES, "reason": "target_missing"})
    assert metric.call_args_list.count(call("reaction_sent", proactive_replies._METRIC_ATTRIBUTES)) == 1


@pytest.mark.parametrize("description", ["Bad Request: REACTION_INVALID", "Bad Request: not enough rights"])
async def test_unexpected_reaction_bad_request_propagates(description: str) -> None:
    chat_tid = -1001234567890
    failure = TelegramBadRequest(
        method=SetMessageReaction(chat_id=chat_tid, message_id=1),
        message=description,
    )
    reaction = AsyncMock(side_effect=failure)
    services = cast(ApplicationServices, SimpleNamespace(bot=SimpleNamespace(set_message_reaction=reaction)))

    with pytest.raises(TelegramBadRequest) as raised:
        await proactive_replies._react_to_message(
            chat_tid, MessageType(user_id=42, message_id=1, text="hello"), "👍", services=services
        )

    assert raised.value is failure
