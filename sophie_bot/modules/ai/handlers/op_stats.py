from __future__ import annotations

from aiogram import Router
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from stfu_tg import Bold, Code, Doc, KeyValue, Section, Template, UserLink
from stfu_tg.doc import Element

from sophie_bot.constants import AI_CREDIT_EMOJI
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.user_status import IsOP
from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.modules.ai.utils.ai_credit_text import format_credit_amount
from sophie_bot.modules.ai.utils.ai_usage_service import OperatorAIStats, get_operator_ai_stats
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _


def _display_name(chat: ChatModel) -> str | Element:
    if chat.type == ChatType.private:
        return UserLink(chat.tid, chat.first_name_or_title)
    return chat.first_name_or_title


def _format_top(ranking: tuple[tuple[ChatModel, int], ...]) -> list[Template]:
    lines: list[Template] = []
    for index, (chat, count) in enumerate(ranking, start=1):
        lines.append(
            Template(
                "{idx}. {name} — {count}",
                idx=Code(index),
                name=_display_name(chat),
                count=Code(count),
            )
        )

    if not lines:
        lines.append(Template("{msg}", msg=Code(_("No data"))))
    return lines


def _format_feature_top(stats: OperatorAIStats) -> list[Template]:
    lines: list[Template] = []
    for item in stats.top_features:
        lines.append(
            Template(
                "{icon} {title} - {requests} req / {credit_emoji} {credits}",
                icon=item.icon,
                title=Bold(item.title),
                requests=Code(item.requests),
                credit_emoji=AI_CREDIT_EMOJI,
                credits=Code(f"{item.credits:,}"),
            )
        )

    if not lines:
        lines.append(Template("{msg}", msg=Code(_("No data"))))
    return lines


def _build_doc(stats: OperatorAIStats) -> Doc:
    return Doc(
        Section(
            KeyValue(_("Requests today"), Code(stats.total_requests_today)),
            KeyValue(_("Requests this week"), Code(stats.total_requests_week)),
            KeyValue(_("Requests this month"), Code(stats.total_requests_month)),
            KeyValue(_("Credits this month"), Code(format_credit_amount(stats.total_credits_month))),
            title=_("AI usage"),
        ),
        Section(Bold(_("Top chats by requests")), *_format_top(stats.top_chats_by_requests)),
        Section(Bold(_("Top chats by credits")), *_format_top(stats.top_chats_by_credits)),
        Section(Bold(_("Top users by requests")), *_format_top(stats.top_users_by_requests)),
        Section(Bold(_("Top users by credits")), *_format_top(stats.top_users_by_credits)),
        Section(Bold(_("Top features this month")), *_format_feature_top(stats)),
    )


async def op_ai_stats_handler(message: Message) -> None:
    await message.reply(str(_build_doc(await get_operator_ai_stats())))


class OpAIStatsHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return CMDFilter("op_aistats"), IsOP(True)

    @classmethod
    def register(cls, router: Router) -> None:
        router.message.register(cls, *cls.filters())

    async def handle(self) -> None:
        await op_ai_stats_handler(self.event)
