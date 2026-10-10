from __future__ import annotations

from datetime import UTC, datetime
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.enums import MessageEntityType
from aiogram.types import Chat, Message, MessageEntity
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from stfu_tg import BlockQuote, Doc, Section

from sophie_bot.modules.ai.utils import ai_chatbot_reply as reply_module
from sophie_bot.modules.ai.utils.message_history import AIMessageHistory
from sophie_bot.services.application import ApplicationServices


class ParsedHTML(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.text = ""
        self.quote_text = ""
        self.expandable_quotes = 0
        self.entity_starts: list[int] = []
        self.entities: list[MessageEntity] = []
        self.feed(source)
        self.close()
        assert not self.tags

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append(tag)
        self.entity_starts.append(len(self.text.encode("utf-16-le")) // 2)
        if tag == "blockquote":
            assert ("expandable", None) in attrs
            self.expandable_quotes += 1

    def handle_endtag(self, tag: str) -> None:
        assert self.tags.pop() == tag
        start = self.entity_starts.pop()
        self.entities.append(
            MessageEntity(
                type={"b": "bold", "u": "underline", "blockquote": "expandable_blockquote"}[tag],
                offset=start,
                length=len(self.text.encode("utf-16-le")) // 2 - start,
            )
        )

    def handle_data(self, data: str) -> None:
        self.text += data
        if "blockquote" in self.tags:
            self.quote_text += data


def _message() -> Message:
    return Message(message_id=1, date=datetime.now(UTC), chat=Chat(id=123, type="private"))


def _assert_history(
    replies: AsyncMock, history: AIMessageHistory, *, oversized: bool, title: str = "LLM History"
) -> None:
    rendered = [call.kwargs["text"] for call in replies.await_args_list]
    expected = ParsedHTML(Section(BlockQuote(history.history_debug(), expandable=True), title=title).to_html())
    underline = next(entity for entity in expected.entities if entity.type == "underline")
    expected.entities.append(underline.model_copy(update={"type": MessageEntityType.BOLD}))
    expected_quote = next(entity for entity in expected.entities if entity.type == "expandable_blockquote")
    title_text = expected.text.encode("utf-16-le")[: expected_quote.offset * 2].decode("utf-16-le")
    assert len(rendered) > 1 if oversized else len(rendered) == 1
    assert all(len(part.encode("utf-16-le")) // 2 <= 4096 for part in rendered)
    quote_parts: list[str] = []
    payload_entities: list[MessageEntity] = []
    payload_offset = 0
    for call in replies.await_args_list:
        assert call.kwargs["parse_mode"] is None
        encoded = call.kwargs["text"].encode("utf-16-le")
        entities = call.kwargs["entities"]
        quotes = [entity for entity in entities if entity.type == MessageEntityType.EXPANDABLE_BLOCKQUOTE]
        assert len(quotes) == 1
        assert call.kwargs["text"].startswith(title_text)
        assert call.kwargs["text"].strip()
        assert quotes[0].offset == expected_quote.offset
        assert quotes[0].offset + quotes[0].length == len(encoded) // 2
        assert sorted(
            (entity.type, entity.offset, entity.length) for entity in entities if entity.offset < quotes[0].offset
        ) == sorted(
            (entity.type, entity.offset, entity.length)
            for entity in expected.entities
            if entity.offset < expected_quote.offset
        )
        for entity in entities:
            assert entity.length > 0
            assert 0 <= entity.offset < entity.offset + entity.length <= len(encoded) // 2
            # Entity boundaries must also respect Unicode surrogate pairs.
            entity_text = encoded[entity.offset * 2 : (entity.offset + entity.length) * 2].decode("utf-16-le")
            if entity in quotes:
                quote_parts.append(entity_text)
            if entity.offset >= quotes[0].offset:
                payload_entities.append(
                    entity.model_copy(update={"offset": entity.offset - quotes[0].offset + payload_offset})
                )
        payload_offset += quotes[0].length
    assert "".join(quote_parts) == expected.quote_text
    for entity_type in ("bold", "underline", "expandable_blockquote"):
        assert {
            position
            for entity in payload_entities
            if entity.type == entity_type
            for position in range(entity.offset, entity.offset + entity.length)
        } == {
            position - expected_quote.offset
            for entity in expected.entities
            if entity.type == entity_type and entity.offset >= expected_quote.offset
            for position in range(entity.offset, entity.offset + entity.length)
        }
    assert all(call.kwargs["disable_web_page_preview"] for call in replies.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parts,oversized",
    [
        ([f"part {index}: " + "history " * 180 for index in range(8)], True),
        (["<tag>& literal &#128512; 😀🧑‍💻 é\n" * 250], True),
        (["single" * 1800], True),
        (["😀" * 2040], True),
        (["a😀界" * 3000], True),
        (["short <&> 😀 history"], False),
    ],
    ids=["multi-part", "escaping-unicode", "single-long-part", "utf16-limit", "mixed-unicode", "short"],
)
async def test_debug_history_preserves_complete_expandable_text(
    test_services: ApplicationServices, parts: list[str], oversized: bool
) -> None:
    history = AIMessageHistory(services=test_services)
    for part in parts:
        history.add_system(part)
    replies = AsyncMock()

    with patch.object(Message, "reply", replies), patch.object(Message, "reply_document", AsyncMock()) as document:
        await reply_module._reply_debug_history(_message(), history)

    _assert_history(replies, history, oversized=oversized)
    document.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", "end"], ids=["whitespace-tail", "whitespace-middle"])
@pytest.mark.parametrize("title", ["LLM History", "Translated 😀 History"])
async def test_debug_history_whitespace_only_quote_remains_sendable(
    test_services: ApplicationServices, suffix: str, title: str
) -> None:
    history = AIMessageHistory(services=test_services)
    template = ParsedHTML(Section(BlockQuote("payload", expandable=True), title=title).to_html())
    quote = next(entity for entity in template.entities if entity.type == "expandable_blockquote")
    payload_limit = 4096 - quote.offset
    payload = "a" * payload_limit + " \t\n" * (payload_limit // 3) + " " * (payload_limit % 3) + suffix
    replies = AsyncMock()

    with (
        patch.object(history, "history_debug", return_value=Doc(payload)),
        patch.object(reply_module, "_", return_value=title),
        patch.object(Message, "reply", replies),
        patch.object(Message, "reply_document", AsyncMock()) as document,
    ):
        await reply_module._reply_debug_history(_message(), history)
        _assert_history(replies, history, oversized=True, title=title)

    assert len(replies.await_args_list) == (3 if suffix else 2)
    whitespace_message = replies.await_args_list[1].kwargs
    encoded = whitespace_message["text"].encode("utf-16-le")
    assert len(encoded) // 2 == 4096
    assert not encoded[quote.offset * 2 :].decode("utf-16-le").strip()
    document.assert_not_awaited()


@pytest.mark.asyncio
async def test_debug_history_preserves_tool_user_response_and_prompt_formatting(
    test_services: ApplicationServices,
) -> None:
    history = AIMessageHistory(services=test_services)
    history.add_custom("user <&> 😀 " * 400, name="Tester")
    history.message_history.extend(
        [
            ModelResponse(
                parts=[
                    TextPart(content="assistant answer"),
                    ToolCallPart(tool_name="tool😀" * 900, args={"query": "<&> 😀"}, tool_call_id="call"),
                ]
            ),
            ModelRequest(parts=[ToolReturnPart(tool_name="tool", content="result <&>", tool_call_id="call")]),
        ]
    )
    history.prompt = ["current prompt <&> 😀 " * 400]
    replies = AsyncMock()

    with patch.object(Message, "reply", replies), patch.object(Message, "reply_document", AsyncMock()) as document:
        await reply_module._reply_debug_history(_message(), history)

    _assert_history(replies, history, oversized=True)
    document.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "history_content",
    ["full history 😀 " * 900, "a" * 4096 + " " * 8192 + "end"],
    ids=["unicode-history", "whitespace-chunk"],
)
async def test_normal_ai_reply_continues_after_all_debug_messages(
    test_services: ApplicationServices, monkeypatch: pytest.MonkeyPatch, history_content: str
) -> None:
    history = AIMessageHistory(services=test_services)
    history.add_system(history_content)
    message = _message()
    connection = SimpleNamespace(tid=123, db_model=SimpleNamespace(iid="chat-iid", tid=123))
    model_plan = SimpleNamespace(primary=SimpleNamespace(model_name="mock/test-model"))
    result = SimpleNamespace(served_model=None, message_history=history.message_history, output="AI answer")
    final_message = _message()
    replies = AsyncMock()

    async def run_chatbot(request: object) -> SimpleNamespace:
        _assert_history(replies, history, oversized=True)
        return result

    for name, return_value in (
        ("is_enabled", True),
        ("_resolve_model_plan", model_plan),
        ("build_message_streamer", None),
        ("prepare_chatbot_history", history),
        ("resolve_chat_service_tier", None),
        ("_build_chatbot_header", None),
        ("_build_fitting_reply_doc", Doc("AI answer")),
        ("should_offer_help_mode", False),
        ("send_ai_rich_message", final_message),
        ("cache_message", None),
        ("remember_chatbot_tool_history", None),
    ):
        monkeypatch.setattr(reply_module, name, AsyncMock(return_value=return_value))
    runner = AsyncMock(side_effect=run_chatbot)
    monkeypatch.setattr(reply_module, "run_chatbot", runner)
    monkeypatch.setattr(reply_module, "SophieAIToolContext", MagicMock(side_effect=SimpleNamespace))

    with patch.object(Message, "reply", replies):
        actual = await reply_module.ai_chatbot_reply(message, connection, debug_mode=True, services=test_services)

    assert actual is final_message
    runner.assert_awaited_once()
    reply_module.send_ai_rich_message.assert_awaited_once()
