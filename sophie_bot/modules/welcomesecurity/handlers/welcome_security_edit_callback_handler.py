from __future__ import annotations

from typing import Any

from aiogram import F
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import ForceReply

from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.welcomesecurity.callbacks import WelcomeSecuritySettingsCB
from sophie_bot.modules.welcomesecurity.fsm import WelcomeSecurityFSM
from sophie_bot.modules.welcomesecurity.utils.settings_panel_callback_context import _callback_context
from sophie_bot.utils.handlers import SophieCallbackQueryHandler
from sophie_bot.utils.i18n import gettext as _


class WelcomeSecurityEditCallbackHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WelcomeSecuritySettingsCB.filter(F.action == "edit"),
            UserRestricting(admin=True),
            FeatureFlagFilter("welcome_security_settings_panel", notify_callback=True),
        )

    async def handle(self) -> Any:
        context = await _callback_context(self)
        if context is None:
            return None
        chat, _settings, message = context
        if await self.state.get_state() is not None:
            return await self.event.answer(_("Finish your current action or use /cancel first."), show_alert=True)
        prompt = await message.answer(
            _(
                "Reply to this message with the new security message, or use /cancel. You can include supported media and buttons."
            ),
            reply_markup=ForceReply(selective=True),
        )
        await self.state.set_state(WelcomeSecurityFSM.security_message)
        await self.state.update_data(ws_settings_chat_iid=str(chat.iid), ws_settings_prompt_id=prompt.message_id)
        return await self.event.answer()
