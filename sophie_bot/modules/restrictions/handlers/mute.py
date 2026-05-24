from __future__ import annotations

from typing import ClassVar

from aiogram.dispatcher.event.handler import CallbackType

from sophie_bot.filters.admin_rights import BotHasPermissions, UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.restrictions.handlers.base import BaseRestrictionHandler, RestrictionActionFunc
from sophie_bot.modules.restrictions.utils.restrictions import mute_user
from sophie_bot.utils import flags
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Mutes the user in the chat."))
class MuteUserHandler(BaseRestrictionHandler):
    bot_action_text: ClassVar[str | LazyProxy] = l_("I cannot mute myself.")
    self_action_text: ClassVar[str | LazyProxy] = l_("You cannot mute yourself.")
    admin_action_text: ClassVar[str | LazyProxy] = l_("I cannot mute an admin.")
    failed_action_text: ClassVar[str | LazyProxy] = l_(
        "Failed to mute the user. Make sure I have the right permissions."
    )
    actor_label: ClassVar[str | LazyProxy] = l_("Muted by")
    result_title: ClassVar[str | LazyProxy] = l_("User muted")
    event_type: ClassVar[LogEvent] = LogEvent.USER_MUTED

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("mute"),
            UserRestricting(can_restrict_members=True),
            BotHasPermissions(can_restrict_members=True),
        )

    @staticmethod
    def get_restriction_action() -> RestrictionActionFunc:
        return mute_user


@flags.help(description=l_("Temporarily mutes the user in the chat."))
class TempMuteUserHandler(BaseRestrictionHandler):
    bot_action_text: ClassVar[str | LazyProxy] = l_("I cannot mute myself.")
    self_action_text: ClassVar[str | LazyProxy] = l_("You cannot mute yourself.")
    admin_action_text: ClassVar[str | LazyProxy] = l_("I cannot mute an admin.")
    failed_action_text: ClassVar[str | LazyProxy] = l_(
        "Failed to mute the user. Make sure I have the right permissions."
    )
    actor_label: ClassVar[str | LazyProxy] = l_("Muted by")
    result_title: ClassVar[str | LazyProxy] = l_("User temporarily muted")
    event_type: ClassVar[LogEvent] = LogEvent.USER_MUTED
    with_duration: ClassVar[bool] = True

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("tmute"),
            UserRestricting(can_restrict_members=True),
            BotHasPermissions(can_restrict_members=True),
        )

    @staticmethod
    def get_restriction_action() -> RestrictionActionFunc:
        return mute_user
