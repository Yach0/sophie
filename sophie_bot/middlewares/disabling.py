from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import Message, TelegramObject

from sophie_bot.db.models.disabling import DisablingModel
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.utils.flags import get_disableable_name
from sophie_bot.utils.logger import log


class DisablingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            chat_db = data["chat_db"]
            disabled = await DisablingModel.get_disabled(chat_db.iid)

            data["disabled"] = disabled
            log.debug("DisablingMiddleware", chat_id=chat_db.tid, disabled=disabled)

            if disableable_name := get_disableable_name(data):
                if event.from_user:
                    user_id = event.from_user.id
                    is_admin = await is_user_admin(chat_db.iid, user_id)
                else:
                    is_admin = False

                if disableable_name in disabled and not is_admin:
                    log.debug("DisablingMiddleware: disabled; Skipping handler!")
                    raise SkipHandler
                if is_admin:
                    log.debug("DisablingMiddleware: user is admin; Not skipping!")

        return await handler(event, data)
