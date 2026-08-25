from typing import Any

from aiogram import F, Router
from aiogram.dispatcher.event.handler import CallbackType

from sophie_bot.db.models import AIMemoryModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.ai.callbacks import AIResetContext
from sophie_bot.modules.ai.filters.ai_mode import AICapabilityFilter
from sophie_bot.modules.ai.fsm.pm import AI_PM_RESET
from sophie_bot.modules.ai.utils.cache_messages import reset_messages
from sophie_bot.modules.ai.utils.chatbot_tool_history import reset_tool_exchanges
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Reset the chat's AI context and AI memory"))
class AIContextReset(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("aireset"), UserRestricting(admin=True), AICapabilityFilter()

    @staticmethod
    def filters_alt() -> tuple[CallbackType, ...]:
        return F.text == AI_PM_RESET, UserRestricting(admin=True), AICapabilityFilter()

    @staticmethod
    def filters_callback() -> tuple[CallbackType, ...]:
        return AIResetContext.filter(), UserRestricting(admin=True), AICapabilityFilter()

    @classmethod
    def register(cls, router: Router) -> None:
        router.message.register(cls, *cls.filters(), flags={"args": cls.handler_args})
        router.message.register(cls, *cls.filters_alt(), flags={"args": cls.handler_args})

    async def handle(self) -> Any:
        await reset_messages(self.connection.tid)
        await reset_tool_exchanges(self.connection.tid)

        if self.connection.db_model:
            await AIMemoryModel.clear(self.connection.db_model.iid)

        return await self.event.reply(
            _("🔄 AI context and AI memory was successfully reset. AI will now operate in a clean state.")
        )
