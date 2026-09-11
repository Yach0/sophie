from __future__ import annotations

from typing import ClassVar

from aiogram.dispatcher.event.handler import CallbackType

from sophie_bot.filters.admin_rights import BotHasPermissions, UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.restrictions.handlers.base import BaseRestrictionHandler
from sophie_bot.shared.actions import RestrictionAction
from sophie_bot.utils import flags
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.handler_help(description=l_("Unbans the user from the chat."))
class UnbanUserHandler(BaseRestrictionHandler):
    bot_action_text: ClassVar[str | LazyProxy] = l_("I cannot unban myself.")
    self_action_text: ClassVar[str | LazyProxy] = l_("You cannot unban yourself.")
    failed_action_text: ClassVar[str | LazyProxy] = l_(
        "Failed to unban the user. Make sure I have the right permissions."
    )
    actor_label: ClassVar[str | LazyProxy] = l_("Unbanned by")
    result_title: ClassVar[str | LazyProxy] = l_("User unbanned")
    event_type: ClassVar[LogEvent] = LogEvent.USER_UNBANNED
    restriction_action: ClassVar[RestrictionAction] = RestrictionAction.UNBAN
    check_admin: ClassVar[bool] = False
    check_federation_ban: ClassVar[bool] = True
    gen_ai_reason: ClassVar[bool] = False
    fed_ban_notice_current: ClassVar[str | LazyProxy] = l_(
        "The user is banned in the current federation: {fed_name} ({fed_id})."
    )
    fed_ban_notice_subscribed: ClassVar[str | LazyProxy] = l_(
        "The user is banned in a subscribed federation: {fed_name} ({fed_id})."
    )

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("unban"),
            UserRestricting(can_restrict_members=True),
            BotHasPermissions(can_restrict_members=True),
        )
