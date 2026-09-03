from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import TextArg
from ass_tg.types.base_abc import ArgFabric

from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.filters.is_connected import GroupOrConnectedFilter
from sophie_bot.modules.filters.action_config import _filter_cfg
from sophie_bot.modules.filters.filter_wizard import start_filter_wizard
from sophie_bot.modules.filters.utils_.filter_handler_rules import validate_filter_handler
from sophie_bot.modules.utils_.action_config_wizard.config import ActionWizardDraft
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Adds a new filter"))
class FilterNewHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter(("addfilter", "newfilter")),
            FeatureFlagFilter("filters"),
            UserRestricting(admin=True),
            GroupOrConnectedFilter(),
        )

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        return {"handler": TextArg(l_("Text to match"))}

    async def handle(self) -> Any:
        keyword: str = self.data["handler"]
        if not await validate_filter_handler(self.event, keyword, self.connection):
            return
        draft = ActionWizardDraft(
            metadata={"handler": keyword, "silent": False},
        )
        await start_filter_wizard(self, draft, _filter_cfg)
