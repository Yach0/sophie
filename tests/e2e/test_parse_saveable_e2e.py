from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
from aiogram import Dispatcher, Router
from aiogram.types import Chat, Message, MessageEntity, Update, User
from aiogram.utils.text_decorations import HtmlDecoration
from aiogram_test_framework import TestClient

from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.notes.utils.buttons_processor.buttons import ButtonsList
from sophie_bot.modules.notes.utils.parse import parse_saveable

E2E_PARSE_RAW_COMMAND = "e2e_parse_saveable_raw"
E2E_PARSE_HTML_COMMAND = "e2e_parse_saveable_html"
TEST_ROUTER = Router(name="parse_saveable_e2e_router")


def _utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


async def _new_requests_for_update(test_client: TestClient, update: Update) -> list[Any]:
    start_index = len(test_client.capture)
    await test_client.dispatcher.feed_update(bot=test_client.bot, update=update)
    return test_client.capture.all_requests[start_index:]


def _extract_last_response_text(requests: list[Any]) -> str:
    for request in reversed(requests):
        request_text = getattr(request, "text", None)
        if request_text:
            return request_text

    return ""


def _slice_entities_to_segment(
    entities: list[MessageEntity],
    source_text: str,
    segment_start: int,
    segment_end: int,
) -> list[MessageEntity]:
    segment_start_utf16 = _utf16_length(source_text[:segment_start])
    segment_end_utf16 = _utf16_length(source_text[:segment_end])
    segment_entities: list[MessageEntity] = []

    for source_entity in entities:
        entity_start = source_entity.offset
        entity_end = source_entity.offset + source_entity.length
        overlap_start = max(entity_start, segment_start_utf16)
        overlap_end = min(entity_end, segment_end_utf16)
        if overlap_start >= overlap_end:
            continue

        segment_entities.append(
            source_entity.model_copy(
                update={
                    "offset": overlap_start - segment_start_utf16,
                    "length": overlap_end - overlap_start,
                }
            )
        )

    return segment_entities


def _build_private_message(
    user_id: int,
    update_id: int,
    message_id: int,
    text: str,
    entities: list[MessageEntity] | None = None,
) -> Update:
    user_chat = Chat(id=user_id, type="private", first_name=f"User{user_id}", username=f"user_{user_id}")
    from_user = User(id=user_id, is_bot=False, first_name=f"User{user_id}", username=f"user_{user_id}")

    message = Message(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=user_chat,
        from_user=from_user,
        text=text,
        entities=entities,
    )
    return Update(update_id=update_id, message=message)


@TEST_ROUTER.message(CMDFilter(E2E_PARSE_RAW_COMMAND))
async def e2e_parse_saveable_raw_handler(message: Message) -> None:
    full_text = message.text or ""
    command_prefix = f"/{E2E_PARSE_RAW_COMMAND}"

    if not full_text.startswith(command_prefix) or len(full_text) <= len(command_prefix):
        await message.reply("NO_CONTENT")
        return

    content_offset = len(command_prefix) + 1
    parsed_content_value = full_text[content_offset:]

    saveable = await parse_saveable(
        message=message,
        text=parsed_content_value,
        allow_reply_message=False,
        buttons=ButtonsList(),
        offset=content_offset,
    )
    await message.reply(saveable.text or "<EMPTY>")


@TEST_ROUTER.message(CMDFilter(E2E_PARSE_HTML_COMMAND))
async def e2e_parse_saveable_html_handler(message: Message) -> None:
    full_text = message.text or ""
    command_prefix = f"/{E2E_PARSE_HTML_COMMAND}"

    if not full_text.startswith(command_prefix) or len(full_text) <= len(command_prefix):
        await message.reply("NO_CONTENT")
        return

    content_offset = len(command_prefix) + 1
    content_text = full_text[content_offset:]
    content_entities = _slice_entities_to_segment(
        entities=list(message.entities or []),
        source_text=full_text,
        segment_start=content_offset,
        segment_end=len(full_text),
    )

    parsed_content_html = HtmlDecoration().unparse(content_text, content_entities) if content_entities else content_text

    saveable = await parse_saveable(
        message=message,
        text=parsed_content_html,
        allow_reply_message=False,
        buttons=ButtonsList(),
        offset=content_offset,
    )
    await message.reply(saveable.text or "<EMPTY>")


@pytest_asyncio.fixture
async def register_parse_saveable_router(test_dispatcher: Dispatcher) -> None:
    if not getattr(test_dispatcher, "_parse_saveable_e2e_router_registered", False):
        test_dispatcher.include_router(TEST_ROUTER)
        setattr(test_dispatcher, "_parse_saveable_e2e_router_registered", True)


@pytest.mark.asyncio
async def test_parse_saveable_e2e_preserves_custom_emoji(
    test_client: TestClient,
    register_parse_saveable_router: None,
) -> None:
    command_prefix = f"/{E2E_PARSE_RAW_COMMAND} "
    message_text = command_prefix + "Custom 🙂 emoji"
    emoji_offset = _utf16_length(command_prefix + "Custom ")

    update = _build_private_message(
        user_id=920001,
        update_id=990001,
        message_id=700001,
        text=message_text,
        entities=[MessageEntity(type="custom_emoji", offset=emoji_offset, length=2, custom_emoji_id="777888999")],
    )

    requests = await _new_requests_for_update(test_client, update)
    response_text = _extract_last_response_text(requests)

    assert response_text
    assert '<tg-emoji emoji-id="777888999">🙂</tg-emoji>' in response_text


@pytest.mark.asyncio
async def test_parse_saveable_e2e_preserves_html_bold_italic_and_link(
    test_client: TestClient,
    register_parse_saveable_router: None,
) -> None:
    command_prefix = f"/{E2E_PARSE_HTML_COMMAND} "
    content_text = "Bold Italic Link"
    message_text = command_prefix + content_text

    bold_offset = _utf16_length(command_prefix)
    italic_offset = _utf16_length(command_prefix + "Bold ")
    link_offset = _utf16_length(command_prefix + "Bold Italic ")

    entities = [
        MessageEntity(type="bold", offset=bold_offset, length=4),
        MessageEntity(type="italic", offset=italic_offset, length=6),
        MessageEntity(type="text_link", offset=link_offset, length=4, url="https://example.com"),
    ]

    update = _build_private_message(
        user_id=920002,
        update_id=990002,
        message_id=700002,
        text=message_text,
        entities=entities,
    )

    requests = await _new_requests_for_update(test_client, update)
    response_text = _extract_last_response_text(requests)

    assert response_text
    assert "<b>Bold</b>" in response_text
    assert "<i>Italic</i>" in response_text
    assert '<a href="https://example.com">Link</a>' in response_text


@pytest.mark.asyncio
async def test_parse_saveable_e2e_preserves_combined_custom_emoji_and_html(
    test_client: TestClient,
    register_parse_saveable_router: None,
) -> None:
    command_prefix = f"/{E2E_PARSE_HTML_COMMAND} "
    content_text = "Hi 🙂 bold"
    message_text = command_prefix + content_text

    emoji_offset = _utf16_length(command_prefix + "Hi ")
    bold_offset = _utf16_length(command_prefix + "Hi 🙂 ")

    entities = [
        MessageEntity(type="custom_emoji", offset=emoji_offset, length=2, custom_emoji_id="123123123"),
        MessageEntity(type="bold", offset=bold_offset, length=4),
    ]

    update = _build_private_message(
        user_id=920003,
        update_id=990003,
        message_id=700003,
        text=message_text,
        entities=entities,
    )

    requests = await _new_requests_for_update(test_client, update)
    response_text = _extract_last_response_text(requests)

    assert response_text
    assert '<tg-emoji emoji_id="123123123">🙂</tg-emoji>' in response_text
    assert "<b>bold</b>" in response_text


@pytest.mark.asyncio
async def test_parse_saveable_e2e_plain_text_stays_plain(
    test_client: TestClient,
    register_parse_saveable_router: None,
) -> None:
    command_prefix = f"/{E2E_PARSE_RAW_COMMAND} "
    content_text = "simple plain text"
    message_text = command_prefix + content_text

    update = _build_private_message(
        user_id=920004,
        update_id=990004,
        message_id=700004,
        text=message_text,
        entities=[],
    )

    requests = await _new_requests_for_update(test_client, update)
    response_text = _extract_last_response_text(requests)

    assert response_text == content_text
