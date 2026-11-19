from __future__ import annotations

from typing import Any

from aiogram import F
from aiogram.dispatcher.event.handler import CallbackType

from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.welcomesecurity.callbacks import WelcomeSecuritySettingsCB
from sophie_bot.modules.welcomesecurity.utils.settings_panel_callback_context import _callback_context
from sophie_bot.modules.welcomesecurity.utils.settings_panel_panel_keyboard import _panel_keyboard
from sophie_bot.modules.welcomesecurity.utils.settings_panel_panel_text import _panel_text
from sophie_bot.utils.handlers import SophieCallbackQueryHandler


class WelcomeSecurityHomeCallbackHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WelcomeSecuritySettingsCB.filter(F.action == "home"),
            UserRestricting(admin=True),
            FeatureFlagFilter("welcome_security_settings_panel", notify_callback=True),
        )

    async def handle(self) -> Any:
        context = await _callback_context(self)
        if context is None:
            return None
        chat, settings, _message = context
        await self.event.answer(None)
        return await self.edit_text(
            await _panel_text(settings, chat.tid, self.current_locale, redis=self.services.redis),
            reply_markup=_panel_keyboard(settings, chat.iid),
        )
