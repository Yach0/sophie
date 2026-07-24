from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from stfu_tg import Doc, KeyValue, Section, Title

from sophie_bot.db.models import ChatModel
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.user_status import IsOP
from sophie_bot.modules.ai.schedules.generate_chat_summaries import GenerateChatSummaries
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


def _current_summary_date() -> date:
    return datetime.now(UTC).date()


@flags.help(description=l_("Force-regenerate today's chat summary for the current chat"))
class OpRegenerateChatSummaryHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("op_regenerate_chat_summary"), IsOP(True)

    async def handle(self) -> Any:
        chat_tid = self.event.chat.id
        summary_date = _current_summary_date()
        chat = await ChatModel.get_by_tid(chat_tid)
        if not chat:
            await self.event.reply(Doc(Title(_("Chat not found"))).to_html())
            return

        await GenerateChatSummaries().process_chat(chat, summary_date, force=True, target_chat_tid=chat_tid)
        await self.event.reply(
            Doc(
                Title(_("Chat summary regenerated")),
                Section(
                    KeyValue(_("Chat ID"), chat_tid),
                    KeyValue(_("Summary day"), summary_date.isoformat()),
                ),
            ).to_html()
        )
