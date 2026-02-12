from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import Message, TelegramObject

from sophie_bot.config import CONFIG
from sophie_bot.db.models import GreetingsModel


class LeaveUserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # TODO: Handle multiple users add

        if isinstance(event, Message) and event.left_chat_member:
            user_id = event.left_chat_member.id

            # Bot left the chat
            if user_id == CONFIG.bot_id:
                # TODO: Delete chat data?
                raise SkipHandler

            chat_db = data["chat_db"]
            db_item: GreetingsModel = await GreetingsModel.get_by_chat_iid(chat_db.iid)

            # Cleanservice
            if db_item.clean_service and db_item.clean_service.enabled:
                await event.delete()

            # Skip handler
            raise SkipHandler

        return await handler(event, data)
