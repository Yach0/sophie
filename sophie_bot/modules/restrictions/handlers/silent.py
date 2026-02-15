from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from aiogram import flags

from sophie_bot.constants import SILENT_MODE_MESSAGE_DELETE_DELAY_SECONDS
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import Message
from ass_tg.types import ActionTimeArg, OptionalArg, TextArg
from ass_tg.types.base_abc import ArgFabric
from babel.dates import format_timedelta
from stfu_tg import KeyValue, Section, UserLink

from sophie_bot.args.users import SophieUserArg
from sophie_bot.config import CONFIG
from sophie_bot.filters.admin_rights import BotHasPermissions, UserRestricting
from sophie_bot.filters.chat_status import ChatTypeFilter
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.modules.restrictions.utils.restrictions import ban_user, kick_user, mute_user
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.modules.utils_.common_try import common_try
from sophie_bot.modules.utils_.get_user import get_arg_or_reply_user, get_union_user
from sophie_bot.modules.utils_.message import is_real_reply
from sophie_bot.services.bot import bot
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


async def delete_messages_after_delay(
    chat_id: int,
    message_ids: list[int],
    delay_seconds: int = SILENT_MODE_MESSAGE_DELETE_DELAY_SECONDS,
) -> None:
    """Delete messages after a specified delay."""
    await asyncio.sleep(delay_seconds)
    await common_try(bot.delete_messages(chat_id, message_ids))


@flags.help(description=l_("Silently kicks the user from the chat. Deletes messages after 10 seconds."))
class SilentKickUserHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("skick"),
            UserRestricting(can_restrict_members=True),
            BotHasPermissions(can_restrict_members=True),
            ~ChatTypeFilter("private"),
        )

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        args: dict[str, ArgFabric] = {}

        if not message or not is_real_reply(message):
            args["user"] = SophieUserArg(l_("User"))

        args["reason"] = OptionalArg(TextArg(l_("Reason")))

        return args

    async def handle(self) -> Any:
        connection = self.connection

        if not self.event.from_user:
            raise SophieException("No from_user")

        user = get_union_user(get_arg_or_reply_user(self.event, self.data))

        if user.chat_id == CONFIG.bot_id:
            return await self.event.reply(_("I cannot kick myself."))

        if self.event.from_user and user.chat_id == self.event.from_user.id:
            return await self.event.reply(_("You cannot kick yourself."))

        if await is_user_admin(connection.tid, user.chat_id):
            return await self.event.reply(_("I cannot kick an admin."))

        if not await kick_user(connection.tid, user.chat_id):
            return await self.event.reply(_("Failed to kick the user. Make sure I have the right permissions."))

        reason = self.data.get("reason")
        await log_event(
            connection.tid,
            self.event.from_user.id,
            LogEvent.USER_KICKED,
            {"target_user_id": user.chat_id, "reason": reason},
        )

        doc = Section(
            KeyValue(_("Chat"), connection.title),
            KeyValue(_("User"), UserLink(user.chat_id, user.first_name)),
            KeyValue(_("Kicked by"), UserLink(self.event.from_user.id, self.event.from_user.first_name)),
            KeyValue(_("Reason"), reason) if reason else None,
            title=_("User kicked"),
        )

        reply_msg = await self.event.reply(str(doc))

        # Schedule deletion of messages after SILENT_MODE_MESSAGE_DELETE_DELAY_SECONDS
        messages_to_delete = [self.event.message_id, reply_msg.message_id]
        if self.event.reply_to_message:
            messages_to_delete.append(self.event.reply_to_message.message_id)

        asyncio.create_task(delete_messages_after_delay(connection.tid, messages_to_delete))


@flags.help(description=l_("Silently bans the user from the chat. Deletes messages after 10 seconds."))
class SilentBanUserHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("sban"),
            UserRestricting(can_restrict_members=True),
            BotHasPermissions(can_restrict_members=True),
            ~ChatTypeFilter("private"),
        )

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        args: dict[str, ArgFabric] = {}

        if not message or not is_real_reply(message):
            args["user"] = SophieUserArg(l_("User"))

        args["reason"] = OptionalArg(TextArg(l_("Reason")))

        return args

    async def handle(self) -> Any:
        connection = self.connection

        if not self.event.from_user:
            raise SophieException("No from_user")

        user = get_union_user(get_arg_or_reply_user(self.event, self.data))

        if user.chat_id == CONFIG.bot_id:
            return await self.event.reply(_("I cannot ban myself."))

        if self.event.from_user and user.chat_id == self.event.from_user.id:
            return await self.event.reply(_("You cannot ban yourself."))

        if await is_user_admin(connection.tid, user.chat_id):
            return await self.event.reply(_("I cannot ban an admin."))

        if not await ban_user(connection.tid, user.chat_id):
            return await self.event.reply(_("Failed to ban the user. Make sure I have the right permissions."))

        reason = self.data.get("reason")
        await log_event(
            connection.tid,
            self.event.from_user.id,
            LogEvent.USER_BANNED,
            {"target_user_id": user.chat_id, "reason": reason},
        )

        doc = Section(
            KeyValue(_("Chat"), connection.title),
            KeyValue(_("User"), UserLink(user.chat_id, user.first_name)),
            KeyValue(_("Banned by"), UserLink(self.event.from_user.id, self.event.from_user.first_name)),
            KeyValue(_("Reason"), reason) if reason else None,
            title=_("User banned"),
        )

        reply_msg = await self.event.reply(str(doc))

        # Schedule deletion of messages after SILENT_MODE_MESSAGE_DELETE_DELAY_SECONDS
        messages_to_delete = [self.event.message_id, reply_msg.message_id]
        if self.event.reply_to_message:
            messages_to_delete.append(self.event.reply_to_message.message_id)

        asyncio.create_task(delete_messages_after_delay(connection.tid, messages_to_delete))


@flags.help(description=l_("Silently temporarily bans the user from the chat. Deletes messages after 10 seconds."))
class SilentTempBanUserHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter(("stban", "tsban")),
            UserRestricting(can_restrict_members=True),
            BotHasPermissions(can_restrict_members=True),
            ~ChatTypeFilter("private"),
        )

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        args: dict[str, ArgFabric] = {}

        if not message or not is_real_reply(message):
            args["user"] = SophieUserArg(l_("User"))

        args["time"] = ActionTimeArg(l_("Time (e.g., 2h, 7d, 2w)"))
        args["reason"] = OptionalArg(TextArg(l_("Reason")))

        return args

    async def handle(self) -> Any:
        connection = self.connection

        if not self.event.from_user:
            raise SophieException("No from_user")

        user = get_union_user(get_arg_or_reply_user(self.event, self.data))

        if user.chat_id == CONFIG.bot_id:
            return await self.event.reply(_("I cannot ban myself."))

        if self.event.from_user and user.chat_id == self.event.from_user.id:
            return await self.event.reply(_("You cannot ban yourself."))

        if await is_user_admin(connection.tid, user.chat_id):
            return await self.event.reply(_("I cannot ban an admin."))

        until_date: timedelta = self.data["time"]

        if not await ban_user(connection.tid, user.chat_id, until_date=until_date):
            return await self.event.reply(_("Failed to ban the user. Make sure I have the right permissions."))

        reason = self.data.get("reason")
        await log_event(
            connection.tid,
            self.event.from_user.id,
            LogEvent.USER_BANNED,
            {"target_user_id": user.chat_id, "reason": reason, "duration": until_date.total_seconds()},
        )

        doc = Section(
            KeyValue(_("Chat"), connection.title),
            KeyValue(_("User"), UserLink(user.chat_id, user.first_name)),
            KeyValue(_("Banned by"), UserLink(self.event.from_user.id, self.event.from_user.first_name)),
            KeyValue(_("Duration"), format_timedelta(until_date, locale=self.current_locale)),
            KeyValue(_("Reason"), reason) if reason else None,
            title=_("User temporarily banned"),
        )

        reply_msg = await self.event.reply(str(doc))

        # Schedule deletion of messages after SILENT_MODE_MESSAGE_DELETE_DELAY_SECONDS
        messages_to_delete = [self.event.message_id, reply_msg.message_id]
        if self.event.reply_to_message:
            messages_to_delete.append(self.event.reply_to_message.message_id)

        asyncio.create_task(delete_messages_after_delay(connection.tid, messages_to_delete))


@flags.help(description=l_("Silently mutes the user in the chat. Deletes messages after 10 seconds."))
class SilentMuteUserHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter("smute"),
            UserRestricting(can_restrict_members=True),
            BotHasPermissions(can_restrict_members=True),
            ~ChatTypeFilter("private"),
        )

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        args: dict[str, ArgFabric] = {}

        if not message or not is_real_reply(message):
            args["user"] = SophieUserArg(l_("User"))

        args["reason"] = OptionalArg(TextArg(l_("Reason")))

        return args

    async def handle(self) -> Any:
        connection = self.connection

        if not self.event.from_user:
            raise SophieException("No from_user")

        user = get_union_user(get_arg_or_reply_user(self.event, self.data))

        if user.chat_id == CONFIG.bot_id:
            return await self.event.reply(_("I cannot mute myself."))

        if self.event.from_user and user.chat_id == self.event.from_user.id:
            return await self.event.reply(_("You cannot mute yourself."))

        if await is_user_admin(connection.tid, user.chat_id):
            return await self.event.reply(_("I cannot mute an admin."))

        if not await mute_user(connection.tid, user.chat_id):
            return await self.event.reply(_("Failed to mute the user. Make sure I have the right permissions."))

        reason = self.data.get("reason")
        await log_event(
            connection.tid,
            self.event.from_user.id,
            LogEvent.USER_MUTED,
            {"target_user_id": user.chat_id, "reason": reason},
        )

        doc = Section(
            KeyValue(_("Chat"), connection.title),
            KeyValue(_("User"), UserLink(user.chat_id, user.first_name)),
            KeyValue(_("Muted by"), UserLink(self.event.from_user.id, self.event.from_user.first_name)),
            KeyValue(_("Reason"), reason) if reason else None,
            title=_("User muted"),
        )

        reply_msg = await self.event.reply(str(doc))

        # Schedule deletion of messages after SILENT_MODE_MESSAGE_DELETE_DELAY_SECONDS
        messages_to_delete = [self.event.message_id, reply_msg.message_id]
        if self.event.reply_to_message:
            messages_to_delete.append(self.event.reply_to_message.message_id)

        asyncio.create_task(delete_messages_after_delay(connection.tid, messages_to_delete))


@flags.help(description=l_("Silently temporarily mutes the user in the chat. Deletes messages after 10 seconds."))
class SilentTempMuteUserHandler(SophieMessageHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter(("stmute", "tsmute")),
            UserRestricting(can_restrict_members=True),
            BotHasPermissions(can_restrict_members=True),
            ~ChatTypeFilter("private"),
        )

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        args: dict[str, ArgFabric] = {}

        if not message or not is_real_reply(message):
            args["user"] = SophieUserArg(l_("User"))

        args["time"] = ActionTimeArg(l_("Time (e.g., 2h, 7d, 2w)"))
        args["reason"] = OptionalArg(TextArg(l_("Reason")))

        return args

    async def handle(self) -> Any:
        connection = self.connection

        if not self.event.from_user:
            raise SophieException("No from_user")

        user = get_union_user(get_arg_or_reply_user(self.event, self.data))

        if user.chat_id == CONFIG.bot_id:
            return await self.event.reply(_("I cannot mute myself."))

        if self.event.from_user and user.chat_id == self.event.from_user.id:
            return await self.event.reply(_("You cannot mute yourself."))

        if await is_user_admin(connection.tid, user.chat_id):
            return await self.event.reply(_("I cannot mute an admin."))

        until_date: timedelta = self.data["time"]

        if not await mute_user(connection.tid, user.chat_id, until_date=until_date):
            return await self.event.reply(_("Failed to mute the user. Make sure I have the right permissions."))

        reason = self.data.get("reason")
        await log_event(
            connection.tid,
            self.event.from_user.id,
            LogEvent.USER_MUTED,
            {"target_user_id": user.chat_id, "reason": reason, "duration": until_date.total_seconds()},
        )

        doc = Section(
            KeyValue(_("Chat"), connection.title),
            KeyValue(_("User"), UserLink(user.chat_id, user.first_name)),
            KeyValue(_("Muted by"), UserLink(self.event.from_user.id, self.event.from_user.first_name)),
            KeyValue(_("Duration"), format_timedelta(until_date, locale=self.current_locale)),
            KeyValue(_("Reason"), reason) if reason else None,
            title=_("User temporarily muted"),
        )

        reply_msg = await self.event.reply(str(doc))

        # Schedule deletion of messages after SILENT_MODE_MESSAGE_DELETE_DELAY_SECONDS
        messages_to_delete = [self.event.message_id, reply_msg.message_id]
        if self.event.reply_to_message:
            messages_to_delete.append(self.event.reply_to_message.message_id)

        asyncio.create_task(delete_messages_after_delay(connection.tid, messages_to_delete))
