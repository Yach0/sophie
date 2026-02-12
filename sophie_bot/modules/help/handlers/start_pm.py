from typing import Any

from aiogram import F, Router, flags
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions
from stfu_tg import Doc, Template, Url

from sophie_bot.config import CONFIG
from sophie_bot.filters.chat_status import ChatTypeFilter
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.message_status import NoArgs
from sophie_bot.modules.help.callbacks import PMHelpModules
from sophie_bot.modules.privacy import PrivacyMenuCallback
from sophie_bot.utils.handlers import SophieMessageCallbackQueryHandler
from sophie_bot.utils.i18n import gettext as _


@flags.help(exclude=True)
class StartPMHandler(SophieMessageCallbackQueryHandler):
    @classmethod
    def register(cls, router: Router):
        router.message.register(cls, CMDFilter("start"), ChatTypeFilter("private"), NoArgs(True))
        router.callback_query.register(cls, ChatTypeFilter("private"), F.data == "go_to_start")

    async def handle(self) -> Any:
        state: FSMContext = self.state

        # Reset current state
        await state.clear()

        buttons = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=_("Add me to your chat"),
                        url=f"https://telegram.me/{CONFIG.username}?startgroup=true",
                        style="success",
                        icon_custom_emoji_id="5397916757333654639",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=_("Privacy"),
                        callback_data=PrivacyMenuCallback(back_to_start=True).pack(),
                        icon_custom_emoji_id="5377754411319698237",
                    ),
                    InlineKeyboardButton(
                        text=_("Language"), callback_data="lang_btn", icon_custom_emoji_id="5447410659077661506"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text=_("Help"),
                        callback_data=PMHelpModules(back_to_start=True).pack(),
                        icon_custom_emoji_id="5373098009640836781",
                    )
                ],
            ]
        )

        text = Doc(
            _("Hey there! My name is Sophie, I help you manage your group in an efficient way!"),
            Template(
                _("Join our {chat} and {channel}."),
                chat=Url(_("💬 Support Chat"), CONFIG.support_link),
                channel=Url(_("📢 NEWS Channel"), CONFIG.news_channel),
            ),
        )

        await self.answer(str(text), reply_markup=buttons, link_preview_options=LinkPreviewOptions(is_disabled=True))
