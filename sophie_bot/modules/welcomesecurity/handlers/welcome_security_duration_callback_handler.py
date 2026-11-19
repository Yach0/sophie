from __future__ import annotations

from typing import Any

from aiogram import F
from aiogram.dispatcher.event.handler import CallbackType

from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.welcomesecurity.callbacks import WelcomeSecuritySettingsCB
from sophie_bot.modules.welcomesecurity.utils.settings_panel_callback_context import _callback_context
from sophie_bot.modules.welcomesecurity.utils.settings_panel_duration_keyboard import _duration_keyboard
from sophie_bot.utils.handlers import SophieCallbackQueryHandler
from sophie_bot.utils.i18n import gettext as _


class WelcomeSecurityDurationCallbackHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WelcomeSecuritySettingsCB.filter(F.action == "duration"),
            UserRestricting(admin=True),
            FeatureFlagFilter("welcome_security_settings_panel", notify_callback=True),
        )

    async def handle(self) -> Any:
        context = await _callback_context(self)
        if context is None:
            return None
        chat, settings, _message = context
        await self.event.answer()
        return await self.edit_text(
            _("Choose how long new members cannot send media."),
            reply_markup=_duration_keyboard(settings, chat.iid, self.current_locale, expiry=False),
        )
