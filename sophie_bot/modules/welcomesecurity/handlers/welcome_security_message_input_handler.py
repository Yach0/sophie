from __future__ import annotations

from typing import Any

from aiogram import F
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.filters import StateFilter

from sophie_bot.db.models import GreetingsModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.notes.utils.parse import parse_saveable
from sophie_bot.modules.welcomesecurity.fsm import WelcomeSecurityFSM
from sophie_bot.modules.welcomesecurity.utils.settings_panel_panel_keyboard import _panel_keyboard
from sophie_bot.modules.welcomesecurity.utils.settings_panel_panel_text import _panel_text
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _


class WelcomeSecurityMessageInputHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            StateFilter(WelcomeSecurityFSM.security_message),
            F.reply_to_message,
            ~F.text.startswith("/"),
            UserRestricting(admin=True),
            FeatureFlagFilter("welcome_security_settings_panel"),
        )

    async def handle(self) -> Any:
        data = await self.state.get_data()
        chat = self.connection.db_model
        if self.connection.type == "private" or data["ws_settings_chat_iid"] != str(chat.iid):
            await self.state.clear()
            return await self.event.reply(
                _("These settings are no longer active. Open /welcome_security again for the intended chat.")
            )
        if not self.event.reply_to_message or self.event.reply_to_message.message_id != data["ws_settings_prompt_id"]:
            return await self.event.reply(_("Reply to the security-message prompt, or use /cancel."))
        saveable = await parse_saveable(
            self.event,
            None if self.event.rich_message else self.event.text or self.event.caption,
            allow_reply_message=False,
            album=self.data.get("album"),
            bot=self.services.bot,
        )
        if not (saveable.text or saveable.file or saveable.files or saveable.rich_message):
            return await self.event.reply(_("Send text or supported media for the security message."))
        await GreetingsModel.change_security_message(chat.iid, saveable)
        await self.state.clear()
        settings = await GreetingsModel.get_by_chat_iid(chat.iid)
        return await self.event.reply(
            str(await _panel_text(settings, chat.tid, self.current_locale, redis=self.services.redis)),
            reply_markup=_panel_keyboard(settings, chat.iid),
        )
