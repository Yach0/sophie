"""Streaming replies show an animated progress marker; final replies show a static AI marker and quota."""

from __future__ import annotations

import re
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message, PhotoSize, Voice
from pydantic_ai.messages import BinaryContent, ModelResponse, ToolCallPart
from pydantic_ai.models import Model
from stfu_tg.ai_md import ai_markdown_to_doc

from sophie_bot.modules.ai.utils.ai_header import (
    AI_CHATBOT_CUSTOM_EMOJI_ID,
    AI_CUSTOM_EMOJI_ID,
    AI_GENERATING_EMOJI_ID,
    AI_PROGRESS_LINE_EMOJI_IDS,
    ai_credit_header,
    build_ai_header,
    build_ai_message_doc,
)
from sophie_bot.modules.ai.utils.ai_tool import AI_TOOLS_BY_NAME
from sophie_bot.modules.ai.utils.chatbot_response import (
    build_chatbot_header,
    build_reply_doc,
    model_display_name,
    used_tool_labels,
)
from sophie_bot.modules.ai.utils.chatbot_streaming import ChatbotMessageStreamer, build_message_streamer
from sophie_bot.modules.ai.utils.message_history import AIMessageHistory
from sophie_bot.utils.feature_flags import delete_override, set_enabled

BATTERY_EMOJI = "🔋"
ANIMATED_LINE_IDS = AI_PROGRESS_LINE_EMOJI_IDS


async def _get_value(
    name: str,
    chat_tid: int | None = None,
    **kwargs: Any,
) -> float:
    del name, chat_tid, kwargs
    return 0.5


def _quota(remaining_credits: int = 500, total_credits: int = 1000) -> AsyncMock:
    return AsyncMock(return_value=SimpleNamespace(remaining_credits=remaining_credits, total_credits=total_credits))


def _response_message() -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=-100123),
        message_id=8,
        bot=SimpleNamespace(edit_message_text=AsyncMock()),
    )


def _message() -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=-100123),
        message_id=7,
        message_thread_id=None,
        bot=SimpleNamespace(send_rich_message=AsyncMock(return_value=_response_message())),
    )


def _model() -> Model:
    return cast(Model, SimpleNamespace(model_name="gpt-5.5"))


def _custom_emoji_ids(html: str) -> list[str]:
    return re.findall(r'<tg-emoji emoji-id="(\d+)">', html)


def _assert_plain_progress(text: str) -> None:
    assert _custom_emoji_ids(text) == [AI_GENERATING_EMOJI_ID, *ANIMATED_LINE_IDS]
    assert AI_CHATBOT_CUSTOM_EMOJI_ID not in text
    assert BATTERY_EMOJI not in text


async def _streamer(
    message: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    test_redis: object,
) -> ChatbotMessageStreamer | None:
    monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_streaming.get_value", _get_value)
    return await build_message_streamer(
        cast(Message, message),
        False,
        redis=test_redis,
    )


@pytest.mark.asyncio
async def test_thinking_placeholder_is_not_a_table_row(
    monkeypatch: pytest.MonkeyPatch, test_redis: object, test_services: object
) -> None:
    quota = _quota()
    monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_response.get_quota_info", quota)

    message = _message()
    streamer = await _streamer(message, monkeypatch, test_redis)

    assert streamer is not None
    _assert_plain_progress(message.bot.send_rich_message.await_args.kwargs["rich_message"].html)
    quota.assert_not_awaited()


@pytest.mark.asyncio
async def test_progress_updates_stay_plain(
    monkeypatch: pytest.MonkeyPatch, test_redis: object, test_services: object
) -> None:
    quota = _quota()
    monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_response.get_quota_info", quota)

    response_message = _response_message()
    streamer = ChatbotMessageStreamer(
        source_message=cast(Message, _message()),
        status="Initial",
        throttle_seconds=0,
        redis=test_redis,
    )
    streamer.response_message = cast(Message, response_message)

    await streamer.update_retrying(1, 5)

    edited_text = response_message.bot.edit_message_text.await_args.kwargs["rich_message"].html
    assert "(Retrying 1/5...)" in edited_text
    assert edited_text.startswith(f'<tg-emoji emoji-id="{AI_GENERATING_EMOJI_ID}">💭</tg-emoji><br>')
    _assert_plain_progress(edited_text)
    quota.assert_not_awaited()


@pytest.mark.asyncio
async def test_draft_displays_action_below_body_and_removes_it_after_new_text(
    test_redis: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    response_message = _response_message()
    monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_streaming.choice", lambda texts: texts[0])
    streamer = ChatbotMessageStreamer(
        source_message=cast(Message, _message()),
        status="Thinking...",
        throttle_seconds=0,
        redis=test_redis,
    )
    streamer.response_message = cast(Message, response_message)
    await streamer.stream("Let me check.")
    await streamer.update_thinking_for_tool("web_search")
    html = response_message.bot.edit_message_text.await_args.kwargs["rich_message"].html
    assert _custom_emoji_ids(html) == [AI_GENERATING_EMOJI_ID, *ANIMATED_LINE_IDS]
    assert html.count("Let me check.") == 1
    assert (
        html.index("Let me check.")
        < html.index("<i>Searching the web...</i>")
        < html.index(ANIMATED_LINE_IDS[0])
    )

    await streamer.stream("Here is the answer.")
    html = response_message.bot.edit_message_text.await_args.kwargs["rich_message"].html
    assert "Searching the web..." not in html
    assert _custom_emoji_ids(html) == [AI_GENERATING_EMOJI_ID, *ANIMATED_LINE_IDS]


@pytest.mark.asyncio
async def test_media_preparation_stacks_video_voice_and_image_activities(
    monkeypatch: pytest.MonkeyPatch, test_redis: object
) -> None:
    response_message = _response_message()
    streamer = ChatbotMessageStreamer(
        source_message=cast(Message, _message()),
        status="Working on it...",
        throttle_seconds=0,
        redis=cast(Any, test_redis),
        stack_tools=True,
    )
    streamer.response_message = cast(Message, response_message)
    download = AsyncMock(side_effect=lambda file_id: BytesIO(b"image-bytes"))
    history = AIMessageHistory(
        services=cast(Any, SimpleNamespace(bot=SimpleNamespace(download=download), redis=test_redis))
    )
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.message_history.transform_video_to_text",
        AsyncMock(return_value="video speech"),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.message_history.transform_voice_to_text",
        AsyncMock(return_value="voice speech"),
    )
    video_message = Message.model_validate(
        {
            "message_id": 1,
            "date": 1790115467,
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 456, "is_bot": False, "first_name": "Alice"},
            "video": {
                "file_id": "video-file",
                "file_unique_id": "video-unique",
                "width": 640,
                "height": 480,
                "duration": 3,
                "thumbnail": PhotoSize(
                    file_id="thumbnail-file", file_unique_id="thumbnail-unique", width=160, height=120
                ),
            },
        }
    )
    await history.add_from_message(video_message, on_activity=streamer.update_processing_activity)
    html = response_message.bot.edit_message_text.await_args.kwargs["rich_message"].html
    assert html.startswith(f'<tg-emoji emoji-id="{AI_GENERATING_EMOJI_ID}">💭</tg-emoji><br>')
    assert "<i>Processing video...</i><br><i>Transcribing video audio...</i><br>" in html
    assert any("video speech" in part for part in history.prompt if isinstance(part, str))

    voice_message = video_message.model_copy(
        update={"video": None, "voice": Voice(file_id="voice-file", file_unique_id="voice-unique", duration=3)}
    )
    await history.add_from_message(voice_message, on_activity=streamer.update_processing_activity)
    image_message = video_message.model_copy(
        update={
            "video": None,
            "photo": [PhotoSize(file_id="photo-file", file_unique_id="photo-unique", width=160, height=120)],
        }
    )
    await history.add_from_message(image_message, on_activity=streamer.update_processing_activity)
    html = response_message.bot.edit_message_text.await_args.kwargs["rich_message"].html
    assert html.index("Processing video...") < html.index("Transcribing video audio...")
    assert html.index("Transcribing video audio...") < html.index("Transcribing voice message...")
    assert html.index("Transcribing voice message...") < html.index("Processing image...")
    assert any(part == "voice speech" for part in history.prompt if isinstance(part, str))
    assert any(isinstance(part, BinaryContent) and part.data == b"image-bytes" for part in history.prompt)


@pytest.mark.asyncio
async def test_first_tool_clears_random_working_header_until_text_arrives(
    monkeypatch: pytest.MonkeyPatch, test_redis: object, test_services: object
) -> None:
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.chatbot_streaming.random_ai_thinking_text", lambda: "Working on it..."
    )
    monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_streaming.choice", lambda texts: texts[0])
    message = _message()
    streamer = await _streamer(message, monkeypatch, test_redis)
    assert streamer is not None
    initial_html = message.bot.send_rich_message.await_args.kwargs["rich_message"].html
    assert initial_html.startswith(f'<tg-emoji emoji-id="{AI_GENERATING_EMOJI_ID}">💭</tg-emoji> Working on it...<br>')

    streamer.throttle_seconds = 0
    await streamer.update_thinking_for_tool("web_search")
    html = streamer.response_message.bot.edit_message_text.await_args.kwargs["rich_message"].html
    assert html.startswith(f'<tg-emoji emoji-id="{AI_GENERATING_EMOJI_ID}">💭</tg-emoji><br>')
    assert "<i>Searching the web...</i><br>" in html
    assert "Working on it..." not in html
    assert "web_search" not in html

    await streamer.stream("Here is the answer.")
    html = streamer.response_message.bot.edit_message_text.await_args.kwargs["rich_message"].html
    assert html.startswith(f'<tg-emoji emoji-id="{AI_GENERATING_EMOJI_ID}">💭</tg-emoji> Here is the answer.')
    assert "Searching the web..." not in html


@pytest.mark.asyncio
async def test_reasoning_as_tool_shows_one_bottom_activity_without_leaking_content(
    monkeypatch: pytest.MonkeyPatch, test_redis: object, test_services: object
) -> None:
    await set_enabled("ai_chatbot_reasoning_as_tool", True, redis=cast(Any, test_redis))
    try:
        monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_streaming.random_ai_thinking_text", lambda: "Working on it...")
        message = _message()
        streamer = await _streamer(message, monkeypatch, test_redis)
        assert streamer is not None
        streamer.throttle_seconds = 0
        await streamer.stream_reasoning("I should check the details.")
        html = streamer.response_message.bot.edit_message_text.await_args.kwargs["rich_message"].html
        assert html.startswith(f'<tg-emoji emoji-id="{AI_GENERATING_EMOJI_ID}">💭</tg-emoji><br>')
        assert "<i>Reasoning...</i><br>" in html
        assert "I should check the details." not in html
        assert "<blockquote>" not in html
        assert "Working on it..." not in html

        await streamer.update_thinking_for_tool("web_search")
        previous_count = streamer.response_message.bot.edit_message_text.await_count
        await streamer.stream_reasoning("The next reasoning pass stays private.")
        assert streamer.response_message.bot.edit_message_text.await_count == previous_count
        html = streamer.response_message.bot.edit_message_text.await_args.kwargs["rich_message"].html
        assert "The next reasoning pass" not in html
        assert "Reasoning..." not in html
    finally:
        await delete_override("ai_chatbot_reasoning_as_tool", redis=cast(Any, test_redis))


@pytest.mark.asyncio
async def test_stacked_activity_keeps_every_tool_retry_and_only_first_reasoning(
    monkeypatch: pytest.MonkeyPatch, test_redis: object, test_services: object
) -> None:
    await set_enabled("ai_chatbot_stack_progress_tools", True, redis=cast(Any, test_redis))
    await set_enabled("ai_chatbot_reasoning_as_tool", True, redis=cast(Any, test_redis))
    try:
        monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_streaming.choice", lambda texts: texts[0])
        monkeypatch.setitem(
            AI_TOOLS_BY_NAME,
            "web_search",
            replace(AI_TOOLS_BY_NAME["web_search"], display_in_ai_header=False),
        )
        message = _message()
        streamer = await _streamer(message, monkeypatch, test_redis)
        assert streamer is not None
        streamer.throttle_seconds = 0
        await streamer.stream_reasoning("First private reasoning.")
        await streamer.update_thinking_for_tool("web_search")
        await streamer.update_thinking_for_tool("web_search")
        await streamer.update_thinking_for_tool("get_notes")
        await streamer.update_retrying(1, 5)
        await streamer.stream_reasoning("Second private reasoning.")

        html = streamer.response_message.bot.edit_message_text.await_args.kwargs["rich_message"].html
        assert html.startswith(f'<tg-emoji emoji-id="{AI_GENERATING_EMOJI_ID}">💭</tg-emoji><br>')
        assert html.count("<i>Reasoning...</i>") == 1
        assert html.count("<i>Searching the web...</i>") == 2
        assert "<i>Scanning notes...</i><br><i>Retrying (1/5)...</i><br>" in html
        assert html.index("Reasoning...") < html.index("Searching the web...") < html.rindex("Searching the web...")
        assert html.rindex("Searching the web...") < html.index("Scanning notes...") < html.index("Retrying (1/5)...")
        assert "First private reasoning" not in html
        assert "Second private reasoning" not in html
        assert "web_search" not in html

        await streamer.stream("Yes, I am going to search the internet...")
        html = streamer.response_message.bot.edit_message_text.await_args.kwargs["rich_message"].html
        assert html.startswith(
            f'<tg-emoji emoji-id="{AI_GENERATING_EMOJI_ID}">💭</tg-emoji> Yes, I am going to search the internet...'
        )
        assert "Reasoning..." not in html
        assert "Searching the web..." not in html
        assert "Retrying (1/5)..." not in html

        await streamer.update_thinking_for_tool("web_search")
        html = streamer.response_message.bot.edit_message_text.await_args.kwargs["rich_message"].html
        assert html.index("Yes, I am going to search") < html.index("<i>Searching the web...</i>")
        assert html.count("<i>Searching the web...</i>") == 1
        assert "Scanning notes..." not in html

        await streamer.update_thinking_for_tool("get_notes")
        html = streamer.response_message.bot.edit_message_text.await_args.kwargs["rich_message"].html
        assert "<i>Searching the web...</i><br><i>Scanning notes...</i><br>" in html

        await streamer.stream("Yes, I am going to search the internet...")
        html = streamer.response_message.bot.edit_message_text.await_args.kwargs["rich_message"].html
        assert "Searching the web..." not in html
        assert "Scanning notes..." not in html
        await streamer.update_thinking_for_tool("get_notes")
        html = streamer.response_message.bot.edit_message_text.await_args.kwargs["rich_message"].html
        assert html.index("Yes, I am going to search") < html.index("<i>Scanning notes...</i>")

        await streamer.stream("Here is the answer.")
        html = streamer.response_message.bot.edit_message_text.await_args.kwargs["rich_message"].html
        assert html.startswith(f'<tg-emoji emoji-id="{AI_GENERATING_EMOJI_ID}">💭</tg-emoji> Here is the answer.')
        assert "Searching the web..." not in html
        assert "Scanning notes..." not in html
        assert "Retrying (1/5)..." not in html
    finally:
        await delete_override("ai_chatbot_stack_progress_tools", redis=cast(Any, test_redis))
        await delete_override("ai_chatbot_reasoning_as_tool", redis=cast(Any, test_redis))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name",
    ["web_search", "get_note_content", "write_memory", "sophie_help", "research_topic"],
)
async def test_tool_call_displays_only_italic_activity(tool_name: str, test_redis: object) -> None:
    response_message = _response_message()
    streamer = ChatbotMessageStreamer(
        source_message=cast(Message, _message()),
        status="Thinking...",
        throttle_seconds=0,
        redis=test_redis,
    )
    streamer.response_message = cast(Message, response_message)
    await streamer.update_thinking_for_tool(tool_name)

    html = response_message.bot.edit_message_text.await_args.kwargs["rich_message"].html
    tool = AI_TOOLS_BY_NAME[tool_name]
    assert any(f"<i>{activity}</i>" in html for activity in tool.activity_texts)
    assert tool.custom_emoji_id not in html
    assert tool_name not in html

@pytest.mark.asyncio
async def test_source_inspection_uses_italic_activity_without_emoji(test_redis: object) -> None:
    response_message = _response_message()
    streamer = ChatbotMessageStreamer(
        source_message=cast(Message, _message()),
        status="Thinking...",
        throttle_seconds=0,
        redis=test_redis,
    )
    streamer.response_message = cast(Message, response_message)

    await streamer.update_thinking_for_tool("sophie_inspect")

    rich_html = response_message.bot.edit_message_text.await_args.kwargs["rich_message"].html
    assert "🔧" not in rich_html
    assert any(
        f"<i>{activity}</i>" in rich_html
        for activity in (
            "Digging through my own sources...",
            "Reading my own code...",
            "Inspecting my source code...",
            "Tracing how Sophie works...",
        )
    )
    assert "Source Inspection</i>" not in rich_html
    assert "sophie_inspect" not in rich_html



@pytest.mark.asyncio
async def test_note_write_uses_its_own_activity_without_a_title(test_redis: object) -> None:
    response_message = _response_message()
    streamer = ChatbotMessageStreamer(
        source_message=cast(Message, _message()),
        status="Thinking...",
        throttle_seconds=0,
        redis=test_redis,
    )
    streamer.response_message = cast(Message, response_message)

    await streamer.update_thinking_for_tool("save_note")

    rich_html = response_message.bot.edit_message_text.await_args.kwargs["rich_message"].html
    assert any(
        f"<i>{activity}</i>" in rich_html
        for activity in ("Saving note...", "Writing to notes...", "Storing the note...", "Updating saved notes...")
    )
    assert '<tg-emoji emoji-id="5537203062138994712">📝</tg-emoji>' not in rich_html
    assert "Notes</i>" not in rich_html


@pytest.mark.asyncio
async def test_retrying_draft_uses_the_configured_simple_layout(test_redis: object) -> None:
    """A retry/failover edit must keep one simple prefix and omit the completed footer."""
    response_message = _response_message()
    streamer = ChatbotMessageStreamer(
        source_message=cast(Message, _message()),
        status="Initial",
        throttle_seconds=0,
        redis=test_redis,
    )
    streamer.response_message = cast(Message, response_message)
    await streamer.stream("The fallback answer")
    await streamer.update_retrying(1, 5)

    assert response_message.bot.edit_message_text.await_count == 2
    rendered_html = response_message.bot.edit_message_text.await_args.kwargs["rich_message"].html
    assert "(Retrying 1/5...)" in rendered_html
    assert rendered_html.count("The fallback answer") == 1
    assert BATTERY_EMOJI not in rendered_html
    assert _custom_emoji_ids(rendered_html) == [AI_GENERATING_EMOJI_ID, *ANIMATED_LINE_IDS]


@pytest.mark.asyncio
async def test_fallback_final_replaces_draft_with_exactly_one_simple_header(test_redis: object) -> None:
    response_message = _response_message()
    streamer = ChatbotMessageStreamer(
        source_message=cast(Message, _message()),
        status="Retrying",
        throttle_seconds=0,
        redis=cast(Any, test_redis),
    )
    streamer.response_message = cast(Message, response_message)
    await streamer.stream("Partial primary answer")
    await streamer.update_retrying(2, 5)

    final_header = build_ai_header("simple", ai_credit_header(50, "fallback-model"))
    final_doc = build_ai_message_doc(
        final_header,
        "Fallback answer",
        tool_labels=(AI_TOOLS_BY_NAME["web_search"],),
        emoji_id=AI_CHATBOT_CUSTOM_EMOJI_ID,
    )
    await streamer.send_final(final_doc)

    assert response_message.bot.edit_message_text.await_args is not None
    rendered_html = response_message.bot.edit_message_text.await_args.kwargs["rich_message"].html
    assert _custom_emoji_ids(rendered_html) == [
        "5573451671289200650",
        "5841424383217766066",
    ]
    assert rendered_html.startswith(
        f'<tg-emoji emoji-id="{AI_CHATBOT_CUSTOM_EMOJI_ID}">✨</tg-emoji> (Search)'
    )
    assert "Fallback answer<br><p>" in rendered_html
    assert rendered_html.count(BATTERY_EMOJI) == 1
    assert "50% (fallback-model)" in rendered_html
    assert rendered_html.count("Fallback answer") == 1
    assert "Partial primary answer" not in rendered_html
    assert "Retrying" not in rendered_html


@pytest.mark.asyncio
async def test_placeholder_emoji_stays_the_same_on_every_edit(
    monkeypatch: pytest.MonkeyPatch, test_redis: object, test_services: object
) -> None:
    """The animated marker and line stay stable across placeholder edits."""
    message = _message()
    streamer = await _streamer(message, monkeypatch, test_redis)

    assert streamer is not None
    streamer.throttle_seconds = 0
    await streamer.update_retrying(1, 5)

    initial_emoji_ids = _custom_emoji_ids(message.bot.send_rich_message.await_args.kwargs["rich_message"].html)
    edited_emoji_ids = _custom_emoji_ids(
        streamer.response_message.bot.edit_message_text.await_args.kwargs["rich_message"].html
    )
    assert initial_emoji_ids == [AI_GENERATING_EMOJI_ID, *ANIMATED_LINE_IDS]
    assert edited_emoji_ids == initial_emoji_ids


@pytest.mark.asyncio
async def test_finished_reply_uses_custom_ai_emoji_and_battery_footer(
    monkeypatch: pytest.MonkeyPatch, test_redis: object, test_services: object
) -> None:
    monkeypatch.setattr("sophie_bot.modules.ai.utils.chatbot_response.get_quota_info", _quota())

    header = await build_chatbot_header(cast(Any, "chat-iid"), redis=test_redis)

    doc = await build_reply_doc(header, "Hello", None, None, False, -100123, redis=test_redis, strip_alien_html_tags=False)
    text = doc.to_rich()
    assert text.startswith(f'<tg-emoji emoji-id="{AI_CHATBOT_CUSTOM_EMOJI_ID}">✨</tg-emoji> Hello')
    assert "<br><p><tg-emoji" in text
    assert BATTERY_EMOJI in text
    assert "50%" in text
    assert "<table" not in text


def test_simple_header_renders_battery_in_separate_paragraph() -> None:
    header = build_ai_header("simple", ai_credit_header(50))

    assert header is not None
    text = build_ai_message_doc(header, "Hello\nSecond line").to_rich()
    assert text.startswith(f'<tg-emoji emoji-id="{AI_CUSTOM_EMOJI_ID}">✨</tg-emoji> Hello\nSecond line')
    assert "<br><p><tg-emoji" in text
    assert text.endswith(" 50%</p>")
    assert "<table" not in text


def test_simple_header_renders_first_markdown_paragraph_inline() -> None:
    header = build_ai_header("simple", ai_credit_header(50))

    assert header is not None
    text = build_ai_message_doc(header, ai_markdown_to_doc("Hello *world*.\n\nSecond paragraph.")).to_rich()
    assert text.startswith(f'<tg-emoji emoji-id="{AI_CUSTOM_EMOJI_ID}">✨</tg-emoji> Hello <i>world</i>.')
    assert "<p>" in text
    assert "\nSecond paragraph.<br>" in text


def test_used_tool_categories_render_before_reply_body() -> None:
    labels = used_tool_labels(
        [
            ModelResponse(parts=[ToolCallPart(tool_name="get_note_content", args={})]),
            ModelResponse(parts=[ToolCallPart(tool_name="kagi_search", args={})]),
            ModelResponse(parts=[ToolCallPart(tool_name="tinyfish_search", args={})]),
        ]
    )
    header = build_ai_header("simple", ai_credit_header(45))

    text = build_ai_message_doc(header, "Reply here", tool_labels=labels).to_rich()

    assert tuple(tool.display_label() for tool in labels) == ("Search", "Notes")
    assert text.startswith(f'<tg-emoji emoji-id="{AI_CUSTOM_EMOJI_ID}">✨</tg-emoji> (Search, Notes) Reply here')
    assert 'emoji-id="5535248817659576336"' not in text
    assert 'emoji-id="5537203062138994712"' not in text
    assert build_ai_message_doc(header, "Reply here", tool_labels=labels).to_md().startswith(
        "✨ (Search, Notes) Reply here"
    )
    assert "<br><p><tg-emoji" in text
    assert text.endswith(" 45%</p>")


def test_memory_help_and_research_tool_titles_omit_custom_emojis() -> None:
    labels = used_tool_labels(
        [
            ModelResponse(parts=[ToolCallPart(tool_name="write_memory", args={})]),
            ModelResponse(parts=[ToolCallPart(tool_name="sophie_help", args={})]),
            ModelResponse(parts=[ToolCallPart(tool_name="research_topic", args={})]),
        ]
    )
    doc = build_ai_message_doc(build_ai_header("simple", ai_credit_header(80)), "Answer", tool_labels=labels)

    assert all(tool.custom_emoji_id not in doc.to_rich() for tool in labels)
    assert doc.to_md().startswith("✨ (Memory, Help, Research) Answer")


def test_header_visibility_is_per_tool_and_preserves_visible_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        AI_TOOLS_BY_NAME,
        "web_search",
        replace(AI_TOOLS_BY_NAME["web_search"], display_in_ai_header=False),
    )
    monkeypatch.setitem(
        AI_TOOLS_BY_NAME,
        "write_memory",
        replace(AI_TOOLS_BY_NAME["write_memory"], display_in_ai_header=False),
    )
    labels = used_tool_labels(
        [
            ModelResponse(parts=[ToolCallPart(tool_name="web_search", args={})]),
            ModelResponse(parts=[ToolCallPart(tool_name="write_memory", args={})]),
            ModelResponse(parts=[ToolCallPart(tool_name="get_notes", args={})]),
            ModelResponse(parts=[ToolCallPart(tool_name="kagi_search", args={})]),
        ]
    )

    doc = build_ai_message_doc(build_ai_header("simple", ai_credit_header(70)), "Answer", tool_labels=labels)
    assert doc.to_md().startswith("✨ (Search, Notes) Answer")
    assert "Memory" not in doc.to_rich()

    monkeypatch.setitem(
        AI_TOOLS_BY_NAME,
        "save_note",
        replace(AI_TOOLS_BY_NAME["save_note"], display_in_ai_header=False),
    )
    hidden_only = used_tool_labels(
        [ModelResponse(parts=[ToolCallPart(tool_name="save_note", args={})])]
    )
    hidden_doc = build_ai_message_doc(build_ai_header("simple", ai_credit_header(70)), "Answer", tool_labels=hidden_only)
    assert hidden_doc.to_md().startswith("✨ Answer")


@pytest.mark.parametrize(
    ("percentage", "emoji_id"),
    [
        (0, "5841410188350852356"),
        (32, "5841410188350852356"),
        (33, "5841424383217766066"),
        (65, "5841424383217766066"),
        (66, "5841233274352963797"),
        (100, "5841233274352963797"),
    ],
)
def test_battery_emoji_matches_remaining_charge(percentage: int, emoji_id: str) -> None:
    expected = f'<tg-emoji emoji-id="{emoji_id}">🔋</tg-emoji> {percentage}%'
    assert ai_credit_header(percentage).to_rich().startswith(expected)


def test_model_name_is_optional_after_battery_percentage() -> None:
    assert ai_credit_header(45).to_rich().endswith(" 45%")
    assert ai_credit_header(45, "Gemini 5").to_rich().endswith(" 45% (Gemini 5)")
    assert model_display_name(_model()) == "GPT 5.5"


def test_disabled_header_leaves_only_the_body() -> None:
    header = build_ai_header("disable", ai_credit_header(50))

    assert header is None
    assert build_ai_message_doc(header, "Hello").to_html() == "Hello"
