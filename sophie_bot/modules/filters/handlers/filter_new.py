from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import TextArg
from ass_tg.types.base_abc import ArgFabric
from stfu_tg import Template

from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.filters.is_connected import GroupOrConnectedFilter
from sophie_bot.modules.filters.filter_wizard import FILTER_WIZARD, FilterDraft
from sophie_bot.modules.filters.utils_.filter_handler_rules import InvalidFilterHandler, validate_filter_handler
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Adds a new filter"))
class FilterNewHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter(("addfilter", "newfilter")),
            FeatureFlagFilter("action_config_wizard"),
            FeatureFlagFilter("filters"),
            UserRestricting(admin=True),
            GroupOrConnectedFilter(),
        )

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        return {"handler": TextArg(l_("Text to match"))}

    async def handle(self) -> Any:
        keyword: str = self.data["handler"]
        try:
            await validate_filter_handler(self.connection.db_model.iid, keyword)
        except InvalidFilterHandler as error:
            await self.answer_rich(error.document or Template(error.message))
            return
        await FILTER_WIZARD.start(self, FilterDraft(handler=keyword))
