from __future__ import annotations

from typing import ClassVar

from aiogram.dispatcher.event.handler import CallbackType

from sophie_bot.filters.admin_rights import BotHasPermissions, UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.restrictions.handlers.base import BaseRestrictionHandler
from sophie_bot.modules.utils_.clear_pending_user import clear_pending_user
from sophie_bot.shared.actions import RestrictionAction
from sophie_bot.utils import flags
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Unmutes the user in the chat."))
class UnmuteUserHandler(BaseRestrictionHandler):
    bot_action_text: ClassVar[str | LazyProxy] = l_("I cannot unmute myself.")
    self_action_text: ClassVar[str | LazyProxy] = l_("You cannot unmute yourself.")
    failed_action_text: ClassVar[str | LazyProxy] = l_(
        "Failed to unmute the user. Make sure I have the right permissions."
    )
    actor_label: ClassVar[str | LazyProxy] = l_("Unmuted by")
    result_title: ClassVar[str | LazyProxy] = l_("User unmuted")
    event_type: ClassVar[LogEvent] = LogEvent.USER_UNMUTED
    restriction_action: ClassVar[RestrictionAction] = RestrictionAction.UNMUTE
    check_admin: ClassVar[bool] = False
    gen_ai_reason: ClassVar[bool] = False

    async def _after_restriction_applied(self, chat_tid: int, user_tid: int) -> None:
        await clear_pending_user(user_tid, chat_tid)

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("unmute"),
            UserRestricting(can_restrict_members=True),
            BotHasPermissions(can_restrict_members=True),
        )
