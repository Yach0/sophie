from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from sophie_bot.db.models.notes_buttons import Button
from sophie_bot.modules.notes.utils.buttons.renderer import render_button, render_buttons


def unparse_button(button: Button, chat_id: int) -> InlineKeyboardButton | None:
    return render_button(button, chat_id)


def unparse_buttons(buttons: list[list[Button]], chat_id: int) -> InlineKeyboardMarkup:
    return render_buttons(buttons, chat_id)
