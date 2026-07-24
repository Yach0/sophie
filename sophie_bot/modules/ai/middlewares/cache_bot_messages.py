from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.flags import get_flag
from aiogram.types import Message, TelegramObject
from pydantic import BaseModel

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel
from sophie_bot.modules.ai.utils.ai_mode import ModeCapabilities
from sophie_bot.modules.ai.utils.cache_messages import cache_message
from sophie_bot.modules.ai.utils.self_reply import cut_titlebar, is_ai_message
from sophie_bot.utils.logger import log


class MessageType(BaseModel):
    user_id: int
    message_id: int
    text: str


class CacheBotMessagesMiddleware(BaseMiddleware):
    @staticmethod
    def get_key(chat_id: int | str) -> str:
        return f"messages:{chat_id}"

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        result = await handler(event, data)
        chat_db: ChatModel | None = data.get("chat_db", None)

        capabilities: ModeCapabilities | None = data.get("ai_capabilities")

        sent_message_text = result.text if isinstance(result, Message) else None
        sent_message_id = result.message_id if isinstance(result, Message) else None

        ai_cache_flag = get_flag(data, "ai_cache", default={})
        cache_handler_result = ai_cache_flag.get("cache_handler_result", False)

        to_cache: str | None = sent_message_text if cache_handler_result else None

        if capabilities and capabilities.message_cache and to_cache and sent_message_id and chat_db:
            if is_ai_message(to_cache):
                to_cache = cut_titlebar(to_cache)

            log.debug("CacheBotMessagesMiddleware: caching message", message=to_cache)
            created_at = (
                result.date
                if isinstance(result, Message)
                else event.date
                if isinstance(event, Message)
                else datetime.now(UTC)
            )
            await cache_message(
                to_cache,
                chat_db.tid,
                CONFIG.bot_id,
                sent_message_id,
                created_at,
                "Sophie",
                message_thread_id=result.message_thread_id if isinstance(result, Message) else None,
                handled_by_ai=True,
                eligible_for_proactive_ai=False,
            )

        return result
