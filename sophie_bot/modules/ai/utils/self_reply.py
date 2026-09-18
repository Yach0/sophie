from __future__ import annotations

import re
from collections.abc import Sequence

from aiogram.types import Message

from sophie_bot.constants import AI_EMOJI
from sophie_bot.modules.ai.fsm.pm import AI_GENERATED_TEXT
from sophie_bot.modules.ai.utils.ai_progress import AI_PROGRESS_MARKER

_LEGACY_AI_HEADER_LABEL = f"{AI_EMOJI} AI"
_LEGACY_AI_HEADER_SEPARATOR = " | "
_LEGACY_SIMPLE_HEADER_PREFIX = f"{AI_EMOJI} 🔋"
_BATTERY_MARKER_PATTERN = r'(?:<tg-emoji emoji-id="\d+">)?🔋(?:</tg-emoji>)?'
_SIMPLE_FOOTER_PATTERN = re.compile(rf"\n+{_BATTERY_MARKER_PATTERN}(?: \d+%)?(?: \([^()\n]+\))?\s*$")


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


def is_ai_message(text: str) -> bool:
    """Whether a message is one of Sophie's AI messages, including old header layouts."""
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


def cut_titlebar(text: str, *, tool_labels: Sequence[str] = ()) -> str:
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
