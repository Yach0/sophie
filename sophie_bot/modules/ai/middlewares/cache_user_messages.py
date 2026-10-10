from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aiogram import BaseMiddleware
from aiogram.filters.command import CommandObject
from aiogram.types import Message, TelegramObject

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.utils.ai_mode import get_capabilities, resolve_chat_mode
from sophie_bot.modules.ai.utils.cache_messages import cache_message, get_context_epoch_key
from sophie_bot.modules.ai.utils.proactive_replies import maybe_run_proactive_reply
from sophie_bot.modules.ai.utils.self_reply import is_ai_message
from sophie_bot.utils.logger import log


@dataclass(slots=True)
class MessageCacheState:
    command: CommandObject | None = None


class CacheUserMessagesMiddleware(BaseMiddleware):
    @staticmethod
    async def capture_command(
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # Inner middleware runs after filters. Share the identity with the outer middleware,
        # since aiogram copies the data dictionary while routing to a matched handler.
        cache_state: MessageCacheState = data["ai_message_cache_state"]
        cache_state.command = data.get("command")
        return await handler(event, data)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat_db: ChatModel | None = data["context"].event_chat

        mode = await resolve_chat_mode(chat_db, data.get("state")) if chat_db else AIMode.disabled
        capabilities = get_capabilities(mode)
        data["ai_mode"] = mode
        data["ai_capabilities"] = capabilities

        cache_state = MessageCacheState()
        data["ai_message_cache_state"] = cache_state
        expected_epoch = None
        if isinstance(event, Message) and chat_db and capabilities.message_cache:
            expected_epoch = int(await data["services"].redis.get(get_context_epoch_key(chat_db.tid)) or 0)
        result = await handler(event, data)

        if isinstance(event, Message) and chat_db and event.from_user and capabilities.message_cache:
            text = event.text or event.caption
            if not text:
                return result

            if cache_state.command and cache_state.command.command == "ai_reset":
                log.debug("CacheUserMessagesMiddleware: skipping reset command")
                return result

            user_id = event.from_user.id
            msg_id = event.message_id
            log.debug("CacheUserMessagesMiddleware: caching message", chat_id=chat_db.tid)
            username = event.from_user.username or event.from_user.full_name
            reply_to_message = event.reply_to_message
            reply_to_user = reply_to_message.from_user if reply_to_message else None
            reply_to_is_sophie_ai = bool(
                reply_to_message
                and reply_to_user
                and reply_to_user.id == CONFIG.bot_id
                and is_ai_message(reply_to_message)
            )
            has_ai_command = text.startswith("/ai") or " /ai" in text
            handled_by_ai = bool(data.get("ai_message_handled", False))
            is_command = text.startswith("/")
            eligible_for_proactive_ai = bool(
                not handled_by_ai
                and not has_ai_command
                and not is_command
                and not reply_to_is_sophie_ai
                and event.chat.type in {"group", "supergroup"}
            )
            await cache_message(
                text,
                chat_db.tid,
                user_id,
                msg_id,
                event.date,
                username,
                is_bot=event.from_user.is_bot,
                message_thread_id=event.message_thread_id,
                handled_by_ai=handled_by_ai,
                eligible_for_proactive_ai=eligible_for_proactive_ai,
                reply_to_message_id=(reply_to_message.message_id if reply_to_message else None),
                reply_to_user_id=(reply_to_user.id if reply_to_user else None),
                reply_to_username=(reply_to_user.username or reply_to_user.full_name if reply_to_user else None),
                reply_to_is_sophie_ai=reply_to_is_sophie_ai,
                has_ai_command=has_ai_command,
                is_ai_filter_reply=bool(data.get("ai_filter_handled", False)),
                expected_epoch=expected_epoch,
                redis=data["services"].redis,
            )
            if eligible_for_proactive_ai and capabilities.proactive_replies:
                await maybe_run_proactive_reply(event, chat_db, services=data["services"])

        return result
