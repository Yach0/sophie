from aiogram.types import InlineKeyboardMarkup

from sophie_bot.db.models.notes_buttons import Button
from sophie_bot.modules.notes.utils.buttons.models import ButtonLayout
from sophie_bot.modules.notes.utils.buttons.parser import parse_buttons_from_text
from sophie_bot.modules.notes.utils.buttons.renderer import render_buttons
from sophie_bot.modules.notes.utils.buttons.storage import buttons_from_ass
from sophie_bot.modules.notes.utils.buttons_processor.ass_types.sophie_button_abc import AssButtonData

__all__ = (
    "Button",
    "ButtonsList",
)


class ButtonsList(ButtonLayout):
    @staticmethod
    def from_ass(buttons: list[AssButtonData]) -> "ButtonsList":
        return ButtonsList(buttons_from_ass(buttons))

    @staticmethod
    async def from_text(text: str) -> "ButtonsList":
        return ButtonsList(await parse_buttons_from_text(text))

    def unparse(self, chat_id: int) -> InlineKeyboardMarkup:
        return render_buttons(self, chat_id)
