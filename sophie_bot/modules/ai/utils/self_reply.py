from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import Message, RichBlockParagraph, RichTextCustomEmoji

from sophie_bot.modules.ai.utils.ai_header import (
    AI_BATTERY_CUSTOM_EMOJI_IDS,
    AI_CHATBOT_CUSTOM_EMOJI_ID,
    AI_CUSTOM_EMOJI_ID,
    AI_GENERATING_EMOJI_ID,
)
from sophie_bot.modules.ai.utils.ai_tool import AITool
from sophie_bot.shared.message_text import message_text, rich_block_text, rich_text


def _ai_marker(message: Message) -> RichTextCustomEmoji | None:
    rich = message.rich_message
    if not rich or not rich.blocks or not isinstance(rich.blocks[0], RichBlockParagraph):
        return None
    first_item = rich.blocks[0].text
    while isinstance(first_item, list) and first_item:
        first_item = first_item[0]
    if isinstance(first_item, RichTextCustomEmoji) and first_item.custom_emoji_id in {
        AI_CUSTOM_EMOJI_ID,
        AI_CHATBOT_CUSTOM_EMOJI_ID,
        AI_GENERATING_EMOJI_ID,
    }:
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
    return len(rich_text(value)), None


def _battery_footer_offset(block: RichBlockParagraph, text: str) -> int | None:
    if text == "🔋":
        return 0
    _, battery_offset = _rich_battery_offset(block.text)
    if battery_offset is None or (battery_offset and text[battery_offset - 1] != "\n"):
        return None
    footer = text[battery_offset + len("🔋") :].partition("\n")[0].strip()
    percentage, separator, model_label = footer.partition(" ")
    if not percentage.endswith("%") or not percentage[:-1].isdigit():
        return None
    if separator and not (model_label.startswith("(") and model_label.endswith(")")):
        return None
    return battery_offset


def _rich_footer_offset(message: Message) -> int | None:
    rich = message.rich_message
    if not rich:
        return None
    offset = 0
    battery_offset = None
    for block in rich.blocks:
        block_text = rich_block_text(block)
        if not block_text:
            continue
        if offset:
            offset += 1
        if isinstance(block, RichBlockParagraph):
            block_battery_offset = _battery_footer_offset(block, block_text)
            if block_battery_offset is not None:
                battery_offset = offset + block_battery_offset
        offset += len(block_text)
    return battery_offset


def is_ai_message(message: Message) -> bool:
    """Whether the Rich message starts with Sophie's current AI custom emoji."""
    return _ai_marker(message) is not None


def _strip_tool_label_prefix(text: str, tool_labels: Sequence[AITool]) -> str:
    if not tool_labels:
        return text
    prefix = f"({', '.join(tool.display_label() for tool in tool_labels)})"
    if text == prefix:
        return ""
    return text.removeprefix(prefix + " ")


def cut_titlebar(message: Message, *, tool_labels: Sequence[AITool] = ()) -> str:
    """Extract a current Rich AI answer without its display decoration."""
    text = message_text(message)
    marker = _ai_marker(message)
    if marker is None:
        return text
    if (battery_offset := _rich_footer_offset(message)) is not None:
        text = text[:battery_offset].removesuffix("\n")
    body = _strip_tool_label_prefix(text[len(marker.alternative_text) :].removeprefix(" "), tool_labels)
    rich = message.rich_message
    if rich is not None:
        first_block_body = _strip_tool_label_prefix(
            rich_block_text(rich.blocks[0])[len(marker.alternative_text) :].removeprefix(" "), tool_labels
        )
        if not first_block_body:
            body = body.removeprefix("\n")
    return body
