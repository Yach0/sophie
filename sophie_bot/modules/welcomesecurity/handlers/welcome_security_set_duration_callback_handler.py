from __future__ import annotations

from datetime import timedelta
from typing import Any

from aiogram import F
from aiogram.dispatcher.event.handler import CallbackType

from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.welcomesecurity.callbacks import WelcomeSecuritySettingsCB
from sophie_bot.modules.welcomesecurity.utils.settings_panel_callback_context import _callback_context
from sophie_bot.modules.welcomesecurity.utils.settings_panel_constants import MEDIA_DURATION_OPTIONS
from sophie_bot.modules.welcomesecurity.utils.settings_panel_panel_keyboard import _panel_keyboard
from sophie_bot.modules.welcomesecurity.utils.settings_panel_panel_text import _panel_text
from sophie_bot.utils.handlers import SophieCallbackQueryHandler
from sophie_bot.utils.i18n import gettext as _


class WelcomeSecuritySetDurationCallbackHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WelcomeSecuritySettingsCB.filter(F.action == "set_duration"),
            UserRestricting(admin=True),
            FeatureFlagFilter("welcome_security_settings_panel", notify_callback=True),
        )

    async def handle(self) -> Any:
        context = await _callback_context(self)
        if context is None:
            return None
        chat, settings, _message = context
        value = self.callback_data.value
        if value not in {int(option.total_seconds()) for option in MEDIA_DURATION_OPTIONS}:
            return await self.event.answer(_("This duration option is no longer available."), show_alert=True)
        await settings.set_status_welcomemute(
            bool(settings.welcome_mute and settings.welcome_mute.enabled), timedelta(seconds=value)
        )
        await self.event.answer(_("Settings updated."))
        return await self.edit_text(
            await _panel_text(settings, chat.tid, self.current_locale, redis=self.services.redis),
            reply_markup=_panel_keyboard(settings, chat.iid),
        )
