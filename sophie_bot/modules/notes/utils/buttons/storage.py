from __future__ import annotations

from typing import Optional, cast

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from sophie_bot.db.models.button_action import ButtonAction
from sophie_bot.db.models.notes_buttons import Button, ButtonStyle
from sophie_bot.modules.notes.utils.buttons.models import ButtonLayout
from sophie_bot.modules.notes.utils.buttons_processor.ass_types.SophieButtonABC import AssButtonData
from sophie_bot.modules.notes.utils.buttons_processor.registry import ASS_MAPPING


def buttons_from_ass(buttons: list[AssButtonData]) -> ButtonLayout:
    layout = ButtonLayout()
    if not buttons:
        return layout

    current_row: list[Button] = []
    for ass_button in buttons:
        button = Button(
            text=ass_button.title,
            action=cast(ButtonAction, ASS_MAPPING.get(ass_button.button_type, ass_button.button_type)),
            data=ass_button.arguments[0] or None if ass_button.arguments else None,
            style=ass_button.style,
        )

        if ass_button.same_row and current_row:
            current_row.append(button)
        else:
            if current_row:
                layout.append(current_row)
            current_row = [button]

    if current_row:
        layout.append(current_row)

    return layout


class UnknownMessageButtonTypeError(Exception):
    pass


def button_from_markup(button: InlineKeyboardButton) -> Optional[Button]:
    if button.url:
        action = ButtonAction.url
        data = button.url
    else:
        raise UnknownMessageButtonTypeError(button)

    return Button(
        text=button.text,
        action=action,
        data=data,
        style=cast(ButtonStyle | None, button.style),
    )


def button_row_from_markup(row: list[InlineKeyboardButton]) -> list[Button]:
    try:
        return list(filter(None, map(button_from_markup, row)))
    except UnknownMessageButtonTypeError:
        return []


def buttons_from_markup(markup: InlineKeyboardMarkup) -> ButtonLayout:
    return ButtonLayout(
        [parsed_row for parsed_row in map(button_row_from_markup, markup.inline_keyboard) if parsed_row]
    )
