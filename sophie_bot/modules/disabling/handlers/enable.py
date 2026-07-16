from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from ass_tg.types import WordArg
from beanie import PydanticObjectId
from stfu_tg import Code, Italic, KeyValue, Section, Template

from sophie_bot.db.models import DisablingModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.disabling.utils.get_disabled import resolve_disableable_cmd
from sophie_bot.modules.help.utils.format_help import format_cmd
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.args(cmd=WordArg(l_("Command")))
@flags.help(description=l_("Enables previously disabled command."))
class EnableHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("enable"), UserRestricting(admin=True)

    @staticmethod
    async def enable_cmd(chat_iid: PydanticObjectId, cmd: str):
        return await DisablingModel.enable(chat_iid, cmd)

    async def handle(self) -> Any:
        connection = self.connection
        cmd_name: str = self.data["cmd"].lower().removeprefix("/").removeprefix("!")

        resolved = resolve_disableable_cmd(cmd_name)

        if not resolved:
            await self.event.reply(str(Template(_("Command {cmd} not found."), cmd=Code("/" + cmd_name))))
            return

        cmd_key, handler = resolved

        if cmd_key not in await DisablingModel.get_disabled(connection.db_model.iid):
            await self.event.reply(str(Template(_("Command {cmd} is not disabled."), cmd=Code("/" + cmd_name))))
            return

        await self.enable_cmd(connection.db_model.iid, cmd_key)

        await self.event.reply(
            str(
                Section(
                    KeyValue(_("Chat"), connection.title),
                    KeyValue(_("Command"), format_cmd(handler.cmds[0])),
                    Italic(handler.description) if handler.description else None,
                    title=_("Command enabled"),
                )
            )
        )
