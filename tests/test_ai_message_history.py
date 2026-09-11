from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from sophie_bot.modules.ai.utils import message_history
from sophie_bot.modules.ai.utils.cache_messages import MessageType, cache_message
from sophie_bot.modules.ai.utils.message_history import AIMessageHistory, AIUserMessageFormatter


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
        message_history.ChatModel, "get_by_tid", AsyncMock(return_value=SimpleNamespace(first_name_or_title="Alice"))
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
    monkeypatch.setattr(message_history.ChatModel, "get_by_tid", AsyncMock(return_value=None))
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
    monkeypatch.setattr(message_history.ChatModel, "get_by_tid", AsyncMock(return_value=None))
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
    monkeypatch.setattr(message_history.ChatModel, "get_by_tid", AsyncMock(return_value=None))
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
    monkeypatch.setattr(message_history.ChatModel, "get_by_tid", AsyncMock(return_value=None))
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
