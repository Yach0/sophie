"""Plain text extraction shared by rich-message consumers.

Rich text takes precedence over Telegram's plain fallback. Captions remain a
caller concern, and entity offsets must be applied to their original source.
"""

from __future__ import annotations

from typing import cast

from aiogram.types import Message, RichBlock, RichBlockUnion, RichMessage

from sophie_bot.utils.rich_message import rich_message_to_plain_text


def rich_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(rich_text(item) for item in value)

    alternative_text = getattr(value, "alternative_text", None)
    if isinstance(alternative_text, str):
        return alternative_text

    nested_text = getattr(value, "text", None)
    return rich_text(nested_text) if nested_text is not None else ""


def rich_block_text(block: object, *, table_cell_separator: str = " | ") -> str:
    # AI context keeps the full visible projection, including nested blocks,
    # captions, and labels. Locks request cell boundaries without display markup.
    if table_cell_separator == " | " and isinstance(block, RichBlock):
        return rich_message_to_plain_text(RichMessage(blocks=[cast("RichBlockUnion", block)]))

    cells = getattr(block, "cells", None)
    if isinstance(cells, list):
        return "\n".join(
            table_cell_separator.join(rich_text(cell) for cell in row) for row in cells if isinstance(row, list)
        )

    return rich_text(getattr(block, "text", None))


def message_text(message: Message | object, *, table_cell_separator: str = " | ") -> str:
    rich = getattr(message, "rich_message", None)
    if rich is not None:
        rich_text = "\n".join(
            text for block in rich.blocks if (text := rich_block_text(block, table_cell_separator=table_cell_separator))
        )
        if rich_text:
            return rich_text

    text = getattr(message, "text", None)
    return text if isinstance(text, str) else ""
