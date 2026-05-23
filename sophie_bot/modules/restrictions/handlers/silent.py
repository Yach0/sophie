from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, ClassVar

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import ActionTimeArg, OptionalArg, TextArg
from ass_tg.types.base_abc import ArgFabric
from babel.dates import format_timedelta

from sophie_bot.args.users import SophieUserArg
from sophie_bot.config import CONFIG
from sophie_bot.filters.admin_rights import BotHasPermissions, UserRestricting
from sophie_bot.filters.chat_status import ChatTypeFilter
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.restrictions.services.silent import (
    build_silent_action_doc,
    collect_message_ids_for_cleanup,
    log_silent_action,
    schedule_message_deletion,
)
from sophie_bot.modules.restrictions.utils.restrictions import ban_user, kick_user, mute_user
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.modules.utils_.get_user import get_arg_or_reply_user, get_union_user
from sophie_bot.modules.utils_.message import is_real_reply
from sophie_bot.utils import flags
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

SilentActionFunc = Callable[[int, int, timedelta | None], Awaitable[bool]]


async def _kick_action(chat_tid: int, target_user_id: int, until_date: timedelta | None) -> bool:
    return await kick_user(chat_tid, target_user_id)


async def _ban_action(chat_tid: int, target_user_id: int, until_date: timedelta | None) -> bool:
    return await ban_user(chat_tid, target_user_id, until_date=until_date)


async def _mute_action(chat_tid: int, target_user_id: int, until_date: timedelta | None) -> bool:
    return await mute_user(chat_tid, target_user_id, until_date=until_date)


class BaseSilentUserHandler(SophieMessageHandler):
    bot_action_text: ClassVar[str]
    self_action_text: ClassVar[str]
    admin_action_text: ClassVar[str]
    failed_action_text: ClassVar[str]
    actor_label: ClassVar[str]
    result_title: ClassVar[str]
    event_type: ClassVar[LogEvent]
    with_duration: ClassVar[bool] = False

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        raise NotImplementedError

    @staticmethod
    def get_moderation_action() -> SilentActionFunc:
        raise NotImplementedError

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict[str, Any]) -> dict[str, ArgFabric]:
        args: dict[str, ArgFabric] = {}

        if not message or not is_real_reply(message):
            args["user"] = SophieUserArg(l_("User"))

        if cls.with_duration:
            args["time"] = ActionTimeArg(l_("Time (e.g., 2h, 7d, 2w)"))

        args["reason"] = OptionalArg(TextArg(l_("Reason")))
        return args

    async def handle(self) -> Any:
        connection = self.connection

        if not self.event.from_user:
            raise SophieException("No from_user")

        user = get_union_user(get_arg_or_reply_user(self.event, self.data))

        if user.chat_id == CONFIG.bot_id:
            return await self.event.reply(self.bot_action_text)

        if user.chat_id == self.event.from_user.id:
            return await self.event.reply(self.self_action_text)

        if await is_user_admin(connection.tid, user.chat_id):
            return await self.event.reply(self.admin_action_text)

        until_date = self.data.get("time") if self.with_duration else None
        moderation_action = self.get_moderation_action()
        if not await moderation_action(connection.tid, user.chat_id, until_date):
            return await self.event.reply(self.failed_action_text)

        reason = self.data.get("reason")
        await log_silent_action(
            chat_tid=connection.tid,
            actor_user_id=self.event.from_user.id,
            event_type=self.event_type,
            target_user_id=user.chat_id,
            reply_to_message=self.event.reply_to_message,
            reason=reason,
            until_date=until_date,
        )

        duration_text = format_timedelta(until_date, locale=self.current_locale) if until_date else None
        doc = build_silent_action_doc(
            chat_title=connection.title,
            target_user_id=user.chat_id,
            target_user_name=user.first_name,
            actor_user_id=self.event.from_user.id,
            actor_user_name=self.event.from_user.first_name,
            actor_label=self.actor_label,
            title=self.result_title,
            reason=reason,
            duration_text=duration_text,
        )

        reply_message = await self.event.reply(str(doc))
        schedule_message_deletion(
            connection.tid,
            collect_message_ids_for_cleanup(self.event, reply_message.message_id),
        )


@flags.help(description=l_("Silently kicks the user from the chat. Deletes messages after 10 seconds."))
class SilentKickUserHandler(BaseSilentUserHandler):
    bot_action_text = _("I cannot kick myself.")
    self_action_text = _("You cannot kick yourself.")
    admin_action_text = _("I cannot kick an admin.")
    failed_action_text = _("Failed to kick the user. Make sure I have the right permissions.")
    actor_label = _("Kicked by")
    result_title = _("User kicked")
    event_type = LogEvent.USER_KICKED

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("skick"),
            UserRestricting(can_restrict_members=True),
            BotHasPermissions(can_restrict_members=True),
            ~ChatTypeFilter("private"),
        )

    @staticmethod
    def get_moderation_action() -> SilentActionFunc:
        return _kick_action


@flags.help(description=l_("Silently bans the user from the chat. Deletes messages after 10 seconds."))
class SilentBanUserHandler(BaseSilentUserHandler):
    bot_action_text = _("I cannot ban myself.")
    self_action_text = _("You cannot ban yourself.")
    admin_action_text = _("I cannot ban an admin.")
    failed_action_text = _("Failed to ban the user. Make sure I have the right permissions.")
    actor_label = _("Banned by")
    result_title = _("User banned")
    event_type = LogEvent.USER_BANNED

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("sban"),
            UserRestricting(can_restrict_members=True),
            BotHasPermissions(can_restrict_members=True),
            ~ChatTypeFilter("private"),
        )

    @staticmethod
    def get_moderation_action() -> SilentActionFunc:
        return _ban_action


@flags.help(description=l_("Silently temporarily bans the user from the chat. Deletes messages after 10 seconds."))
class SilentTempBanUserHandler(BaseSilentUserHandler):
    bot_action_text = _("I cannot ban myself.")
    self_action_text = _("You cannot ban yourself.")
    admin_action_text = _("I cannot ban an admin.")
    failed_action_text = _("Failed to ban the user. Make sure I have the right permissions.")
    actor_label = _("Banned by")
    result_title = _("User temporarily banned")
    event_type = LogEvent.USER_BANNED
    with_duration = True

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter(("stban", "tsban")),
            UserRestricting(can_restrict_members=True),
            BotHasPermissions(can_restrict_members=True),
            ~ChatTypeFilter("private"),
        )

    @staticmethod
    def get_moderation_action() -> SilentActionFunc:
        return _ban_action


@flags.help(description=l_("Silently mutes the user in the chat. Deletes messages after 10 seconds."))
class SilentMuteUserHandler(BaseSilentUserHandler):
    bot_action_text = _("I cannot mute myself.")
    self_action_text = _("You cannot mute yourself.")
    admin_action_text = _("I cannot mute an admin.")
    failed_action_text = _("Failed to mute the user. Make sure I have the right permissions.")
    actor_label = _("Muted by")
    result_title = _("User muted")
    event_type = LogEvent.USER_MUTED

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("smute"),
            UserRestricting(can_restrict_members=True),
            BotHasPermissions(can_restrict_members=True),
            ~ChatTypeFilter("private"),
        )

    @staticmethod
    def get_moderation_action() -> SilentActionFunc:
        return _mute_action


@flags.help(description=l_("Silently temporarily mutes the user in the chat. Deletes messages after 10 seconds."))
class SilentTempMuteUserHandler(BaseSilentUserHandler):
    bot_action_text = _("I cannot mute myself.")
    self_action_text = _("You cannot mute yourself.")
    admin_action_text = _("I cannot mute an admin.")
    failed_action_text = _("Failed to mute the user. Make sure I have the right permissions.")
    actor_label = _("Muted by")
    result_title = _("User temporarily muted")
    event_type = LogEvent.USER_MUTED
    with_duration = True

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter(("stmute", "tsmute")),
            UserRestricting(can_restrict_members=True),
            BotHasPermissions(can_restrict_members=True),
            ~ChatTypeFilter("private"),
        )

    @staticmethod
    def get_moderation_action() -> SilentActionFunc:
        return _mute_action
