from __future__ import annotations

from typing import ClassVar

from aiogram.dispatcher.event.handler import CallbackType

from sophie_bot.filters.admin_rights import BotHasPermissions, UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.restrictions.handlers.base import BaseRestrictionHandler, RestrictionActionFunc
from sophie_bot.modules.restrictions.utils.restrictions import ban_user
from sophie_bot.utils import flags
from sophie_bot.utils.i18n import lazy_gettext as l_


@flags.help(description=l_("Bans the user from the chat."))
class BanUserHandler(BaseRestrictionHandler):
    bot_action_text: ClassVar[str] = l_("I cannot ban myself.")
    self_action_text: ClassVar[str] = l_("You cannot ban yourself.")
    admin_action_text: ClassVar[str] = l_("I cannot ban an admin.")
    failed_action_text: ClassVar[str] = l_("Failed to ban the user. Make sure I have the right permissions.")
    actor_label: ClassVar[str] = l_("Banned by")
    result_title: ClassVar[str] = l_("User banned")
    event_type: ClassVar[LogEvent] = LogEvent.USER_BANNED
    check_federation_ban: ClassVar[bool] = True
    use_common_try: ClassVar[bool] = True

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("ban"),
            UserRestricting(can_restrict_members=True),
            BotHasPermissions(can_restrict_members=True),
        )

    @staticmethod
    def get_restriction_action() -> RestrictionActionFunc:
        return ban_user


@flags.help(description=l_("Temporarily bans the user from the chat."))
class TempBanUserHandler(BaseRestrictionHandler):
    bot_action_text: ClassVar[str] = l_("I cannot ban myself.")
    self_action_text: ClassVar[str] = l_("You cannot ban yourself.")
    admin_action_text: ClassVar[str] = l_("I cannot ban an admin.")
    failed_action_text: ClassVar[str] = l_("Failed to ban the user. Make sure I have the right permissions.")
    actor_label: ClassVar[str] = l_("Banned by")
    result_title: ClassVar[str] = l_("User temporarily banned")
    event_type: ClassVar[LogEvent] = LogEvent.USER_BANNED
    with_duration: ClassVar[bool] = True
    check_federation_ban: ClassVar[bool] = True
    use_common_try: ClassVar[bool] = True

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("tban"),
            UserRestricting(can_restrict_members=True),
            BotHasPermissions(can_restrict_members=True),
        )

    @staticmethod
    def get_restriction_action() -> RestrictionActionFunc:
        return ban_user
