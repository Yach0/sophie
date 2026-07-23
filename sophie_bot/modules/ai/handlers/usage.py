from __future__ import annotations

from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from stfu_tg import Bold, Code, Doc, Italic, KeyValue, Section, Template, Title, VList

from sophie_bot.constants import AI_CREDIT_EMOJI, AI_EMOJI
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.ai.filters.ai_mode import AICapabilityFilter
from sophie_bot.modules.ai.utils.ai_credit_text import format_credit_amount
from sophie_bot.modules.ai.utils.ai_usage_service import get_chat_usage_view
from sophie_bot.utils import flags
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Check AI quota and usage details"))
class AiUsage(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("aiusage"), AICapabilityFilter()

    async def handle(self) -> Any:
        chat_db = self.connection.db_model
        if not chat_db:
            return

        usage_view = await get_chat_usage_view(chat_db.iid)
        if not usage_view:
            await self.event.reply(_("AI quota information is not available yet."))
            return

        feature_lines = []
        for item in usage_view.breakdown:
            feature_lines.append(
                Template(
                    "{icon} {label}: {credit_emoji} {credits} ({percentage}%)",
                    icon=item.icon,
                    label=Bold(item.title),
                    credit_emoji=AI_CREDIT_EMOJI,
                    credits=Code(f"{item.credits:,}"),
                    percentage=Code(item.percentage),
                )
            )

        doc = Doc(
            Title(f"{AI_EMOJI} {_('AI Usage')}"),
            KeyValue(
                _("Usage"),
                Template(
                    _("{used} out of {total}"),
                    used=format_credit_amount(usage_view.used_credits),
                    total=format_credit_amount(usage_view.total_credits),
                ),
            ),
            KeyValue(
                _("Remaining"),
                Template(
                    _("{remaining_credits} ({percentage_remaining}%)"),
                    remaining_credits=format_credit_amount(usage_view.remaining_credits),
                    percentage_remaining=usage_view.percentage_remaining,
                ),
            ),
            KeyValue(_("Resets"), Italic(usage_view.period_end.strftime("%B %d, %Y"))),
        )

        if feature_lines:
            doc += Section(VList(*feature_lines), title=_("Feature breakdown"))
        else:
            doc += _("No credit usage data available yet")

        if usage_view.remaining_credits == 0:
            doc += Template(
                _("Quota exhausted! AI features are disabled until {date}."),
                date=Italic(usage_view.period_end.strftime("%B %d, %Y")),
            )

        await self.event.reply(str(doc))
