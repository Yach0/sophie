from __future__ import annotations

from typing import ClassVar

from aiogram.dispatcher.event.handler import CallbackType

from sophie_bot.filters.admin_rights import BotHasPermissions, UserRestricting
from sophie_bot.filters.chat_status import ChatTypeFilter
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.restrictions.handlers.base import BaseRestrictionHandler
from sophie_bot.shared.actions import RestrictionAction
from sophie_bot.utils import flags
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.handler_help(description=l_("Silently kicks the user from the chat. Deletes messages after 10 seconds."))
class SilentKickUserHandler(BaseRestrictionHandler):
    bot_action_text: ClassVar[str | LazyProxy] = l_("I cannot kick myself.")
    self_action_text: ClassVar[str | LazyProxy] = l_("You cannot kick yourself.")
    admin_action_text: ClassVar[str | LazyProxy] = l_("I cannot kick an admin.")
    failed_action_text: ClassVar[str | LazyProxy] = l_(
        "Failed to kick the user. Make sure I have the right permissions."
    )
    actor_label: ClassVar[str | LazyProxy] = l_("Kicked by")
    result_title: ClassVar[str | LazyProxy] = l_("User kicked")
    event_type: ClassVar[LogEvent] = LogEvent.USER_KICKED
    restriction_action: ClassVar[RestrictionAction] = RestrictionAction.KICK
    check_admin: ClassVar[bool] = True
    silent: ClassVar[bool] = True

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("skick"),
            UserRestricting(can_restrict_members=True),
            BotHasPermissions(can_restrict_members=True),
            ~ChatTypeFilter("private"),
        )


@flags.handler_help(description=l_("Silently bans the user from the chat. Deletes messages after 10 seconds."))
class SilentBanUserHandler(BaseRestrictionHandler):
    bot_action_text: ClassVar[str | LazyProxy] = l_("I cannot ban myself.")
    self_action_text: ClassVar[str | LazyProxy] = l_("You cannot ban yourself.")
    admin_action_text: ClassVar[str | LazyProxy] = l_("I cannot ban an admin.")
    failed_action_text: ClassVar[str | LazyProxy] = l_(
        "Failed to ban the user. Make sure I have the right permissions."
    )
    actor_label: ClassVar[str | LazyProxy] = l_("Banned by")
    result_title: ClassVar[str | LazyProxy] = l_("User banned")
    event_type: ClassVar[LogEvent] = LogEvent.USER_BANNED
    restriction_action: ClassVar[RestrictionAction] = RestrictionAction.BAN
    check_admin: ClassVar[bool] = True
    silent: ClassVar[bool] = True

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("sban"),
            UserRestricting(can_restrict_members=True),
            BotHasPermissions(can_restrict_members=True),
            ~ChatTypeFilter("private"),
        )


@flags.handler_help(
    description=l_("Silently temporarily bans the user from the chat. Deletes messages after 10 seconds.")
)
class SilentTempBanUserHandler(BaseRestrictionHandler):
    bot_action_text: ClassVar[str | LazyProxy] = l_("I cannot ban myself.")
    self_action_text: ClassVar[str | LazyProxy] = l_("You cannot ban yourself.")
    admin_action_text: ClassVar[str | LazyProxy] = l_("I cannot ban an admin.")
    failed_action_text: ClassVar[str | LazyProxy] = l_(
        "Failed to ban the user. Make sure I have the right permissions."
    )
    actor_label: ClassVar[str | LazyProxy] = l_("Banned by")
    result_title: ClassVar[str | LazyProxy] = l_("User temporarily banned")
    event_type: ClassVar[LogEvent] = LogEvent.USER_BANNED
    restriction_action: ClassVar[RestrictionAction] = RestrictionAction.BAN
    with_duration: ClassVar[bool] = True
    check_admin: ClassVar[bool] = True
    silent: ClassVar[bool] = True

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter(("stban", "tsban")),
            UserRestricting(can_restrict_members=True),
            BotHasPermissions(can_restrict_members=True),
            ~ChatTypeFilter("private"),
        )


@flags.handler_help(description=l_("Silently mutes the user in the chat. Deletes messages after 10 seconds."))
class SilentMuteUserHandler(BaseRestrictionHandler):
    bot_action_text: ClassVar[str | LazyProxy] = l_("I cannot mute myself.")
    self_action_text: ClassVar[str | LazyProxy] = l_("You cannot mute yourself.")
    admin_action_text: ClassVar[str | LazyProxy] = l_("I cannot mute an admin.")
    failed_action_text: ClassVar[str | LazyProxy] = l_(
        "Failed to mute the user. Make sure I have the right permissions."
    )
    actor_label: ClassVar[str | LazyProxy] = l_("Muted by")
    result_title: ClassVar[str | LazyProxy] = l_("User muted")
    event_type: ClassVar[LogEvent] = LogEvent.USER_MUTED
    restriction_action: ClassVar[RestrictionAction] = RestrictionAction.MUTE
    check_admin: ClassVar[bool] = True
    silent: ClassVar[bool] = True

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("smute"),
            UserRestricting(can_restrict_members=True),
            BotHasPermissions(can_restrict_members=True),
            ~ChatTypeFilter("private"),
        )


@flags.handler_help(
    description=l_("Silently temporarily mutes the user in the chat. Deletes messages after 10 seconds.")
)
class SilentTempMuteUserHandler(BaseRestrictionHandler):
    bot_action_text: ClassVar[str | LazyProxy] = l_("I cannot mute myself.")
    self_action_text: ClassVar[str | LazyProxy] = l_("You cannot mute yourself.")
    admin_action_text: ClassVar[str | LazyProxy] = l_("I cannot mute an admin.")
    failed_action_text: ClassVar[str | LazyProxy] = l_(
        "Failed to mute the user. Make sure I have the right permissions."
    )
    actor_label: ClassVar[str | LazyProxy] = l_("Muted by")
    result_title: ClassVar[str | LazyProxy] = l_("User temporarily muted")
    event_type: ClassVar[LogEvent] = LogEvent.USER_MUTED
    restriction_action: ClassVar[RestrictionAction] = RestrictionAction.MUTE
    with_duration: ClassVar[bool] = True
    check_admin: ClassVar[bool] = True
    silent: ClassVar[bool] = True

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter(("stmute", "tsmute")),
            UserRestricting(can_restrict_members=True),
            BotHasPermissions(can_restrict_members=True),
            ~ChatTypeFilter("private"),
        )
