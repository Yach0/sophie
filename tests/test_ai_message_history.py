from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiogram import Router
from aiogram.types import Chat, Message, PhotoSize, TelegramObject, User
from fakeredis import FakeAsyncRedis
from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.chat_admin import ChatAdminModel
from sophie_bot.modules.ai.handlers.ai_cmd import AiCmd
from sophie_bot.modules.ai.handlers.pm import AiPmHandle
from sophie_bot.modules.ai.handlers.reply import AiReplyHandler
from sophie_bot.modules.ai.middlewares.cache_bot_messages import CacheBotMessagesMiddleware
from sophie_bot.modules.ai.utils import message_history
from sophie_bot.modules.ai.utils.cache_messages import MessageType, cache_message
from sophie_bot.modules.ai.utils.message_history import AIMessageHistory, AIUserMessageFormatter
from sophie_bot.modules.utils_.admin import check_user_admin_permissions
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.handlers import SophieMessageHandler
from tests.e2e.helpers import grant_admin, next_group_id, next_user_id


@pytest.mark.asyncio
@pytest.mark.parametrize("handler_type", [AiCmd, AiReplyHandler, AiPmHandle])
async def test_chatbot_response_caches_only_the_answer(
    handler_type: type[SophieMessageHandler], monkeypatch: pytest.MonkeyPatch, test_redis: object
) -> None:
    router = Router()
    handler_type.register(router)
    sent = Message(
        message_id=51,
        date=datetime.now(UTC),
        chat=Chat(id=-100123, type="supergroup"),
        text="✨ (🙂 Search, 🙂 Memory) Answer\n🔋 50%",
    )
    cached = AsyncMock()
    monkeypatch.setattr("sophie_bot.modules.ai.middlewares.cache_bot_messages.cache_message", cached)

    async def reply(_event: TelegramObject, _data: dict[str, Any]) -> Message:
        await cached("Answer")
        return sent

    result = await CacheBotMessagesMiddleware()(
        reply,
        sent,
        {
            "handler": router.message.handlers[-1],
            "context": SimpleNamespace(event_chat=SimpleNamespace(tid=sent.chat.id)),
            "ai_capabilities": SimpleNamespace(message_cache=True),
            "services": SimpleNamespace(redis=test_redis),
        },
    )

    assert result is sent
    cached.assert_awaited_once_with("Answer")


def test_user_message_formatter_localizes_and_sanitizes_reply_title(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(message_history, "_", lambda text: "Antwort auf" if text == "reply to" else text)

    rendered = AIUserMessageFormatter.user_message(
        "hello",
        name="<Alice!>",
        reply_to_user="Bob@example",
    )

    assert rendered == "Alice (Antwort auf Bobexample): hello"


@pytest.mark.asyncio
async def test_cached_history_keeps_reply_title(
    monkeypatch: pytest.MonkeyPatch, test_redis: object, test_services: object
) -> None:
    cached = MessageType(
        user_id=1,
        message_id=2,
        text="hello",
        reply_to_user_id=3,
        reply_to_username="Bob",
    )
    monkeypatch.setattr(
        ChatModel, "get_by_tid", AsyncMock(return_value=SimpleNamespace(first_name_or_title="Alice"))
    )
    monkeypatch.setattr(message_history, "_admin_context_name", AsyncMock(return_value="Alice"))

    history = AIMessageHistory(services=test_services)
    transformed = await history._cache_transform_msg(10, cached)
    context_line = await history._format_context_line(10, cached)

    assert isinstance(transformed, ModelRequest)
    assert transformed.parts[0].content == "Alice (reply to Bob): hello"
    assert context_line == "Alice (reply to Bob): hello"


@pytest.mark.asyncio
async def test_cached_ai_history_uses_shared_message_text_representation(
    monkeypatch: pytest.MonkeyPatch, test_redis: object, test_services: object
) -> None:
    cached = MessageType(user_id=message_history.CONFIG.bot_id, message_id=2, text="stored body")
    monkeypatch.setattr(ChatModel, "get_by_tid", AsyncMock(return_value=None))
    monkeypatch.setattr(
        message_history,
        "message_text",
        lambda message: "✨ AI | Help 📖 | 🔋 80%\nstored body",
    )

    transformed = await AIMessageHistory(services=test_services)._cache_transform_msg(10, cached)

    assert isinstance(transformed, ModelResponse)
    assert transformed.parts[0].content == "stored body"


@pytest.mark.asyncio
async def test_cached_foreign_bot_message_is_reference_only_context(
    monkeypatch: pytest.MonkeyPatch, test_redis: object, test_services: object
) -> None:
    monkeypatch.setattr(ChatModel, "get_by_tid", AsyncMock(return_value=None))
    await cache_message(
        "Dergbot chatter",
        10,
        42,
        2,
        datetime.now(UTC),
        "Dergbot",
        is_bot=True,
        redis=test_redis,
    )

    history = AIMessageHistory(services=test_services)
    await history.add_from_cache(10, fold_background=True)

    assert history.message_history == []
    assert history.context_lines == ["Unknown: Dergbot chatter"]


@pytest.mark.asyncio
async def test_cached_relevant_foreign_bot_message_is_an_assistant_response(
    monkeypatch: pytest.MonkeyPatch, test_redis: object, test_services: object
) -> None:
    monkeypatch.setattr(ChatModel, "get_by_tid", AsyncMock(return_value=None))
    await cache_message(
        "Dergbot answer",
        10,
        42,
        2,
        datetime.now(UTC),
        "Dergbot",
        is_bot=True,
        handled_by_ai=True,
        redis=test_redis,
    )

    history = AIMessageHistory(services=test_services)
    await history.add_from_cache(10, fold_background=True)

    assert len(history.message_history) == 1
    response = history.message_history[0]
    assert isinstance(response, ModelResponse)
    assert response.parts[0].content == "Dergbot answer"


@pytest.mark.asyncio
async def test_next_generation_replays_the_authoritative_sophie_answer(
    monkeypatch: pytest.MonkeyPatch,
    test_redis: object,
    test_services: object,
) -> None:
    monkeypatch.setattr(ChatModel, "get_by_tid", AsyncMock(return_value=None))
    answer_time = datetime.now(UTC)
    await cache_message(
        "prior answer",
        10,
        message_history.CONFIG.bot_id,
        20,
        answer_time,
        "Sophie",
        reply_to_message_id=19,
        reply_to_user_id=1,
        redis=test_redis,
    )

    history = AIMessageHistory(services=test_services)
    await history.add_from_cache(10)

    assert len(history.message_history) == 1
    cached_answer = history.message_history[0]
    assert isinstance(cached_answer, ModelResponse)
    assert cached_answer.parts[0].content == "prior answer"


@pytest.mark.asyncio
async def test_cached_reply_target_is_not_added_to_prompt_twice(
    monkeypatch: pytest.MonkeyPatch,
    test_redis: object,
    test_services: object,
) -> None:
    answer_time = datetime.now(UTC)
    await cache_message(
        "prior answer",
        10,
        message_history.CONFIG.bot_id,
        20,
        answer_time,
        "Sophie",
        is_bot=True,
        redis=test_redis,
    )
    monkeypatch.setattr(ChatModel, "get_by_tid", AsyncMock(return_value=None))
    monkeypatch.setattr(message_history, "_admin_context_name", AsyncMock(return_value="Alice"))
    chat = Chat(id=10, type="group", title="Test chat")
    sophie = User(id=message_history.CONFIG.bot_id, is_bot=True, first_name="Sophie")
    alice = User(id=1, is_bot=False, first_name="Alice")
    prior_answer = Message(
        message_id=20,
        date=answer_time,
        chat=chat,
        from_user=sophie,
        text="✨ prior answer\n🔋 90%",
    )
    follow_up = Message(
        message_id=21,
        date=answer_time,
        chat=chat,
        from_user=alice,
        text="follow up",
        reply_to_message=prior_answer,
    )

    history = AIMessageHistory(services=test_services)
    await history.add_from_cache(10)
    await history.add_from_message(follow_up)

    assert len(history.message_history) == 1
    assert history.prompt == ["Alice (reply to Sophie): follow up"]


def test_message_history_adds_system_custom_and_debug_output(
    test_services: object,
) -> None:
    history = AIMessageHistory(services=test_services)

    history.add_system("system prompt")
    history.add_custom("user prompt", name="Tester")
    history.prompt = ["current prompt"]
    debug_doc = history.history_debug()

    rendered = str(debug_doc)
    assert "system prompt" in rendered
    assert "Tester: user prompt" in rendered
    assert "current prompt" in rendered


def test_message_history_moderation_extracts_text_roles(
    test_services: object,
) -> None:
    history = AIMessageHistory(services=test_services)
    history.add_system("system prompt")
    history.add_custom("user prompt", name="Tester")
    history.message_history.append(ModelResponse(parts=[TextPart(content="assistant reply")]))
    history.prompt = ["current prompt"]

    assert history.to_moderation == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "Tester: user prompt"},
        {"role": "assistant", "content": "assistant reply"},
        {"role": "user", "content": "current prompt"},
    ]


def _cached_message(text: str, *, handled_by_ai: bool = False, has_ai_command: bool = False) -> MessageType:
    return MessageType(
        user_id=1,
        message_id=1,
        text=text,
        handled_by_ai=handled_by_ai,
        has_ai_command=has_ai_command,
    )


def test_is_ai_dialogue_classifies_background_vs_conversation() -> None:
    assert AIMessageHistory._is_ai_dialogue(_cached_message("hi", handled_by_ai=True)) is True
    assert AIMessageHistory._is_ai_dialogue(_cached_message("/ai hello", has_ai_command=True)) is True
    assert AIMessageHistory._is_ai_dialogue(_cached_message("just chatting")) is False


def test_fold_trailing_requests_moves_dangling_user_turns_to_context(
    test_services: object,
) -> None:
    history = AIMessageHistory(services=test_services)
    history.message_history = [
        ModelResponse(parts=[TextPart(content="Sophie reply")]),
        ModelRequest(parts=[UserPromptPart(content="Alice: first")]),
        ModelRequest(parts=[UserPromptPart(content="Bob: second")]),
    ]

    history._fold_trailing_requests()

    # The bot reply stays as the last conversation turn; dangling user turns become context in order.
    assert len(history.message_history) == 1
    remaining = history.message_history[0]
    assert isinstance(remaining, ModelResponse)
    assert remaining.parts[0].content == "Sophie reply"
    assert history.context_lines == ["Alice: first", "Bob: second"]


def test_apply_context_block_prepends_reference_only_context(
    test_services: object,
) -> None:
    history = AIMessageHistory(services=test_services)
    history.context_lines = ["Alice: first", "Bob: second"]
    history.prompt = ["Carol: latest question"]

    history.apply_context_block()

    assert history.context_lines == []
    assert len(history.prompt) == 2
    context_block = history.prompt[0]
    assert isinstance(context_block, str)
    assert "Alice: first" in context_block
    assert "Bob: second" in context_block
    assert history.prompt[1] == "Carol: latest question"


def test_apply_context_block_is_noop_without_context(
    test_services: object,
) -> None:
    history = AIMessageHistory(services=test_services)
    history.prompt = ["Carol: latest question"]

    history.apply_context_block()

    assert history.prompt == ["Carol: latest question"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fold_background", [False, True])
async def test_history_build_deduplicates_chat_reads_across_cache_replies_media_and_tools(
    monkeypatch: pytest.MonkeyPatch,
    test_redis: FakeAsyncRedis,
    test_services: ApplicationServices,
    fold_background: bool,
) -> None:
    group = Chat(id=next_group_id(), type="supergroup", title="History lookups")
    alice = User(id=next_user_id(), is_bot=False, first_name="Alice")
    bob = User(id=next_user_id(), is_bot=False, first_name="Bob")
    missing_user_tid = next_user_id()
    await ChatModel.upsert_group(group)
    await ChatModel.upsert_user(alice)
    await ChatModel.upsert_user(bob)
    await grant_admin(group.id, alice.id, creator=True, custom_title="Founder")
    await grant_admin(group.id, bob.id, custom_title="Helper")
    created_at = datetime.now(UTC) - timedelta(minutes=1)
    rows = [
        (alice.id, "first", True, False),
        (bob.id, "second", True, False),
        (missing_user_tid, "missing", True, False),
        (message_history.CONFIG.bot_id, "answer", True, True),
        (alice.id, "background", False, False),
        (message_history.CONFIG.bot_id, "final answer", True, True),
    ]
    for message_id, (user_tid, text, handled_by_ai, is_bot) in enumerate(rows, start=1):
        await cache_message(
            text,
            group.id,
            user_tid,
            message_id,
            created_at + timedelta(seconds=message_id),
            None,
            handled_by_ai=handled_by_ai,
            is_bot=is_bot,
            reply_to_user_id=bob.id if message_id == 1 else None,
            reply_to_username="Bob cached" if message_id == 1 else None,
            redis=test_redis,
        )
    tool_call = ModelResponse(
        parts=[ToolCallPart(tool_name="search", args={"query": "history"}, tool_call_id="history-search")]
    )
    tool_return = ModelRequest(
        parts=[ToolReturnPart(tool_name="search", content="found", tool_call_id="history-search")]
    )
    chat_reads = AsyncMock(wraps=ChatModel.get_pymongo_collection().find_one)
    monkeypatch.setattr(ChatModel.get_pymongo_collection(), "find_one", chat_reads)
    admin_reads = AsyncMock(wraps=ChatAdminModel.get_pymongo_collection().find_one)
    monkeypatch.setattr(ChatAdminModel.get_pymongo_collection(), "find_one", admin_reads)
    download = AsyncMock(side_effect=[BytesIO(b"history-image"), BytesIO(b"history-image")])
    monkeypatch.setattr(test_services.bot, "download", download)

    history = AIMessageHistory(services=test_services)
    await history.add_from_cache(
        group.id,
        fold_background=fold_background,
        tool_exchanges={6: [tool_call, tool_return]},
    )
    replied_message = Message(
        message_id=90,
        date=created_at,
        chat=group,
        from_user=bob.model_copy(update={"first_name": "Bob live"}),
        text="uncached reply",
    )
    current_message = Message(
        message_id=91,
        date=datetime.now(UTC),
        chat=group,
        from_user=alice.model_copy(update={"first_name": "Alice live"}),
        caption="question",
        reply_to_message=replied_message,
        photo=[PhotoSize(file_id="history-photo", file_unique_id="history-photo-id", width=16, height=16)],
    )
    await history.add_from_message(current_message)
    await history.add_from_message(current_message, allow_reply_messages=False, disable_name=True)

    expected_turns = [
        "Alice [Owner - Founder] (reply to Bob cached): first",
        "Bob [Admin - Helper]: second",
        "Unknown: missing",
        "answer",
    ]
    if not fold_background:
        expected_turns.append("Alice [Owner - Founder]: background")
    expected_turns.append("final answer")
    rendered_turns = [
        part.content
        for turn in history.message_history
        for part in turn.parts
        if isinstance(part, (UserPromptPart, TextPart))
    ]
    assert rendered_turns == expected_turns
    assert history.context_lines == (["Alice [Owner - Founder]: background"] if fold_background else [])
    assert history.message_history[-3:-1] == [tool_call, tool_return]
    assert history.prompt[0] == "Bob live [Admin - Helper]: uncached reply"
    assert history.prompt[1] == "Alice live [Owner - Founder] (reply to Bob live): question"
    assert isinstance(history.prompt[2], BinaryContent)
    assert history.prompt[2].data == b"history-image"
    assert history.prompt[3] == "question"
    assert isinstance(history.prompt[4], BinaryContent)
    assert download.await_count == 2
    assert Counter(call.kwargs["filter"]["chat_id"] for call in chat_reads.await_args_list) == Counter(
        {group.id: 1, alice.id: 1, bob.id: 1, missing_user_tid: 1, message_history.CONFIG.bot_id: 1}
    )
    assert admin_reads.await_count == 2


@pytest.mark.asyncio
async def test_new_history_build_reloads_changed_and_previously_missing_chat_models(
    monkeypatch: pytest.MonkeyPatch,
    test_redis: FakeAsyncRedis,
    test_services: ApplicationServices,
) -> None:
    group = Chat(id=next_group_id(), type="supergroup", title="Fresh history")
    alice = User(id=next_user_id(), is_bot=False, first_name="Alice")
    missing = User(id=next_user_id(), is_bot=False, first_name="New user")
    await ChatModel.upsert_group(group)
    await ChatModel.upsert_user(alice)
    for message_id, user_tid in enumerate([alice.id, missing.id, alice.id, missing.id], start=1):
        await cache_message(
            f"message {message_id}",
            group.id,
            user_tid,
            message_id,
            datetime.now(UTC) - timedelta(seconds=10 - message_id),
            None,
            redis=test_redis,
        )
    chat_reads = AsyncMock(wraps=ChatModel.get_pymongo_collection().find_one)
    monkeypatch.setattr(ChatModel.get_pymongo_collection(), "find_one", chat_reads)
    first_history = AIMessageHistory(services=test_services)
    await first_history.add_from_cache(group.id)

    assert [entry["content"] for entry in first_history.to_moderation] == [
        "Alice: message 1",
        "Unknown: message 2",
        "Alice: message 3",
        "Unknown: message 4",
    ]
    assert chat_reads.await_count == 3
    await ChatModel.upsert_user(alice.model_copy(update={"first_name": "Alicia"}))
    await ChatModel.upsert_user(missing)
    chat_reads.reset_mock()
    second_history = AIMessageHistory(services=test_services)
    await second_history.add_from_cache(group.id)

    assert [entry["content"] for entry in second_history.to_moderation] == [
        "Alicia: message 1",
        "New user: message 2",
        "Alicia: message 3",
        "New user: message 4",
    ]
    assert chat_reads.await_count == 3


@pytest.mark.asyncio
async def test_private_history_keeps_names_and_replies_without_group_role_lookups(
    monkeypatch: pytest.MonkeyPatch,
    test_redis: FakeAsyncRedis,
    test_services: ApplicationServices,
) -> None:
    alice = User(id=next_user_id(), is_bot=False, first_name="Alice")
    bob = User(id=next_user_id(), is_bot=False, first_name="Bob")
    private_chat = Chat(id=alice.id, type="private", first_name="Alice")
    await ChatModel.upsert_user(alice)
    await ChatModel.upsert_user(bob)
    await grant_admin(alice.id, alice.id, creator=True, custom_title="Not a group")
    chat_reads = AsyncMock(wraps=ChatModel.get_pymongo_collection().find_one)
    monkeypatch.setattr(ChatModel.get_pymongo_collection(), "find_one", chat_reads)
    admin_reads = AsyncMock(wraps=ChatAdminModel.get_pymongo_collection().find_one)
    monkeypatch.setattr(ChatAdminModel.get_pymongo_collection(), "find_one", admin_reads)
    history = AIMessageHistory(services=test_services)
    await cache_message(
        "cached",
        private_chat.id,
        alice.id,
        1,
        datetime.now(UTC) - timedelta(seconds=1),
        None,
        redis=test_redis,
    )
    await history.add_from_cache(private_chat.id)
    replied_message = Message(
        message_id=2, date=datetime.now(UTC), chat=private_chat, from_user=bob, text="reply target"
    )
    await history.add_from_message(
        Message(
            message_id=3,
            date=datetime.now(UTC),
            chat=private_chat,
            from_user=alice,
            text="question",
            reply_to_message=replied_message,
        )
    )

    assert [entry["content"] for entry in history.to_moderation] == [
        "Alice: cached",
        "Bob: reply target",
        "Alice (reply to Bob): question",
    ]
    assert chat_reads.await_count == 1
    assert admin_reads.await_count == 0


@pytest.mark.asyncio
async def test_new_history_build_reloads_admin_titles_promotions_and_revocations(
    monkeypatch: pytest.MonkeyPatch,
    test_redis: FakeAsyncRedis,
    test_services: ApplicationServices,
) -> None:
    group = Chat(id=next_group_id(), type="supergroup", title="Fresh admin history")
    alice = User(id=next_user_id(), is_bot=False, first_name="Alice")
    bob = User(id=next_user_id(), is_bot=False, first_name="Bob")
    carol = User(id=next_user_id(), is_bot=False, first_name="Carol")
    await ChatModel.upsert_group(group)
    for user in [alice, bob, carol]:
        await ChatModel.upsert_user(user)
    alice_admin = await grant_admin(group.id, alice.id, creator=True, custom_title="Founder")
    bob_admin = await grant_admin(group.id, bob.id, custom_title="Helper")
    for message_id, user in enumerate([alice, bob, carol, alice, bob, carol], start=1):
        await cache_message(
            f"message {message_id}",
            group.id,
            user.id,
            message_id,
            datetime.now(UTC) - timedelta(seconds=10 - message_id),
            None,
            redis=test_redis,
        )
    admin_reads = AsyncMock(wraps=ChatAdminModel.get_pymongo_collection().find_one)
    monkeypatch.setattr(ChatAdminModel.get_pymongo_collection(), "find_one", admin_reads)
    first_history = AIMessageHistory(services=test_services)
    await first_history.add_from_cache(group.id)

    assert [entry["content"] for entry in first_history.to_moderation] == [
        "Alice [Owner - Founder]: message 1",
        "Bob [Admin - Helper]: message 2",
        "Carol: message 3",
        "Alice [Owner - Founder]: message 4",
        "Bob [Admin - Helper]: message 5",
        "Carol: message 6",
    ]
    assert admin_reads.await_count == 3
    alice_admin.member = alice_admin.member.model_copy(update={"custom_title": "New title"})
    await alice_admin.save()
    await bob_admin.delete()
    assert await check_user_admin_permissions(group.id, bob.id) is False
    await grant_admin(group.id, carol.id, custom_title="Promoted")
    admin_reads.reset_mock()
    second_history = AIMessageHistory(services=test_services)
    await second_history.add_from_cache(group.id)

    assert [entry["content"] for entry in second_history.to_moderation] == [
        "Alice [Owner - New title]: message 1",
        "Bob: message 2",
        "Carol [Admin - Promoted]: message 3",
        "Alice [Owner - New title]: message 4",
        "Bob: message 5",
        "Carol [Admin - Promoted]: message 6",
    ]
    assert admin_reads.await_count == 3


@pytest.mark.asyncio
async def test_history_admin_labels_are_scoped_by_chat_and_do_not_cache_live_names(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    first_group = Chat(id=next_group_id(), type="supergroup", title="First group")
    second_group = Chat(id=next_group_id(), type="supergroup", title="Second group")
    alice = User(id=next_user_id(), is_bot=False, first_name="Alice")
    await ChatModel.upsert_group(first_group)
    await ChatModel.upsert_group(second_group)
    await ChatModel.upsert_user(alice)
    await grant_admin(first_group.id, alice.id, custom_title="Moderator")
    await grant_admin(second_group.id, alice.id, creator=True)
    admin_reads = AsyncMock(wraps=ChatAdminModel.get_pymongo_collection().find_one)
    monkeypatch.setattr(ChatAdminModel.get_pymongo_collection(), "find_one", admin_reads)
    history = AIMessageHistory(services=test_services)
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=first_group,
        from_user=alice,
        text="first",
    )
    await history.add_from_message(message)
    await history.add_from_message(
        message.model_copy(
            update={"message_id": 2, "from_user": alice.model_copy(update={"first_name": "Alicia"}), "text": "renamed"}
        )
    )
    await history.add_from_message(message.model_copy(update={"message_id": 3, "chat": second_group, "text": "second"}))

    assert history.prompt == [
        "Alice [Admin - Moderator]: first",
        "Alicia [Admin - Moderator]: renamed",
        "Alice [Owner]: second",
    ]
    assert admin_reads.await_count == 2
