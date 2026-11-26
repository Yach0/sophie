from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import MessageEntityType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message, TelegramObject, Update
from pymongo.errors import PyMongoError

from sophie_bot.db.models.spam_match import SpamMatchModel
from sophie_bot.middlewares.request_context import RequestContext
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.group_whitelist import is_user_group_whitelisted
from sophie_bot.utils.group_whitelist_logging import log_group_whitelist_exemption
from sophie_bot.utils.logger import log
from sophie_bot.utils.spam_classifier import predict_message


class SpamDetectionMiddleware(BaseMiddleware):
    """Middleware that scans group messages for spam using USSR classifier."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)

        message = event.message or event.edited_message
        if message and message.chat.type in ("group", "supergroup"):
            await self._check_spam(message, data)

        return await handler(event, data)

    @staticmethod
    async def _check_spam(message: Message, data: dict[str, Any]) -> None:
        context: RequestContext = data["context"]
        chat_db = context.event_chat
        user_id = message.from_user.id if message.from_user else None
        chat_tid = message.chat.id

        if not user_id or not chat_db:
            return

        redis = data["services"].redis
        if await is_user_group_whitelisted(chat_tid, user_id, redis=redis):
            await log_group_whitelist_exemption(chat_tid, user_id, "spam_detection")
            return

        try:
            if await is_user_admin(chat_db.iid, user_id):
                return
        except (PyMongoError, TelegramAPIError):
            return

        text = message.text or message.caption
        if not text:
            return

        try:
            entities = message.entities if message.text else message.caption_entities
            links = tuple(
                entity.url for entity in entities or () if entity.type == MessageEntityType.TEXT_LINK and entity.url
            )
            precision = await is_enabled("spam_precision_classifier", chat_tid, redis=redis)
            result = await asyncio.to_thread(predict_message, text, links, precision=precision)
            spam_prob = result["spam_probability"]
            nsfw_prob = result["nsfw_probability"]

            if spam_prob > 0.5 or nsfw_prob > 0.5:
                log.info(
                    "Spam detection match",
                    text=text,
                    spam_probability=spam_prob,
                    nsfw_probability=nsfw_prob,
                )

                match = SpamMatchModel(
                    text=text,
                    spam_probability=spam_prob,
                    nsfw_probability=nsfw_prob,
                    chat_tid=chat_tid,
                )
                await match.insert()

        except Exception as exc:  # noqa: BLE001  # boundary: spam detection is best-effort, never blocks delivery
            log.warning("Spam detection failed", error=str(exc))
