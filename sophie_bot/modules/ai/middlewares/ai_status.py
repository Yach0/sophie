from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.flags import get_flag
from aiogram.types import Message, TelegramObject
from aiogram.utils.chat_action import ChatActionSender

from sophie_bot.services.bot import bot
from sophie_bot.utils.feature_flags import is_enabled


class AiStatusMiddleware(BaseMiddleware):
    """Sends continuous typing status for handlers decorated with @flags.status('typing').

    Telegram stops showing the typing indicator after ~5 seconds, so ChatActionSender
    re-sends it periodically for the duration of the handler execution.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        status = get_flag(data, "status", default=None)
        if status != "typing" or not isinstance(event, Message):
            return await handler(event, data)

        is_ai_chatbot_response = bool(get_flag(data, "ai_chatbot_response", default=False))
        if (
            is_ai_chatbot_response
            and event.chat.type != "private"
            and await is_enabled("ai_chatbot_thinking_message", chat_tid=event.chat.id)
        ):
            return await handler(event, data)

        async with ChatActionSender.typing(
            bot=bot,
            chat_id=event.chat.id,
            message_thread_id=event.message_thread_id,
        ):
            return await handler(event, data)
