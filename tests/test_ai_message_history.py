from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from sophie_bot.modules.ai.utils import message_history
from sophie_bot.modules.ai.utils.cache_messages import MessageType
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
async def test_cached_history_keeps_reply_title(monkeypatch: pytest.MonkeyPatch) -> None:
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

    transformed = await AIMessageHistory._cache_transform_msg(10, cached)
    history = AIMessageHistory()
    context_line = await history._format_context_line(10, cached)

    assert isinstance(transformed, ModelRequest)
    assert transformed.parts[0].content == "Alice (reply to Bob): hello"
    assert context_line == "Alice (reply to Bob): hello"


def test_message_history_adds_system_custom_and_debug_output() -> None:
    history = AIMessageHistory()

    history.add_system("system prompt")
    history.add_custom("user prompt", name="Tester")
    history.prompt = ["current prompt"]
    debug_doc = history.history_debug()

    rendered = str(debug_doc)
    assert "system prompt" in rendered
    assert "Tester: user prompt" in rendered
    assert "current prompt" in rendered


def test_message_history_moderation_extracts_text_roles() -> None:
    history = AIMessageHistory()
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


def test_fold_trailing_requests_moves_dangling_user_turns_to_context() -> None:
    history = AIMessageHistory()
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


def test_apply_context_block_prepends_reference_only_context() -> None:
    history = AIMessageHistory()
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


def test_apply_context_block_is_noop_without_context() -> None:
    history = AIMessageHistory()
    history.prompt = ["Carol: latest question"]

    history.apply_context_block()

    assert history.prompt == ["Carol: latest question"]
