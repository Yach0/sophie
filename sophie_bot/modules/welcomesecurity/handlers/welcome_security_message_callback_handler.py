from __future__ import annotations

from typing import Any

from aiogram import F
from aiogram.dispatcher.event.handler import CallbackType

from sophie_bot.db.models import RulesModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.greetings.default_welcome import get_default_security_message
from sophie_bot.modules.notes.utils.send import send_saveable
from sophie_bot.modules.welcomesecurity.callbacks import WelcomeSecuritySettingsCB
from sophie_bot.modules.welcomesecurity.utils.settings_panel_callback_context import _callback_context
from sophie_bot.utils.handlers import SophieCallbackQueryHandler
from sophie_bot.utils.i18n import gettext as _


class WelcomeSecurityMessageCallbackHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WelcomeSecuritySettingsCB.filter(F.action == "message"),
            UserRestricting(admin=True),
            FeatureFlagFilter("welcome_security_settings_panel", notify_callback=True),
        )

    async def handle(self) -> Any:
        context = await _callback_context(self)
        if context is None:
            return None
        chat, settings, message = context
        await self.event.answer()
        rules = await RulesModel.get_rules(chat.iid)
        return await send_saveable(
            message,
            message.chat.id,
            settings.security_note or get_default_security_message(),
            additional_fillings={"rules": rules.text or "" if rules else _("No chat rules, have fun!")},
            connection=self.connection,
            owner_chat_tid=chat.tid,
            bot=self.services.bot,
        )
