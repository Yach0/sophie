from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message

from sophie_bot.config import CONFIG
from sophie_bot.modules.ai.filters.ai_mode import AICapabilityFilter
from sophie_bot.modules.ai.filters.quota import AIQuotaFilter
from sophie_bot.modules.ai.utils.ai_chatbot_reply import ai_chatbot_reply
from sophie_bot.modules.ai.utils.self_reply import is_ai_message
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.utils import flags
from sophie_bot.utils.ai_features import AI_FEATURE_CHATBOT
from sophie_bot.utils.handlers import SophieMessageHandler


@flags.status(value="typing")
@flags.ai_chatbot_response()
@flags.ai_cache(cache_handler_result=True)
class AiReplyHandler(SophieMessageHandler):
    @staticmethod
    async def filter(message: Message, ai_support_proactive: bool = False) -> bool:
        parent = message.reply_to_message
        if message.pinned_message or not parent or not parent.from_user:
            return False
        if parent.from_user.id != CONFIG.bot_id or not is_ai_message(parent):
            return False
        if not ai_support_proactive:
            return True

        sender = message.from_user
        return bool(
            sender and not sender.is_bot and not message.sender_chat and await is_user_admin(message.chat.id, sender.id)
        )

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            AiReplyHandler.filter,
            AICapabilityFilter(lambda capabilities: capabilities.trigger_on_reply, quiet=True),
            AIQuotaFilter(AI_FEATURE_CHATBOT),
        )

    async def handle(self) -> Any:
        self.data["ai_message_handled"] = True
        return await ai_chatbot_reply(
            self.event,
            self.connection,
            mode=self.data["ai_mode"],
            services=self.services,
        )
