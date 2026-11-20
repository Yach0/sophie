from typing import Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.dispatcher.middlewares.manager import MiddlewareManager
from aiogram.types import Message

from sophie_bot.modules.ai.filters.bot_mention import AIBotMentionFilter
from sophie_bot.modules.ai.filters.quota import AIQuotaFilter
from sophie_bot.modules.ai.utils.ai_chatbot_reply import ai_chatbot_reply
from sophie_bot.modules.antiflood.middlewares.enforcer import AntifloodEnforcerMiddleware
from sophie_bot.modules.filters.enforce_middleware import EnforceFiltersMiddleware
from sophie_bot.modules.locks.middlewares.enforcer import LocksEnforcerMiddleware
from sophie_bot.utils import flags
from sophie_bot.utils.ai_features import AI_FEATURE_CHATBOT
from sophie_bot.utils.handlers import SophieMessageHandler


@flags.status(value="typing")
@flags.ai_chatbot_response()
@flags.ai_cache(cache_handler_result=True)
class AiMentionHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (AIBotMentionFilter(),)

    async def handle(self) -> Any:
        # These routers follow AI in production and would otherwise never see a
        # handled mention. Keep their enforcement order and module availability.
        enforcers: list[BaseMiddleware] = [
            middleware
            for module, middleware in (
                ("filters", EnforceFiltersMiddleware()),
                ("antiflood", AntifloodEnforcerMiddleware(self.services)),
                ("locks", LocksEnforcerMiddleware()),
            )
            if module in self.services.modules.modules
        ]
        reply = MiddlewareManager.wrap_middlewares(enforcers, self._reply)
        try:
            return await reply(self.event, self.data)
        except SkipHandler:
            # Enforcement and quota rejection both finish this mention. Do not
            # let the observer run later routers and repeat moderation actions.
            return None

    async def _reply(self, _message: Message, **_data: object) -> Any:
        # Reuse the quota filter's notice and data contract, after moderation.
        quota_result = await AIQuotaFilter(AI_FEATURE_CHATBOT)(self.event, self.context, self.services)
        if not quota_result:
            return None
        if isinstance(quota_result, dict):
            self.data.update(quota_result)
        self.context.ai_message_handled = True
        return await ai_chatbot_reply(
            self.event,
            self.connection,
            self.data["ai_mention_prompt"],
            mode=self.data["ai_mode"],
            services=self.services,
        )
