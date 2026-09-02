from __future__ import annotations

from contextlib import suppress
from typing import Any, cast

from aiogram import Bot
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message
from ass_tg.types import TextArg
from stfu_tg import Doc

from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.ai.filters.ai_mode import AICapabilityFilter
from sophie_bot.modules.ai.filters.quota import AIQuotaFilter
from sophie_bot.modules.ai.utils.ai_progress import ai_progress_line, random_ai_progress_custom_emoji_id
from sophie_bot.modules.ai.utils.chatbot_response import build_chatbot_header
from sophie_bot.modules.ai.utils.research import (
    ResearchProgressStage,
    build_research_doc,
    random_research_progress_text,
    research_progress_suffix,
    run_research_workflow,
)
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.utils import flags
from sophie_bot.utils.ai_features import AI_FEATURE_RESEARCH
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_
from sophie_bot.utils.logger import log


class ResearchProgressMessage:
    def __init__(self, message: Message, emoji_id: str, bot: Bot) -> None:
        self.message = message
        self.emoji_id = emoji_id
        self.bot = bot

    @classmethod
    async def send(cls, source_message: Message) -> ResearchProgressMessage:
        emoji_id = random_ai_progress_custom_emoji_id()
        message = await source_message.reply(
            Doc(ai_progress_line(_("Starting the research..."), emoji_id, "🧑‍🔬")).to_html(),
            disable_web_page_preview=True,
        )
        return cls(message, emoji_id, cast(Bot, source_message.bot))

    async def update(self, stage: ResearchProgressStage) -> None:
        text = random_research_progress_text(stage)
        suffix = research_progress_suffix(stage)
        with suppress(TelegramAPIError):
            await self.bot.edit_message_text(
                text=Doc(ai_progress_line(text, self.emoji_id, suffix)).to_html(),
                chat_id=self.message.chat.id,
                message_id=self.message.message_id,
                disable_web_page_preview=True,
            )

    async def send_final(self, source_message: Message, doc: Doc) -> Message:
        try:
            edited_message = await self.bot.edit_message_text(
                text=doc.to_html(),
                chat_id=self.message.chat.id,
                message_id=self.message.message_id,
                disable_web_page_preview=True,
            )
            if isinstance(edited_message, Message):
                return edited_message
            return self.message
        except TelegramAPIError:
            sent = await common_try(source_message.reply(doc.to_html(), disable_web_page_preview=True))
            return sent if isinstance(sent, Message) else self.message


@flags.args(
    text=TextArg(l_("Research topic")),
)
@flags.help(description=l_("Research a topic using multistage web search and return a summary with sources"))
@flags.status("typing")
@flags.ai_cache(cache_handler_result=True)
@flags.disableable(name="research")
class ResearchCmd(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("research"),
            FeatureFlagFilter("ai_research"),
            AICapabilityFilter(),
            AIQuotaFilter(AI_FEATURE_RESEARCH),
        )

    async def handle(self) -> Any:
        # Disconnect from any connected group before running research so that quota
        # is charged to the private chat, not the connected group.
        if self.event.chat.type == "private" and self.connection.is_connected and self.event.from_user:
            from sophie_bot.middlewares.connections import ConnectionsMiddleware
            from sophie_bot.modules.connections.utils.connection import set_connected_chat

            await set_connected_chat(self.event.from_user.id, None)
            await self.event.reply(_("You have been automatically disconnected from the chat to use AI."))
            self.data["connection"] = await ConnectionsMiddleware.get_current_chat_info(self.event.chat)

        prompt: str = self.data["text"]
        progress_message = await ResearchProgressMessage.send(self.event)
        try:
            result = await run_research_workflow(prompt, self.connection, progress_callback=progress_message.update)
        except SophieException as exc:
            log.warning("research: SophieException during workflow", error=str(exc))
            await self.event.reply(str(exc))
            return
        header = await build_chatbot_header(
            self.connection.db_model.iid,
            result.model,
            result.message_history,
        )
        current_locale = self.data["i18n"].current_locale
        return await progress_message.send_final(
            self.event,
            build_research_doc(result.response, header=header, current_locale=current_locale),
        )
