from __future__ import annotations

import re

from aiogram.types import Message

from sophie_bot.constants import AI_EMOJI
from sophie_bot.modules.ai.fsm.pm import AI_GENERATED_TEXT
from sophie_bot.modules.ai.utils.ai_header import AI_HEADER_LABEL, AI_HEADER_SEPARATOR, AI_SIMPLE_HEADER_PREFIX
from sophie_bot.modules.ai.utils.ai_progress import AI_PROGRESS_MARKER


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
    """Whether a message is one of Sophie's AI messages, in any rendering it has ever had.

    Callers check the sender first, so this only has to tell Sophie's AI messages from her other
    ones. The separator matters: without it "✨ AI Usage" and other AI-titled replies would match
    too, and replying to them would start a conversation.
    """
    first_line = text.split("\n", 1)[0].strip()
    if first_line == AI_HEADER_LABEL or first_line.startswith(AI_HEADER_LABEL + AI_HEADER_SEPARATOR):
        return True
    if first_line == AI_SIMPLE_HEADER_PREFIX or first_line.startswith(AI_SIMPLE_HEADER_PREFIX + " "):
        return True
    if first_line.startswith(AI_EMOJI + " ") and "\n" in text:
        return text.rsplit("\n", 1)[-1].startswith("🔋 ")

    # An answer still being generated has no header yet — it is a plain progress line, and replying
    # to it has to continue the conversation just like replying to the finished message does.
    if first_line.startswith(AI_PROGRESS_MARKER + " "):
        return True

    # Messages sent before the header became a table are still in chat history, and replying to one
    # has to keep working for as long as Telegram keeps it.
    return first_line.startswith((f"[{AI_GENERATED_TEXT}]", f"[{AI_HEADER_LABEL}]"))


def cut_titlebar(text: str) -> str:
    simple_footer_match = re.match(rf"^{re.escape(AI_EMOJI)} (.+)\n+🔋 \d+%$", text, re.DOTALL)
    if simple_footer_match:
        return simple_footer_match.group(1)

    simple_match = re.match(
        rf"^{re.escape(AI_SIMPLE_HEADER_PREFIX)}(?: \d+% (?=\S)|\n|$)",
        text,
    )
    if simple_match:
        return text[simple_match.end() :]

    first_line, separator, body = text.partition("\n")
    if is_ai_message(first_line):
        return body.lstrip("\n") if separator else ""
    return text
