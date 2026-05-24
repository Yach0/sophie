from __future__ import annotations

from datetime import timedelta
from typing import ClassVar

from aiogram.dispatcher.event.handler import CallbackType

from sophie_bot.filters.admin_rights import BotHasPermissions, UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.restrictions.handlers.base import BaseRestrictionHandler, RestrictionActionFunc
from sophie_bot.modules.restrictions.utils.restrictions import unmute_user
from sophie_bot.utils import flags
from sophie_bot.utils.i18n import lazy_gettext as l_


async def _unmute_action(chat_tid: int, user_tid: int, until_date: timedelta | None) -> bool:
    return await unmute_user(chat_tid, user_tid)


@flags.help(description=l_("Unmutes the user in the chat."))
class UnmuteUserHandler(BaseRestrictionHandler):
    bot_action_text: ClassVar[str] = l_("I cannot unmute myself.")
    self_action_text: ClassVar[str] = l_("You cannot unmute yourself.")
    failed_action_text: ClassVar[str] = l_("Failed to unmute the user. Make sure I have the right permissions.")
    actor_label: ClassVar[str] = l_("Unmuted by")
    result_title: ClassVar[str] = l_("User unmuted")
    event_type: ClassVar[LogEvent] = LogEvent.USER_UNMUTED
    check_admin: ClassVar[bool] = False
    gen_ai_reason: ClassVar[bool] = False

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("unmute"),
            UserRestricting(can_restrict_members=True),
            BotHasPermissions(can_restrict_members=True),
        )

    @staticmethod
    def get_restriction_action() -> RestrictionActionFunc:
        return _unmute_action
