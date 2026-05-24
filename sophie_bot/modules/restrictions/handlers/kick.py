from __future__ import annotations

from datetime import timedelta
from typing import ClassVar

from aiogram.dispatcher.event.handler import CallbackType

from sophie_bot.filters.admin_rights import BotHasPermissions, UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.restrictions.handlers.base import BaseRestrictionHandler, RestrictionActionFunc
from sophie_bot.modules.restrictions.utils.restrictions import kick_user
from sophie_bot.utils import flags
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_


async def _kick_action(chat_tid: int, user_tid: int, until_date: timedelta | None) -> bool:
    return await kick_user(chat_tid, user_tid)


@flags.help(description=l_("Kicks the user from the chat. The user would be able to join back."))
class KickUserHandler(BaseRestrictionHandler):
    bot_action_text: ClassVar[str | LazyProxy] = l_("I cannot kick myself.")
    self_action_text: ClassVar[str | LazyProxy] = l_("You cannot kick yourself.")
    admin_action_text: ClassVar[str | LazyProxy] = l_("I cannot kick an admin.")
    failed_action_text: ClassVar[str | LazyProxy] = l_(
        "Failed to kick the user. Make sure I have the right permissions."
    )
    actor_label: ClassVar[str | LazyProxy] = l_("Kicked by")
    result_title: ClassVar[str | LazyProxy] = l_("User kicked")
    event_type: ClassVar[LogEvent] = LogEvent.USER_KICKED

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("kick"),
            UserRestricting(can_restrict_members=True),
            BotHasPermissions(can_restrict_members=True),
        )

    @staticmethod
    def get_restriction_action() -> RestrictionActionFunc:
        return _kick_action
