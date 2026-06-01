from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject, Update

from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.logger import log


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

    async def _check_spam(self, message: Message, data: dict[str, Any]) -> None:
        if not await is_enabled("ussr_spam_detection"):
            return

        from sophie_bot.modules.utils_.admin import is_user_admin

        chat_db = data.get("group_db") or data.get("chat_db")
        user_id = message.from_user.id if message.from_user else None

        if not user_id or not chat_db:
            return

        try:
            if await is_user_admin(chat_db.iid, user_id):
                return
        except Exception:
            return

        text = message.text or message.caption
        if not text:
            return

        try:
            from ussr import predict_message

            result = predict_message(text)
            if not result:
                return

            spam_prob = result.get("spam_probability", 0.0)
            nsfw_prob = result.get("nsfw_probability", 0.0)

            if spam_prob > 0.5 or nsfw_prob > 0.5:
                log.info(
                    "Spam detection match",
                    text=text,
                    spam_probability=spam_prob,
                    nsfw_probability=nsfw_prob,
                )

                if await is_enabled("ussr_spam_save_to_db"):
                    from sophie_bot.db.models.spam_match import SpamMatchModel

                    match = SpamMatchModel(
                        text=text,
                        spam_probability=spam_prob,
                        nsfw_probability=nsfw_prob,
                        chat_tid=message.chat.id,
                    )
                    await match.insert()

        except Exception as exc:
            log.warning("Spam detection failed", error=str(exc))
