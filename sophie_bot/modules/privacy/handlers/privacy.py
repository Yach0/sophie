from typing import Any

from aiogram import Router
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from sophie_bot.config import CONFIG
from sophie_bot.filters.chat_status import ChatTypeFilter
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageCallbackQueryHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

from ..callbacks import PrivacyMenuCallback


@flags.help(description=l_("Shows the privacy policy of the bot"))
class PrivacyMenu(SophieMessageCallbackQueryHandler):
    @classmethod
    def register(cls, router: Router) -> None:
        router.message.register(cls, CMDFilter("privacy"), ChatTypeFilter("private"))
        router.callback_query.register(cls, PrivacyMenuCallback.filter())

    async def handle(self) -> Any:
        callback_data: PrivacyMenuCallback | None = self.data.get("callback_data", None)

        text = _("The privacy policy of the bot is available on our wiki page.")
        buttons = InlineKeyboardBuilder().add(
            InlineKeyboardButton(
                text=_("Privacy Policy"),
                url=CONFIG.privacy_link,
            )
        )

        if callback_data and callback_data.back_to_start:
            buttons.row(InlineKeyboardButton(text=_("⬅️ Back"), callback_data="go_to_start"))

        await self.answer(text, reply_markup=buttons.as_markup())
