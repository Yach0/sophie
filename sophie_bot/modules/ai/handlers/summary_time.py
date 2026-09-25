from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import OptionalArg, WordArg
from ass_tg.types.base_abc import ArgFabric
from stfu_tg import KeyValue, Section, Template

from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


def _normalize_utc_time(raw_time: str) -> str | None:
    try:
        return datetime.strptime(raw_time, "%H:%M").replace(tzinfo=UTC).strftime("%H:%M")
    except ValueError:
        return None


@flags.handler_help(description=l_("Sets the daily AI chat summary generation time in UTC"))
class AIChatSummariesTimeSetting(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("ai_summaries_time"), UserRestricting(admin=True)

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict[str, Any]) -> dict[str, ArgFabric]:
        return {"summary_time": OptionalArg(WordArg(l_("UTC time (HH:MM)")))}

    async def handle(self) -> Any:
        chat = self.connection.db_model
        if chat is None:
            return None

        raw_time: str | None = self.data.get("summary_time")
        if raw_time is None:
            return await self.event.reply(
                Section(
                    KeyValue(_("Current UTC time"), chat.ai_summary_time_utc),
                    title=_("AI Chat Summary Schedule"),
                ).to_html()
            )

        summary_time = _normalize_utc_time(raw_time)
        if summary_time is None:
            return await self.event.reply(Template(_("Invalid UTC time. Use HH:MM, for example 07:45.")).to_html())

        await chat.set_ai_summary_time_utc(summary_time)
        return await self.event.reply(
            Section(
                KeyValue(_("New UTC time"), summary_time),
                KeyValue(_("Chat"), self.connection.title),
                title=_("AI Chat Summary Schedule"),
            ).to_html()
        )
