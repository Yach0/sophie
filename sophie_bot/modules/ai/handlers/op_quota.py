from __future__ import annotations

from typing import Any

from aiogram import flags
from aiogram.dispatcher.event.handler import CallbackType
from ass_tg.types import IntArg

from sophie_bot.constants import AI_CREDIT_EMOJI, AI_EMOJI
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.user_status import IsOP
from sophie_bot.modules.ai.utils.ai_credit_text import format_credit_amount
from sophie_bot.modules.ai.utils.ai_quota import get_quota_info, reset_period_usage, set_monthly_quota
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_
from stfu_tg import Code, Doc, KeyValue, Section, Template, Title


@flags.args(
    credits=IntArg(l_("Monthly credit amount")),
)
@flags.help(description=l_("Set monthly AI quota for a chat"))
class SetQuota(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("op_aisetquota"), IsOP(True)

    async def handle(self) -> Any:
        credits: int = self.data["credits"]
        connection = self.connection

        if credits < 0:
            await self.event.reply(
                str(Template(_("{credit_emoji} amount must be a positive number."), credit_emoji=AI_CREDIT_EMOJI))
            )
            return

        await set_monthly_quota(connection.db_model, credits)
        quota_info = await get_quota_info(connection.db_model.iid)

        doc = Doc(
            Title(f"{AI_EMOJI} {_('AI Quota Updated')}"),
            Section(
                KeyValue(_("Monthly quota"), Code(format_credit_amount(credits))),
                KeyValue(
                    _("Remaining"),
                    Code(format_credit_amount(quota_info.remaining_credits)) if quota_info else Code("N/A"),
                ),
                title=Template(_("New quota for {chat}"), chat=connection.title),
            ),
        )
        await self.event.reply(str(doc))


@flags.help(description=l_("Reset AI quota usage for a chat"))
class ResetQuota(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("op_airesetquota"), IsOP(True)

    async def handle(self) -> Any:
        connection = self.connection
        await reset_period_usage(connection.db_model.iid)

        quota_info = await get_quota_info(connection.db_model.iid)
        doc = Doc(
            Title(f"{AI_EMOJI} {_('AI Quota Reset')}"),
            Template(_("Quota usage has been reset for this period.")),
            Template(
                _("New remaining: {remaining}"),
                remaining=Code(format_credit_amount(quota_info.remaining_credits)) if quota_info else Code("N/A"),
            ),
        )
        await self.event.reply(str(doc))
