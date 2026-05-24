from __future__ import annotations

from typing import Any

from ass_tg.types.base_abc import ParsedArg

from sophie_bot.modules.notes.utils.buttons_processor.ass_types.SophieButtonABC import AssButtonData
from sophie_bot.modules.notes.utils.buttons_processor.buttons import ButtonsList


def parse_text_with_buttons(
    data: dict[str, Any],
) -> tuple[str | None, int, ButtonsList]:
    """Extract text, offset, and buttons from text_with_buttons ASS data.

    Returns:
        Tuple of (raw_text, text_offset, buttons_list).
    """
    text_with_buttons: dict[str, Any] = data.get("text_with_buttons", {})

    raw_text_parsed: ParsedArg[str] | None = text_with_buttons.get("text")
    raw_text = raw_text_parsed.value if raw_text_parsed else None
    text_offset = raw_text_parsed.offset if raw_text_parsed else 0

    raw_buttons_parsed: ParsedArg[list[AssButtonData]] | None = text_with_buttons.get("buttons")
    raw_buttons = raw_buttons_parsed.value if raw_buttons_parsed else []
    buttons = ButtonsList.from_ass(raw_buttons)

    return raw_text, text_offset, buttons
