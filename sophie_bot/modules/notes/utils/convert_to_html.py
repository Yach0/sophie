from __future__ import annotations

from aiogram.types import Message, MessageEntity
from aiogram.utils.text_decorations import HtmlDecoration


def tg_emoji_workaround(text: str) -> str:
    return text.replace("<tg-emoji emoji_id=", "<tg-emoji emoji-id=")


def _utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _message_text_and_entities(message: Message) -> tuple[str, list[MessageEntity]] | None:
    message_text = getattr(message, "text", None)
    if message_text is not None:
        return message_text, list(getattr(message, "entities", None) or [])

    caption_text = getattr(message, "caption", None)
    if caption_text is not None:
        return caption_text, list(getattr(message, "caption_entities", None) or [])

    return None


def _message_entity_type(entity: MessageEntity) -> str:
    entity_type = entity.type
    return str(entity_type.value) if hasattr(entity_type, "value") else str(entity_type)


def preserve_custom_emoji_inline_html(message: Message, text: str, offset: int) -> str | None:
    if "<tg-emoji" in text:
        return None

    text_and_entities = _message_text_and_entities(message)
    if not text_and_entities:
        return None

    source_text, source_entities = text_and_entities
    if not source_text:
        return None

    source_length = len(source_text)
    segment_start = max(0, offset)
    segment_end = min(source_length, segment_start + len(text))

    if segment_start >= source_length or segment_end <= segment_start:
        return None

    source_slice = source_text[segment_start:segment_end]
    if source_slice != text:
        fallback_start = source_text.find(text)
        if fallback_start < 0:
            return None
        segment_start = fallback_start
        segment_end = segment_start + len(text)
        source_slice = source_text[segment_start:segment_end]

    segment_start_utf16 = _utf16_length(source_text[:segment_start])
    segment_end_utf16 = _utf16_length(source_text[:segment_end])

    segment_entities: list[MessageEntity] = []
    has_custom_emoji = False
    for source_entity in source_entities:
        entity_start = source_entity.offset
        entity_end = source_entity.offset + source_entity.length

        overlap_start = max(entity_start, segment_start_utf16)
        overlap_end = min(entity_end, segment_end_utf16)
        if overlap_start >= overlap_end:
            continue

        segment_entity = source_entity.model_copy(
            update={
                "offset": overlap_start - segment_start_utf16,
                "length": overlap_end - overlap_start,
            }
        )
        segment_entities.append(segment_entity)

        if _message_entity_type(segment_entity) == "custom_emoji":
            has_custom_emoji = True

    if not has_custom_emoji:
        return None

    html_text = HtmlDecoration().unparse(source_slice, segment_entities)
    return tg_emoji_workaround(html_text)
