from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware, Bot
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import MessageReactionUpdated, TelegramObject

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.modules.locks.utils.cache import get_cached_locks
from sophie_bot.modules.locks.utils.lock_types import LockType
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.logger import log


OUTSIDER_STATUSES = {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}


class ReactionLocksEnforcerMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, MessageReactionUpdated):
            return await handler(event, data)
        if event.chat.type == ChatType.PRIVATE:
            return await handler(event, data)
        if not event.new_reaction:
            return await handler(event, data)
        if not event.user:
            return await handler(event, data)
        chat_tid = event.chat.id
        if not await is_enabled("locks", chat_tid=chat_tid):
            return await handler(event, data)

        chat = await ChatModel.get_by_tid(chat_tid)
        if not chat:
            return await handler(event, data)

        locked_types = await get_cached_locks(chat_tid, chat.iid)
        if not locked_types or LockType.OUTSIDE_REACTION not in locked_types:
            return await handler(event, data)

        bot: Bot = data["bot"]
        try:
            member = await bot.get_chat_member(chat_id=chat_tid, user_id=event.user.id)
        except TelegramAPIError as exc:
            log.debug("Failed to get reaction sender chat member", error=str(exc), chat_tid=chat_tid)
            return await handler(event, data)

        if member.status not in OUTSIDER_STATUSES:
            return await handler(event, data)

        try:
            await bot.set_message_reaction(chat_id=chat_tid, message_id=event.message_id, reaction=[])
        except TelegramAPIError as exc:
            log.debug("Failed to remove outsider reaction", error=str(exc), chat_tid=chat_tid)
            return await handler(event, data)

        raise SkipHandler
