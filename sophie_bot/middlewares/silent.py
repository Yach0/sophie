from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, TelegramObject

from sophie_bot.config import CONFIG
from sophie_bot.services.bot import bot
from sophie_bot.services.redis import aredis
from sophie_bot.utils.feature_flags import FeatureType, is_enabled

TTL_SECONDS = 86400  # 24 hours


def _redis_key(chat_id: int, user_id: int) -> str:
    return f"silent:{chat_id}:{user_id}"


def _is_command(text: str) -> bool:
    """Check whether the message text starts with any configured command prefix."""
    return any(text.startswith(prefix) for prefix in CONFIG.commands_prefix)


class SilentMiddleware(BaseMiddleware):
    """Outer middleware that auto-deletes previous command/reply messages for
    users who have silent mode enabled."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or not event.text or not event.from_user:
            return await handler(event, data)

        if not _is_command(event.text):
            return await handler(event, data)

        feature: FeatureType = "silent_mode"
        if not await is_enabled(feature, chat_tid=event.chat.id):
            return await handler(event, data)

        chat_id = event.chat.id
        user_id = event.from_user.id
        redis_key = _redis_key(chat_id, user_id)

        # --- Pre-handler: delete previous command + reply messages ---
        raw = await aredis.get(redis_key)
        if raw:
            state = json.loads(raw)
            if state.get("enabled"):
                for msg_key in ("last_cmd_msg_id", "last_reply_msg_id"):
                    msg_id = state.get(msg_key)
                    if msg_id is None:
                        continue
                    try:
                        await bot.delete_message(chat_id, msg_id)
                    except TelegramBadRequest:
                        pass

                # Reset stored IDs so we don't try deleting them again
                await aredis.set(redis_key, json.dumps({"enabled": True}), ex=TTL_SECONDS)

        # --- Wrap event.reply to capture the bot's reply message_id ---
        captured_reply_id: int | None = None
        original_reply = event.reply

        async def wrapped_reply(*args: Any, **kwargs: Any) -> Any:
            result = await original_reply(*args, **kwargs)
            nonlocal captured_reply_id
            if isinstance(result, Message):
                captured_reply_id = result.message_id
            return result

        event.reply = wrapped_reply  # ty: ignore[invalid-assignment]

        # --- Run the handler ---
        try:
            result = await handler(event, data)
        finally:
            event.reply = original_reply  # ty: ignore[invalid-assignment]

        # --- Post-handler: store new command and reply message IDs ---
        raw = await aredis.get(redis_key)
        if raw:
            state = json.loads(raw)
            if state.get("enabled"):
                new_state: dict[str, Any] = {
                    "enabled": True,
                    "last_cmd_msg_id": event.message_id,
                }
                if captured_reply_id is not None:
                    new_state["last_reply_msg_id"] = captured_reply_id
                await aredis.set(redis_key, json.dumps(new_state), ex=TTL_SECONDS)

        return result
