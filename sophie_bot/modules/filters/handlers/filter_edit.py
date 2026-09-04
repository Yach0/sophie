from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import TextArg
from ass_tg.types.base_abc import ArgFabric
from stfu_tg import Code, Template

from sophie_bot.db.models import FiltersModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.filters.is_connected import GroupOrConnectedFilter
from sophie_bot.modules.filters.filter_wizard import FILTER_WIZARD, FilterDraft
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Edits filter settings"))
class FilterEditHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("editfilter"),
            FeatureFlagFilter("action_config_wizard"),
            FeatureFlagFilter("filters"),
            UserRestricting(admin=True),
            GroupOrConnectedFilter(),
        )

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        return {"handler": TextArg(l_("Filter's keyword"))}

    async def handle(self) -> Any:
        keyword: str = self.data["handler"]
        filter_item = await FiltersModel.get_by_keyword(self.connection.db_model.iid, keyword)
        if filter_item is None:
            return await self.event.reply(
                str(
                    Template(
                        _("Filter with handler {handler} can not be found! Check all filters using {cmd} command"),
                        handler=Code(keyword),
                        cmd=Code("/filters"),
                    )
                )
            )
        await FILTER_WIZARD.start(self, FilterDraft.from_model(filter_item))
