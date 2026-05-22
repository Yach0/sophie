from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from sophie_bot.db.models.notes_buttons import Button
from sophie_bot.modules.notes.utils.buttons.renderer import render_buttons


class ButtonLayout(list[list[Button]]):
    def render(self, chat_tid: int) -> InlineKeyboardMarkup:
        return render_buttons(self, chat_tid)
