from __future__ import annotations

from aiogram.types import Message

from sophie_bot.modules.ai.fsm.pm import AI_GENERATED_TEXT
from sophie_bot.modules.ai.utils.ai_header import AI_HEADER_LABEL, AI_HEADER_SEPARATOR


def _rich_block_text(block: object) -> str:
    """Flatten one rich block to text, joining table cells the way to_html() does."""
    cells = getattr(block, "cells", None)
    if isinstance(cells, list):
        return "\n".join(
            AI_HEADER_SEPARATOR.join(str(getattr(cell, "text", "") or "") for cell in row)
            for row in cells
            if isinstance(row, list)
        )

    block_text = getattr(block, "text", None)
    if isinstance(block_text, str):
        return block_text
    if isinstance(block_text, list):
        return "".join(item for item in block_text if isinstance(item, str))
    return ""


def message_text(message: Message) -> str:
    if message.text:
        return message.text

    rich = getattr(message, "rich_message", None)
    if rich is None:
        return ""
    return "\n".join(text for block in rich.blocks if (text := _rich_block_text(block)))


def is_ai_message(text: str) -> bool:
    """Whether a message carries Sophie's AI header, in any rendering it has ever had.

    The separator matters: without it "✨ AI Usage" and other AI-titled replies would match too,
    and replying to them would start a conversation.
    """
    first_line = text.split("\n", 1)[0].strip()
    if first_line == AI_HEADER_LABEL or first_line.startswith(AI_HEADER_LABEL + AI_HEADER_SEPARATOR):
        return True

    # Messages sent before the header became a table are still in chat history, and replying to one
    # has to keep working for as long as Telegram keeps it.
    return first_line.startswith((f"[{AI_GENERATED_TEXT}]", f"[{AI_HEADER_LABEL}]"))


def cut_titlebar(text: str) -> str:
    lines = text.split("\n")
    return lines[1] if len(lines) > 1 else ""
