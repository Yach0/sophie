from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel
from sophie_bot.db.models.ai.ai_evidence_memory import AIEvidenceMemoryModel
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.ai.filters.bot_mention import AIBotMentionFilter
from sophie_bot.modules.ai.utils.ai_mode import get_capabilities, resolve_chat_mode
from sophie_bot.modules.ai.utils.cache_messages import CachedMedia, cache_message, get_context_epoch_key
from sophie_bot.modules.ai.utils.proactive_replies import maybe_run_proactive_reply
from sophie_bot.modules.ai.utils.self_reply import is_ai_message
from sophie_bot.modules.ai.utils.support_replies import maybe_run_support_reply
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.logger import log


class CacheUserMessagesMiddleware(BaseMiddleware):
    def __init__(self, excluded_commands: tuple[CMDFilter, ...]) -> None:
        self.excluded_commands = excluded_commands

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

        expected_epoch = None
        if isinstance(event, Message) and chat_db and capabilities.message_cache:
            expected_epoch = int(await data["services"].redis.get(get_context_epoch_key(chat_db.tid)) or 0)
        data["ai_message_cache_epoch"] = expected_epoch
        memory_enabled = bool(
            chat_db
            and capabilities.memory
            and await is_enabled("ai_memory_writer", chat_tid=chat_db.tid, redis=data["services"].redis)
        )
        excluded_users: list[int] = []
        if memory_enabled and chat_db:
            memory = await AIEvidenceMemoryModel.find_one(AIEvidenceMemoryModel.chat.id == chat_db.iid)
            excluded_users = memory.state.excluded_users if memory else []
        support_proactive = bool(
            isinstance(event, Message)
            and event.chat.type in {"group", "supergroup"}
            and chat_db
            and mode == AIMode.support
            and await is_enabled("ai_proactive_support", chat_tid=chat_db.tid, redis=data["services"].redis)
        )
        data["ai_support_proactive"] = support_proactive

        result = await handler(event, data)
        if data.get("ai_moderation_blocked"):
            return result

        if (
            isinstance(event, Message)
            and chat_db
            and (event.from_user or event.sender_chat)
            and capabilities.message_cache
        ):
            text = event.text or event.caption or ""
            media = CachedMedia.from_message(event)
            if not text and media is None:
                return result

            # Check command syntax even when a feature or permission filter blocks its handler.
            for command_filter in self.excluded_commands:
                if (
                    text
                    and text[0] in command_filter.prefix
                    and await command_filter(event, data["services"].bot, event.chat)
                ):
                    log.debug("CacheUserMessagesMiddleware: skipping command excluded from AI history")
                    return result

            sender = event.from_user
            sender_chat = event.sender_chat
            if sender:
                user_id, username = sender.id, sender.username or sender.full_name
            elif sender_chat and support_proactive:
                user_id, username = sender_chat.id, sender_chat.title
            else:
                return result
            msg_id = event.message_id
            log.debug("CacheUserMessagesMiddleware: caching message", chat_id=chat_db.tid)
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
            addressed_to_bot = bool(await AIBotMentionFilter()(event, data["services"].bot, mode))
            eligible_for_proactive_ai = bool(
                not handled_by_ai
                and not has_ai_command
                and not is_command
                and not reply_to_is_sophie_ai
                and not addressed_to_bot
                and event.chat.type in {"group", "supergroup"}
            )
            await cache_message(
                text,
                chat_db.tid,
                user_id,
                msg_id,
                event.date,
                username,
                is_bot=bool(sender and sender.is_bot),
                from_sender_chat=event.sender_chat is not None,
                message_thread_id=event.message_thread_id,
                handled_by_ai=handled_by_ai,
                eligible_for_proactive_ai=eligible_for_proactive_ai,
                reply_to_message_id=(reply_to_message.message_id if reply_to_message else None),
                reply_to_user_id=(reply_to_user.id if reply_to_user else None),
                reply_to_username=(reply_to_user.username or reply_to_user.full_name if reply_to_user else None),
                reply_to_is_sophie_ai=reply_to_is_sophie_ai,
                has_ai_command=has_ai_command,
                is_ai_filter_reply=bool(data.get("ai_filter_handled", False)),
                eligible_for_memory=bool(
                    memory_enabled
                    and bool(text)
                    and not (event.text and event.text[0] in CONFIG.commands_prefix)
                    and sender is not None
                    and not sender.is_bot
                    and not event.sender_chat
                    and user_id not in excluded_users
                ),
                speaker_name=sender.full_name if sender else "",
                expected_epoch=expected_epoch,
                media=media,
                redis=data["services"].redis,
            )

            if eligible_for_proactive_ai and capabilities.proactive_replies:
                await maybe_run_proactive_reply(event, chat_db, services=data["services"])
            elif support_proactive and not (handled_by_ai or is_command or has_ai_command) and text.strip():
                await maybe_run_support_reply(event, chat_db, services=data["services"])

        return result
