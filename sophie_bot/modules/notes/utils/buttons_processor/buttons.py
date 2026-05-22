from typing import Optional

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from sophie_bot.db.models.notes_buttons import Button
from sophie_bot.modules.notes.utils.buttons.models import ButtonLayout
from sophie_bot.modules.notes.utils.buttons.parser import parse_buttons_from_text
from sophie_bot.modules.notes.utils.buttons.renderer import render_buttons
from sophie_bot.modules.notes.utils.buttons.storage import (
    UnknownMessageButtonTypeError,
    button_from_markup,
    button_row_from_markup,
    buttons_from_ass,
    buttons_from_markup,
)
from sophie_bot.modules.notes.utils.buttons_processor.ass_types.SophieButtonABC import AssButtonData

__all__ = (
    "Button",
    "ButtonsList",
    "UnknownMessageButtonTypeError",
    "parse_message_button",
    "parse_message_buttons",
    "parse_message_buttons_row",
)


class ButtonsList(ButtonLayout):
    @staticmethod
    def from_ass(buttons: list[AssButtonData]) -> "ButtonsList":
        return ButtonsList(buttons_from_ass(buttons))

    @staticmethod
    def from_markup(markup: InlineKeyboardMarkup) -> "ButtonsList":
        return ButtonsList(buttons_from_markup(markup))

    @staticmethod
    async def from_text(text: str) -> "ButtonsList":
        return ButtonsList(await parse_buttons_from_text(text))

    def unparse(self, chat_id: int) -> InlineKeyboardMarkup:
        return render_buttons(self, chat_id)


def parse_message_button(button: InlineKeyboardButton) -> Optional[Button]:
    return button_from_markup(button)


def parse_message_buttons_row(row: list[InlineKeyboardButton]) -> list[Button]:
    return button_row_from_markup(row)


def parse_message_buttons(reply_markup: InlineKeyboardMarkup) -> list[list[Button]]:
    return buttons_from_markup(reply_markup)
