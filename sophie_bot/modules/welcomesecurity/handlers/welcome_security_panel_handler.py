from __future__ import annotations

from typing import Any

from aiogram import F
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.filters import MagicData

from sophie_bot.db.models import GreetingsModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.welcomesecurity.utils.settings_panel_panel_keyboard import _panel_keyboard
from sophie_bot.modules.welcomesecurity.utils.settings_panel_panel_text import _panel_text
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _


@flags.handler_help(exclude=True)
@flags.disableable(name="welcomesecurity")
class WelcomeSecurityPanelHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter(("welcome_security", "welcome_captcha", "enable_welcome_captcha", "welcome_restrict")),
            MagicData(~F.command.args),
            UserRestricting(admin=True),
            FeatureFlagFilter("welcome_security_settings_panel"),
        )

    async def handle(self) -> Any:
        if self.connection.type == "private":
            return await self.event.reply(_("Connect to a group before opening Welcome Security settings."))
        chat = self.connection.db_model
        settings = await GreetingsModel.get_by_chat_iid(chat.iid)
        return await self.event.reply(
            str(await _panel_text(settings, chat.tid, self.current_locale, redis=self.services.redis)),
            reply_markup=_panel_keyboard(settings, chat.iid),
        )
