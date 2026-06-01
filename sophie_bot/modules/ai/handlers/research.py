from __future__ import annotations

from random import choice
from typing import Any, cast

from aiogram import Bot
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message
from ass_tg.types import TextArg
from stfu_tg import Doc

from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.ai.filters.ai_enabled import AIEnabledFilter
from sophie_bot.modules.ai.filters.quota import AIQuotaFilter
from sophie_bot.modules.ai.utils.ai_progress import ai_progress_line, random_ai_progress_custom_emoji_id
from sophie_bot.modules.ai.utils.chatbot_response import build_chatbot_header
from sophie_bot.modules.ai.utils.research import ResearchProgressStage, build_research_doc, run_research_workflow
from sophie_bot.utils import flags
from sophie_bot.utils.ai_features import AI_FEATURE_RESEARCH
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


def _research_progress_texts(stage: ResearchProgressStage) -> tuple[str, ...]:
    return {
        "planning": (
            _("Preparing search queries..."),
            _("Planning the research..."),
            _("Choosing what to search for..."),
        ),
        "searching": (
            _("Searching the internet..."),
            _("Looking it up online..."),
            _("Gathering sources from the web..."),
        ),
        "reviewing": (
            _("Reviewing search results..."),
            _("Checking if more searches are needed..."),
            _("Reading through the sources..."),
        ),
        "summarizing": (
            _("Summarizing the research..."),
            _("Putting the findings together..."),
            _("Preparing the final answer..."),
        ),
    }[stage]


_RESEARCH_PROGRESS_SUFFIXES: dict[ResearchProgressStage, str] = {
    "planning": "🧑‍🔬",
    "searching": "🔎",
    "reviewing": "🧐",
    "summarizing": "🧾",
}


class ResearchProgressMessage:
    def __init__(self, message: Message, emoji_id: str, bot: Bot) -> None:
        self.message = message
        self.emoji_id = emoji_id
        self.bot = bot

    @classmethod
    async def send(cls, source_message: Message) -> "ResearchProgressMessage":
        emoji_id = random_ai_progress_custom_emoji_id()
        message = await source_message.reply(
            Doc(ai_progress_line(_("Starting the research..."), emoji_id, "🧑‍🔬")).to_html(),
            disable_web_page_preview=True,
        )
        return cls(message, emoji_id, cast(Bot, source_message.bot))

    async def update(self, stage: ResearchProgressStage) -> None:
        text = choice(_research_progress_texts(stage))
        suffix = _RESEARCH_PROGRESS_SUFFIXES[stage]
        try:
            await self.bot.edit_message_text(
                text=Doc(ai_progress_line(text, self.emoji_id, suffix)).to_html(),
                chat_id=self.message.chat.id,
                message_id=self.message.message_id,
                disable_web_page_preview=True,
            )
        except TelegramAPIError:
            pass

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
            return await source_message.reply(doc.to_html(), disable_web_page_preview=True)


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
            AIEnabledFilter(),
            AIQuotaFilter(AI_FEATURE_RESEARCH),
        )

    async def handle(self) -> Any:
        prompt: str = self.data["text"]
        progress_message = await ResearchProgressMessage.send(self.event)
        result = await run_research_workflow(prompt, self.connection, progress_callback=progress_message.update)
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
