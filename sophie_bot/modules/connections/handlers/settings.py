from __future__ import annotations

from abc import ABC

from aiogram import flags

from sophie_bot.db.models.chat_connection_settings import ChatConnectionSettingsModel
from sophie_bot.modules.utils_.status_handler import StatusBoolHandlerABC
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Sets whether normal users (non-admins) are allowed to connect."))
class AllowUsersConnectCmd(StatusBoolHandlerABC, ABC):
    header_text = l_("Allow users connect")
    change_command = "allowusersconnect"

    @staticmethod
    def filters():
        from sophie_bot.filters.cmd import CMDFilter
        from sophie_bot.filters.chat_status import ChatTypeFilter
        from sophie_bot.filters.admin_rights import UserRestricting

        return (
            CMDFilter("allowusersconnect"),
            ChatTypeFilter("group", "supergroup"),
            UserRestricting(admin=True),
        )

    async def get_status(self) -> bool:
        settings = await ChatConnectionSettingsModel.get_by_chat_iid(self.connection.db_model.iid)
        return settings.allow_users_connect if settings else False

    async def set_status(self, new_status: bool):
        chat_iid = self.connection.db_model.iid
        settings = await ChatConnectionSettingsModel.get_by_chat_iid(chat_iid)
        if not settings:
            settings = ChatConnectionSettingsModel(chat=chat_iid, allow_users_connect=new_status)
            await settings.insert()
        else:
            settings.allow_users_connect = new_status
            await settings.save()
