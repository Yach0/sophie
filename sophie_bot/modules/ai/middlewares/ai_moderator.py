from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import Message, TelegramObject
from mistralai.client.errors import SDKError
from openai import OpenAIError
from stfu_tg import Doc, Italic, KeyValue, Section, Title, UserLink, VList

from sophie_bot.config import CONFIG
from sophie_bot.constants import AI_MODERATION_NOTICE_DELETE_DELAY_SECONDS
from sophie_bot.db.models import AIModeratorModel, ChatModel
from sophie_bot.db.models.chat import ChatType
from sophie_bot.modules.ai.utils.ai_errors import AIErrorContext, capture_ai_error
from sophie_bot.modules.ai.utils.ai_mode import ModeCapabilities
from sophie_bot.modules.ai.utils.moderation import (
    MODERATION_CATEGORIES_TRANSLATES,
    ModerationCategory,
    check_moderator,
)
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.modules.utils_.delayed_delete import schedule_message_deletion
from sophie_bot.services.bot import bot
from sophie_bot.utils.feature_flags import get_value, is_enabled
from sophie_bot.utils.global_whitelist import is_user_globally_whitelisted
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import ngettext as pl_
from sophie_bot.utils.logger import log


async def _notice_delete_delay(chat_tid: int) -> int:
    value = await get_value("ai_moderation_notice_delete_after_seconds", chat_tid=chat_tid)
    if isinstance(value, bool):
        return AI_MODERATION_NOTICE_DELETE_DELAY_SECONDS
    try:
        delay = int(value)
    except (TypeError, ValueError):
        return AI_MODERATION_NOTICE_DELETE_DELAY_SECONDS
    return max(delay, 0)


class AiModeratorMiddleware(BaseMiddleware):
    @staticmethod
    async def _triggered(message: Message, categories: frozenset[ModerationCategory], chat_tid: int) -> None:
        await common_try(message.delete())

        delete_after = await _notice_delete_delay(chat_tid)

        doc = Doc(
            Title(_("✋ AI Moderator")),
            _("This message violates the AI moderator policy and therefore has been deleted."),
            KeyValue(_("Message author"), UserLink(message.from_user.id, message.from_user.first_name)),  # type: ignore
            Section(
                VList(
                    *(MODERATION_CATEGORIES_TRANSLATES.get(category, category) for category in categories),
                    prefix="- " if len(categories) > 1 else "",
                ),
                title=pl_("Reason", "Reasons", len(categories)),
            ),
        )
        if delete_after:
            doc += Italic(_("This message will be deleted shortly."))

        sent = await bot.send_message(message.chat.id, text=doc.to_html(), message_thread_id=message.message_thread_id)
        if delete_after:
            schedule_message_deletion(message.chat.id, [sent.message_id], delay_seconds=delete_after)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat_db: ChatModel | None = data.get("chat_db", None)
        log.debug("AiModeratorMiddleware: checking moderator...")

        capabilities: ModeCapabilities | None = data.get("ai_capabilities")

        if chat_db and chat_db.type != ChatType.private and capabilities and capabilities.moderator:
            if not isinstance(event, Message):
                return await handler(event, data)
            if not await is_enabled("ai_moderation", chat_tid=chat_db.tid):
                return await handler(event, data)
            settings = await AIModeratorModel.find_one(AIModeratorModel.chat.id == chat_db.iid)

            if not (event.text or event.caption or event.photo or event.audio):
                return await handler(event, data)

            if not event.from_user:
                return await handler(event, data)

            if await is_user_globally_whitelisted(event.from_user.id):
                return await handler(event, data)

            if CONFIG.debug_mode == "off" and await is_user_admin(chat_db.tid, event.from_user.id):
                return await handler(event, data)

            try:
                result = await check_moderator(event, settings=settings, chat_tid=chat_db.tid)
                if result.flagged:
                    await self._triggered(event, result.triggered, chat_db.tid)
                    raise SkipHandler
            except (SDKError, OpenAIError) as err:
                # The provider is already distinguishable from the exception type, so no flag lookup
                # is done here: this runs while the moderation backend is failing.
                capture_ai_error(err, AIErrorContext(operation="moderation"))

        return await handler(event, data)
