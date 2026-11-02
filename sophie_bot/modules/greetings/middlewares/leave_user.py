from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import Message, TelegramObject

from sophie_bot.config import CONFIG
from sophie_bot.db.models import GreetingsModel
from sophie_bot.modules.utils_.common_try import common_try


class LeaveUserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # TODO: Handle multiple users add

        if isinstance(event, Message) and event.left_chat_member:
            user_id = event.left_chat_member.id

            # Bot left the chat
            if user_id == CONFIG.bot_id:
                # TODO: Delete chat data?
                raise SkipHandler

            chat_db = data["context"].event_chat
            db_item: GreetingsModel = await GreetingsModel.get_by_chat_iid(chat_db.iid)

            # Cleanservice
            if db_item.clean_service and db_item.clean_service.enabled:
                await common_try(event.delete())

            # Skip handler
            raise SkipHandler

        return await handler(event, data)
