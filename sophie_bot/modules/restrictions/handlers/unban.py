from __future__ import annotations

from datetime import timedelta
from typing import ClassVar

from aiogram.dispatcher.event.handler import CallbackType

from sophie_bot.filters.admin_rights import BotHasPermissions, UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.restrictions.handlers.base import BaseRestrictionHandler, RestrictionActionFunc
from sophie_bot.modules.restrictions.utils.restrictions import unban_user
from sophie_bot.utils import flags
from sophie_bot.utils.i18n import lazy_gettext as l_


async def _unban_action(chat_tid: int, user_tid: int, until_date: timedelta | None) -> bool:
    return await unban_user(chat_tid, user_tid)


@flags.help(description=l_("Unbans the user from the chat."))
class UnbanUserHandler(BaseRestrictionHandler):
    bot_action_text: ClassVar[str] = l_("I cannot unban myself.")
    self_action_text: ClassVar[str] = l_("You cannot unban yourself.")
    failed_action_text: ClassVar[str] = l_("Failed to unban the user. Make sure I have the right permissions.")
    actor_label: ClassVar[str] = l_("Unbanned by")
    result_title: ClassVar[str] = l_("User unbanned")
    event_type: ClassVar[LogEvent] = LogEvent.USER_UNBANNED
    check_admin: ClassVar[bool] = False
    check_federation_ban: ClassVar[bool] = True
    gen_ai_reason: ClassVar[bool] = False
    fed_ban_notice_current: ClassVar[str] = l_("The user is banned in the current federation: {fed_name} ({fed_id}).")
    fed_ban_notice_subscribed: ClassVar[str] = l_(
        "The user is banned in a subscribed federation: {fed_name} ({fed_id})."
    )

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("unban"),
            UserRestricting(can_restrict_members=True),
            BotHasPermissions(can_restrict_members=True),
        )

    @staticmethod
    def get_restriction_action() -> RestrictionActionFunc:
        return _unban_action
