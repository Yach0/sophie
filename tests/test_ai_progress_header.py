"""AI progress messages stay plain; completed messages use the simple AI prefix and quota footer."""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any, Protocol, cast
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models import Model
from stfu_tg.ai_md import ai_markdown_to_doc

from sophie_bot.modules.ai.utils.ai_header import (
    AI_CUSTOM_EMOJI_ID,
    AIHeaderStyle,
    ai_credit_header,
    build_ai_header,
    build_ai_message_doc,
)
from sophie_bot.modules.ai.utils.ai_progress import (
    AI_PROGRESS_CUSTOM_EMOJI_IDS,
    AI_PROGRESS_DEFAULT_CUSTOM_EMOJI_ID,
)
from sophie_bot.modules.ai.utils.chatbot_response import (
    build_chatbot_header,
    model_display_name,
    used_tool_labels,
)
from sophie_bot.modules.ai.utils.chatbot_streaming import ChatbotMessageStreamer, StreamMode, build_message_streamer

BATTERY_EMOJI = "🔋"
_RANDOM_EMOJI_ID = AI_PROGRESS_CUSTOM_EMOJI_IDS[-1]


class _IsEnabled(Protocol):
    async def __call__(
        self,
        name: str,
        chat_tid: int | None = None,
        **kwargs: Any,
    ) -> bool: ...


def _flags(**enabled: bool) -> _IsEnabled:
    async def is_enabled(
        name: str,
        chat_tid: int | None = None,
        **kwargs: Any,
    ) -> bool:
        del chat_tid, kwargs
        return enabled.get(name, False)

    return is_enabled


async def _get_value(
    name: str,
    chat_tid: int | None = None,
    **kwargs: Any,
) -> float:
    del name, chat_tid, kwargs
    return 0.5


def _quota(remaining_credits: int = 500, total_credits: int = 1000) -> AsyncMock:
    return AsyncMock(return_value=SimpleNamespace(remaining_credits=remaining_credits, total_credits=total_credits))


def _message() -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=-100123),
        message_id=7,
        message_thread_id=None,
        reply=AsyncMock(return_value=SimpleNamespace(edit_text=AsyncMock())),
    )


def _model() -> Model:
    return cast(Model, SimpleNamespace(model_name="gpt-5.5"))


def _custom_emoji_ids(html: str) -> list[str]:
    return re.findall(r'<tg-emoji emoji-id="(\d+)">', html)


def _assert_plain_progress(text: str) -> None:
    assert AI_CUSTOM_EMOJI_ID not in text
    assert BATTERY_EMOJI not in text


async def _streamer_with_flags(
    message: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    test_redis: object,
    header_style: AIHeaderStyle = "table",
    **flags: bool,
) -> Any:
    monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_streaming.is_enabled", _flags(**flags))
    monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_streaming.get_value", _get_value)
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.chatbot_streaming.random_ai_progress_custom_emoji_id",
        lambda: _RANDOM_EMOJI_ID,
    )
    return await build_message_streamer(
        cast(Message, message),
        _model(),
        False,
        header_style,
        redis=test_redis,
    )


@pytest.mark.asyncio
async def test_thinking_placeholder_is_not_a_table_row(
    monkeypatch: pytest.MonkeyPatch, test_redis: object, test_services: object
) -> None:
    quota = _quota()
    monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_response.get_quota_info", quota)

    message = _message()
    streamer = await _streamer_with_flags(
        message,
        monkeypatch,
        test_redis,
        ai_chatbot_thinking_message=True,
        ai_chatbot_streaming=True,
    )

    assert streamer is not None
    _assert_plain_progress(message.reply.await_args.args[0])
    quota.assert_not_awaited()


@pytest.mark.asyncio
async def test_streaming_placeholder_shows_no_battery(
    monkeypatch: pytest.MonkeyPatch, test_redis: object, test_services: object
) -> None:
    """Streaming without the thinking placeholder still must not spend a battery reading early."""
    quota = _quota()
    monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_response.get_quota_info", quota)

    message = _message()
    streamer = await _streamer_with_flags(
        message,
        monkeypatch,
        test_redis,
        ai_chatbot_streaming=True,
    )

    assert streamer is not None
    _assert_plain_progress(message.reply.await_args.args[0])
    quota.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_header_keeps_initial_progress_placeholder(
    monkeypatch: pytest.MonkeyPatch, test_redis: object, test_services: object
) -> None:
    message = _message()
    streamer = await _streamer_with_flags(
        message,
        monkeypatch,
        test_redis,
        header_style="disable",
        ai_chatbot_streaming=True,
    )

    assert streamer is not None
    initial_text = message.reply.await_args.args[0]
    assert initial_text
    _assert_plain_progress(initial_text)


@pytest.mark.asyncio
async def test_progress_updates_stay_plain(
    monkeypatch: pytest.MonkeyPatch, test_redis: object, test_services: object
) -> None:
    quota = _quota()
    monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_response.get_quota_info", quota)

    response_message = SimpleNamespace(edit_text=AsyncMock())
    streamer = ChatbotMessageStreamer(
        source_message=cast(Message, _message()),
        header=cast(Any, "Initial"),
        mode=StreamMode.HTML_EDIT,
        throttle_seconds=0,
        redis=test_redis,
    )
    streamer.response_message = cast(Message, response_message)

    await streamer.update_retrying(1, 5)

    edited_text = response_message.edit_text.await_args.kwargs["text"]
    assert "(Retrying 1/5...)" in edited_text
    _assert_plain_progress(edited_text)
    quota.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [StreamMode.HTML_EDIT, StreamMode.RICH_EDIT])
async def test_retrying_draft_uses_the_configured_simple_layout(
    mode: StreamMode,
    test_redis: object,
) -> None:
    """A retry/failover edit must keep one simple prefix and omit the completed footer."""
    response_message = SimpleNamespace(
        chat=SimpleNamespace(id=-100123),
        message_id=8,
        edit_text=AsyncMock(),
        bot=SimpleNamespace(edit_message_text=AsyncMock()),
    )
    streamer = ChatbotMessageStreamer(
        source_message=cast(Message, _message()),
        header=cast(Any, "Initial"),
        mode=mode,
        throttle_seconds=0,
        header_style="simple",
        redis=test_redis,
    )
    streamer.response_message = cast(Message, response_message)
    await streamer.stream("The fallback answer")
    await streamer.update_retrying(1, 5)

    if mode == StreamMode.RICH_EDIT:
        assert response_message.bot.edit_message_text.await_count == 2
        rendered_html = response_message.bot.edit_message_text.await_args.kwargs["rich_message"].html
    else:
        assert response_message.edit_text.await_count == 2
        rendered_html = response_message.edit_text.await_args.kwargs["text"]
    assert "(Retrying 1/5...)" in rendered_html
    assert rendered_html.count("The fallback answer") == 1
    assert BATTERY_EMOJI not in rendered_html
    if mode == StreamMode.RICH_EDIT:
        assert _custom_emoji_ids(rendered_html).count(AI_CUSTOM_EMOJI_ID) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [StreamMode.HTML_EDIT, StreamMode.RICH_EDIT])
async def test_streaming_and_retrying_drafts_respect_disabled_layout(
    mode: StreamMode,
    test_redis: object,
) -> None:
    """Disabled headers must not render progress content in either edit mode."""
    response_message = SimpleNamespace(
        chat=SimpleNamespace(id=-100123),
        message_id=8,
        edit_text=AsyncMock(),
        bot=SimpleNamespace(edit_message_text=AsyncMock()),
    )
    streamer = ChatbotMessageStreamer(
        source_message=cast(Message, _message()),
        header=cast(Any, "Initial"),
        mode=mode,
        throttle_seconds=0,
        header_style="disable",
        redis=test_redis,
    )
    streamer.response_message = cast(Message, response_message)
    await streamer.stream("The fallback answer")
    await streamer.update_retrying(1, 5)

    if mode == StreamMode.RICH_EDIT:
        rendered_html = response_message.bot.edit_message_text.await_args.kwargs["rich_message"].html
    else:
        rendered_html = response_message.edit_text.await_args.kwargs["text"]
    assert rendered_html == "The fallback answer"


@pytest.mark.asyncio
async def test_placeholder_emoji_stays_the_same_on_every_edit(
    monkeypatch: pytest.MonkeyPatch, test_redis: object, test_services: object
) -> None:
    """Without the random-emoji flag the placeholder keeps one emoji instead of flickering."""
    message = _message()
    streamer = await _streamer_with_flags(
        message,
        monkeypatch,
        test_redis,
        ai_chatbot_thinking_message=True,
        ai_chatbot_streaming=True,
    )

    assert streamer is not None
    streamer.throttle_seconds = 0
    await streamer.update_retrying(1, 5)

    initial_emoji_ids = _custom_emoji_ids(message.reply.await_args.args[0])
    edited_emoji_ids = _custom_emoji_ids(streamer.response_message.edit_text.await_args.kwargs["text"])
    assert initial_emoji_ids == [AI_PROGRESS_DEFAULT_CUSTOM_EMOJI_ID]
    assert edited_emoji_ids == initial_emoji_ids


@pytest.mark.asyncio
async def test_random_emoji_flag_applies_without_the_thinking_placeholder(
    monkeypatch: pytest.MonkeyPatch, test_redis: object, test_services: object
) -> None:
    """The flag picks the placeholder emoji; whether the thinking text is shown is a separate flag."""
    message = _message()
    streamer = await _streamer_with_flags(
        message,
        monkeypatch,
        test_redis,
        ai_chatbot_streaming=True,
        ai_chatbot_random_emoji=True,
    )

    assert streamer is not None
    streamer.throttle_seconds = 0
    await streamer.update_retrying(1, 5)

    assert _custom_emoji_ids(message.reply.await_args.args[0]) == [_RANDOM_EMOJI_ID]
    assert _custom_emoji_ids(streamer.response_message.edit_text.await_args.kwargs["text"]) == [_RANDOM_EMOJI_ID]


@pytest.mark.asyncio
async def test_finished_reply_uses_custom_ai_emoji_and_battery_footer(
    monkeypatch: pytest.MonkeyPatch, test_redis: object, test_services: object
) -> None:
    monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_response.get_quota_info", _quota())

    header = await build_chatbot_header(cast(Any, "chat-iid"), redis=test_redis)

    text = build_ai_message_doc(header, "Hello").to_rich()
    assert text.startswith(f'<tg-emoji emoji-id="{AI_CUSTOM_EMOJI_ID}">✨</tg-emoji> Hello')
    assert "<br><tg-emoji" in text
    assert BATTERY_EMOJI in text
    assert "50%" in text
    assert "<table" not in text


def test_simple_header_is_inline_and_places_battery_on_next_line() -> None:
    header = build_ai_header("simple", ai_credit_header(50))

    assert header is not None
    text = build_ai_message_doc(header, "Hello\nSecond line").to_rich()
    assert text.startswith(f'<tg-emoji emoji-id="{AI_CUSTOM_EMOJI_ID}">✨</tg-emoji> Hello\nSecond line')
    assert "<br><tg-emoji" in text
    assert text.endswith(" 50%")
    assert "<table" not in text


def test_simple_header_renders_first_markdown_paragraph_inline() -> None:
    header = build_ai_header("simple", ai_credit_header(50))

    assert header is not None
    text = build_ai_message_doc(header, ai_markdown_to_doc("Hello *world*.\n\nSecond paragraph.")).to_rich()
    assert text.startswith(f'<tg-emoji emoji-id="{AI_CUSTOM_EMOJI_ID}">✨</tg-emoji> Hello <i>world</i>.')
    assert "<p>" not in text
    assert "\nSecond paragraph.<br>" in text


def test_used_tool_categories_render_before_reply_body() -> None:
    labels = used_tool_labels(
        [
            ModelResponse(parts=[ToolCallPart(tool_name="kagi_search", args={})]),
            ModelResponse(parts=[ToolCallPart(tool_name="tinyfish_search", args={})]),
            ModelResponse(parts=[ToolCallPart(tool_name="get_note_content", args={})]),
        ]
    )
    header = build_ai_header("simple", ai_credit_header(45))

    text = build_ai_message_doc(header, "Reply here", tool_labels=labels).to_rich()

    assert labels == ("🔍 Internet Search", "📝 Notes")
    assert text.startswith(
        f'<tg-emoji emoji-id="{AI_CUSTOM_EMOJI_ID}">✨</tg-emoji> '
        "(🔍 Internet Search, 📝 Notes) Reply here"
    )
    assert "<br><tg-emoji" in text
    assert text.endswith(" 45%")


def test_model_name_is_optional_after_battery_percentage() -> None:
    assert ai_credit_header(45).to_rich().endswith(" 45%")
    assert ai_credit_header(45, "Gemini 5").to_rich().endswith(" 45% (Gemini 5)")
    assert model_display_name(_model()) == "GPT 5.5"


def test_disabled_header_leaves_only_the_body() -> None:
    header = build_ai_header("disable", ai_credit_header(50))

    assert header is None
    assert build_ai_message_doc(header, "Hello").to_html() == "Hello"
