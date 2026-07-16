from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from ass_tg.types import OptionalArg, TextArg
from stfu_tg import Italic, Template

from sophie_bot.db.models import RulesModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.chat_status import ChatTypeFilter
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.notes.utils.parse import parse_saveable
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Sets chat rules"))
@flags.args(
    content=OptionalArg(TextArg(l_("Content"), parse_entities=True)),
)
class SetRulesHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("setrules"), ~ChatTypeFilter("private"), UserRestricting(admin=True)

    async def handle(self) -> Any:
        connection = self.connection
        content: str = self.data["content"]

        saveable = await parse_saveable(self.event, content)

        # `content` is optional because the rules may come from a replied message instead,
        # so emptiness can only be judged once both sources have been parsed.
        if not saveable.text and not saveable.file and not saveable.files:
            return await self.event.reply(
                _("Please provide the rules text, or reply to the message you want to set as the rules.")
            )

        await RulesModel.set_rules(connection.db_model.iid, saveable)

        await self.event.reply(
            str(
                Template(
                    _("🪧 Rules have been set in {chat_name} successfully."),
                    chat_name=Italic(connection.title),
                )
            )
        )
