from __future__ import annotations

from typing import Any

from aiogram import F
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import InlineKeyboardMarkup

from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.welcomesecurity.callbacks import WelcomeSecuritySettingsCB
from sophie_bot.modules.welcomesecurity.utils.settings_panel_button import _button
from sophie_bot.modules.welcomesecurity.utils.settings_panel_callback_context import _callback_context
from sophie_bot.utils.handlers import SophieCallbackQueryHandler
from sophie_bot.utils.i18n import gettext as _


class WelcomeSecurityResetConfirmCallbackHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WelcomeSecuritySettingsCB.filter(F.action == "reset_confirm"),
            UserRestricting(admin=True),
            FeatureFlagFilter("welcome_security_settings_panel", notify_callback=True),
        )

    async def handle(self) -> Any:
        context = await _callback_context(self)
        if context is None:
            return None
        chat, _settings, _message = context
        await self.event.answer()
        return await self.edit_text(
            _("Reset the security message to the default?"),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [_button(chat.iid, _("Reset"), "reset")],
                    [_button(chat.iid, _("Cancel"), "home")],
                ]
            ),
        )
