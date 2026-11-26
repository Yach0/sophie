from __future__ import annotations

from asyncio import gather
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType, ContentType
from aiogram.types import Message, TelegramObject

from sophie_bot.middlewares.request_context import RequestContext
from sophie_bot.modules.antispam.service import AntispamService
from sophie_bot.services.application import ApplicationServices


class AntispamMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or event.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            return await handler(event, data)
        if not event.from_user:
            return await handler(event, data)

        context: RequestContext = data["context"]
        services: ApplicationServices = data["services"]
        protection = await AntispamService.for_chat(services, context.event_chat) if context.event_chat else None
        if protection is None:
            return await handler(event, data)

        if event.new_chat_members:
            results = await gather(
                *(protection.protect(member, message=event, joining=True) for member in event.new_chat_members)
            )
            removed = frozenset(
                member.id for member, stopped in zip(event.new_chat_members, results, strict=True) if stopped
            )
            context.antispam_removed_joiners = removed
            humans = {member.id for member in event.new_chat_members if not member.is_bot}
            if removed and (humans or {member.id for member in event.new_chat_members}) <= removed:
                await protection.delete_message(event)
                return None
        elif event.content_type in USER_MESSAGE_TYPES and await protection.protect(
            event.from_user,
            message=event,
            membership=context.user_in_group,
        ):
            return None

        return await handler(event, data)


USER_MESSAGE_TYPES = frozenset(
    {
        ContentType.TEXT,
        ContentType.ANIMATION,
        ContentType.AUDIO,
        ContentType.DOCUMENT,
        ContentType.LIVE_PHOTO,
        ContentType.PAID_MEDIA,
        ContentType.PHOTO,
        ContentType.STICKER,
        ContentType.STORY,
        ContentType.VIDEO,
        ContentType.VIDEO_NOTE,
        ContentType.VOICE,
        ContentType.CONTACT,
        ContentType.DICE,
        ContentType.GAME,
        ContentType.POLL,
        ContentType.VENUE,
        ContentType.LOCATION,
        ContentType.CHECKLIST,
        ContentType.INVOICE,
        ContentType.RICH_MESSAGE,
    }
)
