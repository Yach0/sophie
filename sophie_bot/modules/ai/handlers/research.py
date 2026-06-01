from __future__ import annotations

from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.exceptions import TelegramBadRequest
from ass_tg.types import OptionalArg, TextArg
from stfu_tg import Doc, HList, PreformattedHTML, Section, Template

from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.ai.filters.ai_enabled import AIEnabledFilter
from sophie_bot.modules.ai.filters.quota import AIQuotaFilter
from sophie_bot.modules.ai.utils.research_agent import run_research_workflow
from sophie_bot.utils import flags
from sophie_bot.utils.ai_features import AI_FEATURE_RESEARCH
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.args(
    text=OptionalArg(TextArg(l_("Topic"))),
)
@flags.help(description=l_("Run a multi-stage research workflow on a topic"))
@flags.status("typing")
@flags.ai_chatbot_response()
@flags.ai_cache(cache_handler_result=True)
@flags.disableable(name="research")
class AiResearch(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("research"),
            FeatureFlagFilter("ai_research"),
            AIEnabledFilter(),
            AIQuotaFilter(AI_FEATURE_RESEARCH),
        )

    async def handle(self) -> Any:
        topic: str | None = self.data.get("text")
        if not topic:
            await self.event.reply(str(_("Usage: /research <topic>")))
            return

        progress_msg = await self.event.reply(str(Template(_("🔬 Starting research on: {topic}"), topic=topic)))

        async def on_progress(status: str) -> None:
            try:
                await progress_msg.edit_text(status)
            except TelegramBadRequest:
                pass

        report = await run_research_workflow(
            topic=topic,
            chat_tid=self.connection.tid,
            chat_iid=self.connection.db_model.iid,
            connection=self.connection,
            on_progress=on_progress,
        )

        doc = Doc()
        doc += Section(PreformattedHTML(report.summary), title=str(Template(_("🔬 {title}"), title=report.title)))
        for section in report.sections:
            doc += Section(PreformattedHTML(section["body"]), title=section["heading"])
        if report.sources:
            doc += Section(
                HList(
                    *(
                        Template('<a href="{url}">{title}</a>', url=source["url"], title=source["title"])
                        for source in report.sources
                    ),
                    divider=" | ",
                ),
                title=str(_("Sources")),
            )

        await progress_msg.edit_text(str(doc), parse_mode="HTML")
