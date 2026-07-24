from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import Message, TelegramObject

from sophie_bot.db.models import ChatModel, WSUserModel
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.logger import log

GROUP_CHAT_TYPES = ("group", "supergroup")


class LockMutedUsers(BaseMiddleware):
    """Deletes group messages of users who joined but have not passed the captcha yet.

    Telegram's own restriction is the primary gate; this is the backstop for when it
    could not be applied (Sophie was not an admin at join time, the restriction was
    lifted manually, and so on).
    """

    @staticmethod
    async def _is_message_locked(message: Message, data: dict[str, Any]) -> bool:
        if not message.from_user or message.chat.type not in GROUP_CHAT_TYPES:
            return False

        chat_db: ChatModel = data["chat_db"]
        user_db: ChatModel | None = data.get("user_db")

        # Absent for anonymous admins, who are exempt anyway
        if not user_db:
            return False

        if not await is_enabled("welcomecaptcha", chat_tid=chat_db.tid):
            return False

        log.debug("LockMutedUsers", chat=chat_db.tid, user=user_db.tid)

        if await is_user_admin(chat_db.tid, user_db.tid):
            return False

        ws_user = await WSUserModel.is_user(user_db.iid, chat_db.iid)
        return ws_user is not None and not ws_user.passed

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and await self._is_message_locked(event, data):
            await common_try(event.delete())
            raise SkipHandler

        return await handler(event, data)
