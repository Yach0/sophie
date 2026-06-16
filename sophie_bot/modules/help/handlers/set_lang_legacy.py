from typing import Any

from aiogram import F
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sophie_bot.modules.language.handlers.language import SelectLangCb
from sophie_bot.utils.handlers import SophieCallbackQueryHandler
from sophie_bot.utils.i18n import get_i18n, gettext as _


class SetLangLegacyHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (F.data == "lang_btn",)

    async def handle(self) -> Any:
        i18n = get_i18n()
        buttons = []

        for code in i18n.available_locales:
            locale = i18n.babels.get(code)
            display_name = i18n.locale_display(locale) if locale else code
            buttons.append(InlineKeyboardButton(text=display_name, callback_data=SelectLangCb(code=code).pack()))

        keyboard = InlineKeyboardMarkup(inline_keyboard=[buttons[i : i + 2] for i in range(0, len(buttons), 2)])

        if self.event.message and isinstance(self.event.message, Message):
            await self.event.message.edit_text(text=_("Select your language:"), reply_markup=keyboard)
