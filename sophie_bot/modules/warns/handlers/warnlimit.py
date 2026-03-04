from __future__ import annotations

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType

from sophie_bot.db.models.warns import WarnSettingsModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.utils_.status_handler import StatusIntHandlerABC
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(
    description=l_("Shows / changes the warn limit for this chat."),
)
class WarnLimitHandler(StatusIntHandlerABC):
    """Handler for viewing and changing the warn limit."""

    header_text = l_("Warn Limit")
    change_command = "warnlimit"
    change_args = l_("<number> (2-20)")
    min_value = 2
    max_value = 20
    default_value = 3

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("warnlimit"), UserRestricting(admin=True)

    async def get_status(self) -> int:
        chat_iid = self.connection.db_model.iid
        settings = await WarnSettingsModel.find_one(WarnSettingsModel.chat.id == chat_iid)
        if settings is None:
            return self.default_value
        return settings.max_warns

    async def set_status(self, new_status: int) -> None:
        chat_iid = self.connection.db_model.iid
        settings = await WarnSettingsModel.get_or_create(chat_iid)
        settings.max_warns = new_status
        await settings.save()
