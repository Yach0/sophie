from __future__ import annotations

import re
from collections.abc import Sequence

from aiogram.types import Message, RichBlockParagraph, RichTextCustomEmoji

from sophie_bot.constants import AI_EMOJI
from sophie_bot.modules.ai.fsm.pm import AI_GENERATED_TEXT
from sophie_bot.modules.ai.utils.ai_header import AI_BATTERY_CUSTOM_EMOJI_IDS, AI_CUSTOM_EMOJI_ID
from sophie_bot.modules.ai.utils.ai_progress import AI_PROGRESS_MARKER

_LEGACY_AI_HEADER_LABEL = f"{AI_EMOJI} AI"
_LEGACY_AI_HEADER_SEPARATOR = " | "
_LEGACY_SIMPLE_HEADER_PREFIX = f"{AI_EMOJI} 🔋"
_BATTERY_MARKER_PATTERN = r'(?:<tg-emoji emoji-id="\d+">)?🔋(?:</tg-emoji>)?'
_SIMPLE_FOOTER_PATTERN = re.compile(
    rf"\n+{_BATTERY_MARKER_PATTERN}(?: \d+%)?(?: \([^()\n]+\))?(?=[ \t]*(?:\n|$)|[ \t]+\*?⚠️)"
)


def _rich_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_rich_text(item) for item in value)

    alternative_text = getattr(value, "alternative_text", None)
    if isinstance(alternative_text, str):
        return alternative_text

    nested_text = getattr(value, "text", None)
    return _rich_text(nested_text) if nested_text is not None else ""


def _rich_block_text(block: object) -> str:
    cells = getattr(block, "cells", None)
    if isinstance(cells, list):
        return "\n".join(
            _LEGACY_AI_HEADER_SEPARATOR.join(_rich_text(cell) for cell in row) for row in cells if isinstance(row, list)
        )

    return _rich_text(getattr(block, "text", None))


def message_text(message: Message | object) -> str:
    rich = getattr(message, "rich_message", None)
    if rich is not None:
        rich_text = "\n".join(text for block in rich.blocks if (text := _rich_block_text(block)))
        if rich_text:
            return rich_text

    text = getattr(message, "text", None)
    return text if isinstance(text, str) else ""


def _ai_marker(message: Message) -> RichTextCustomEmoji | None:
    rich = message.rich_message
    if not rich or not rich.blocks or not isinstance(rich.blocks[0], RichBlockParagraph):
        return None
    first_item = rich.blocks[0].text
    while isinstance(first_item, list) and first_item:
        first_item = first_item[0]
    if isinstance(first_item, RichTextCustomEmoji) and first_item.custom_emoji_id == AI_CUSTOM_EMOJI_ID:
        return first_item
    return None


def _rich_battery_offset(value: object) -> tuple[int, int | None]:
    if isinstance(value, list):
        length = 0
        battery_offset = None
        for item in value:
            item_length, item_battery_offset = _rich_battery_offset(item)
            if item_battery_offset is not None:
                battery_offset = length + item_battery_offset
            length += item_length
        return length, battery_offset
    if isinstance(value, RichTextCustomEmoji):
        battery_offset = 0 if value.custom_emoji_id in AI_BATTERY_CUSTOM_EMOJI_IDS else None
        return len(value.alternative_text), battery_offset
    if isinstance(value, str):
        return len(value), None
    nested_text = getattr(value, "text", None)
    if nested_text is not None:
        return _rich_battery_offset(nested_text)
    return len(_rich_text(value)), None


def _last_battery_offset(message: Message) -> int | None:
    rich = message.rich_message
    if not rich:
        return None
    offset = 0
    battery_offset = None
    for block in rich.blocks:
        block_text = _rich_block_text(block)
        if not block_text:
            continue
        if offset:
            offset += 1
        if isinstance(block, RichBlockParagraph):
            _, block_battery_offset = _rich_battery_offset(block.text)
            if block_battery_offset is not None:
                battery_offset = offset + block_battery_offset
        offset += len(block_text)
    return battery_offset


def is_ai_message(message: str | Message) -> bool:
    """Whether a message is one of Sophie's AI messages, including old header layouts."""
    if isinstance(message, Message):
        return _ai_marker(message) is not None
    text = message
    first_line = text.split("\n", 1)[0].strip()
    if first_line == _LEGACY_AI_HEADER_LABEL or first_line.startswith(
        _LEGACY_AI_HEADER_LABEL + _LEGACY_AI_HEADER_SEPARATOR
    ):
        return True
    if first_line == _LEGACY_SIMPLE_HEADER_PREFIX or first_line.startswith(_LEGACY_SIMPLE_HEADER_PREFIX + " "):
        return True
    if (first_line == AI_EMOJI or first_line.startswith(AI_EMOJI + " ")) and _SIMPLE_FOOTER_PATTERN.search(text):
        return True

    if first_line.startswith(AI_PROGRESS_MARKER + " "):
        return True

    return first_line.startswith((f"[{AI_GENERATED_TEXT}]", f"[{_LEGACY_AI_HEADER_LABEL}]"))


def _strip_tool_label_prefix(text: str, tool_labels: Sequence[str]) -> str:
    if not tool_labels:
        return text
    prefix = f"({', '.join(tool_labels)})"
    if text == prefix:
        return ""
    return text.removeprefix(prefix + " ")


def cut_titlebar(message: str | Message, *, tool_labels: Sequence[str] = ()) -> str:
    if isinstance(message, Message):
        text = message_text(message)
        marker = _ai_marker(message)
        if marker is None:
            return text
        if (battery_offset := _last_battery_offset(message)) is not None:
            text = text[:battery_offset]
        body = text[len(marker.alternative_text) :].strip(" \n")
        return _strip_tool_label_prefix(body, tool_labels)

    text = message
    footer_match = _SIMPLE_FOOTER_PATTERN.search(text)
    if text.startswith(AI_EMOJI) and footer_match:
        body = text[len(AI_EMOJI) : footer_match.start()].lstrip(" \n").rstrip("\n")
        return _strip_tool_label_prefix(body, tool_labels)

    simple_match = re.match(
        rf"^{re.escape(_LEGACY_SIMPLE_HEADER_PREFIX)}(?: \d+% (?=\S)|\n|$)",
        text,
    )
    if simple_match:
        return _strip_tool_label_prefix(text[simple_match.end() :], tool_labels)

    first_line, separator, body = text.partition("\n")
    if is_ai_message(first_line):
        stripped = body.lstrip("\n") if separator else ""
        return _strip_tool_label_prefix(stripped, tool_labels)
    return text
