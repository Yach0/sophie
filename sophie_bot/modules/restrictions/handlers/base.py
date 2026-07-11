from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, ClassVar

from aiogram.types import Message
from ass_tg.types import ActionTimeArg, OptionalArg, TextArg
from ass_tg.types.base_abc import ArgFabric
from babel.dates import format_timedelta
from stfu_tg import KeyValue, Section, Template, UserLink

from sophie_bot.args.users import SophieUserArg
from sophie_bot.config import CONFIG
from sophie_bot.metrics.moderation import track_moderation_action
from sophie_bot.modules.ai.utils.ai_restriction_reasons import generate_restriction_reason
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.modules.restrictions.services.silent import (
    collect_message_ids_for_cleanup,
    schedule_message_deletion,
)
from sophie_bot.modules.restrictions.utils.logging import add_offending_message_text
from sophie_bot.modules.utils_.admin import is_user_admin
from sophie_bot.modules.utils_.get_user import get_arg_or_reply_user, get_union_user
from sophie_bot.modules.utils_.message import is_real_reply
from sophie_bot.modules.utils_.reply_or_answer import reply_or_answer
from sophie_bot.utils.exception import SophieException
from sophie_bot.utils.federation_ban_check import FederationBanInfo, get_user_federation_ban_info
from sophie_bot.utils.handlers import SophieMessageHandler
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

RestrictionActionFunc = Callable[[int, int, timedelta | None], Awaitable[bool]]

_LOG_EVENT_TO_ACTION: dict[LogEvent, str] = {
    LogEvent.USER_BANNED: "ban",
    LogEvent.USER_UNBANNED: "unban",
    LogEvent.USER_KICKED: "kick",
    LogEvent.USER_MUTED: "mute",
    LogEvent.USER_UNMUTED: "unmute",
    LogEvent.USER_RESTRICTED: "restrict",
    LogEvent.USER_UNRESTRICTED: "unrestrict",
}


class BaseRestrictionHandler(SophieMessageHandler):
    bot_action_text: ClassVar[str | LazyProxy]
    self_action_text: ClassVar[str | LazyProxy]
    admin_action_text: ClassVar[str | LazyProxy]
    failed_action_text: ClassVar[str | LazyProxy]
    actor_label: ClassVar[str | LazyProxy]
    result_title: ClassVar[str | LazyProxy]
    event_type: ClassVar[LogEvent]
    with_duration: ClassVar[bool] = False
    check_admin: ClassVar[bool] = True
    check_federation_ban: ClassVar[bool] = False
    gen_ai_reason: ClassVar[bool] = True
    use_common_try: ClassVar[bool] = False
    silent: ClassVar[bool] = False
    fed_ban_notice_current: ClassVar[str | LazyProxy] = l_(
        "The user is already banned in the current federation: {fed_name} ({fed_id})."
    )
    fed_ban_notice_subscribed: ClassVar[str | LazyProxy] = l_(
        "The user is already banned in a subscribed federation: {fed_name} ({fed_id})."
    )

    @staticmethod
    def get_restriction_action() -> RestrictionActionFunc:
        raise NotImplementedError

    @classmethod
    async def handler_args(cls, message: Message | None, data: dict) -> dict[str, ArgFabric]:
        args: dict[str, ArgFabric] = {}

        if not message or not is_real_reply(message):
            args["user"] = SophieUserArg(l_("User"))

        if cls.with_duration:
            args["time"] = ActionTimeArg(l_("Time (e.g., 2h, 7d, 2w)"), min=timedelta(minutes=1))

        args["reason"] = OptionalArg(TextArg(l_("Reason")))
        return args

    async def handle(self) -> Any:
        connection = self.connection

        if not self.event.from_user:
            raise SophieException("No from_user")

        user = get_union_user(get_arg_or_reply_user(self.event, self.data))

        if user.chat_id == CONFIG.bot_id:
            return await self.event.reply(str(self.bot_action_text))

        if user.chat_id == self.event.from_user.id:
            return await self.event.reply(str(self.self_action_text))

        if self.check_admin and await is_user_admin(connection.tid, user.chat_id):
            return await self.event.reply(str(self.admin_action_text))

        federation_ban_info: FederationBanInfo | None = None
        if self.check_federation_ban:
            federation_ban_info = await get_user_federation_ban_info(connection.db_model.iid, user.chat_id)

        until_date = self.data.get("time") if self.with_duration else None
        restriction_action = self.get_restriction_action()
        if not await restriction_action(connection.tid, user.chat_id, until_date):
            return await self.event.reply(str(self.failed_action_text))

        track_moderation_action(
            _LOG_EVENT_TO_ACTION.get(self.event_type, "unknown"),
            chat_type=self.event.chat.type,
            is_temporary=bool(until_date),
        )

        reason = self.data.get("reason")

        if self.gen_ai_reason and not reason and self.event.reply_to_message:
            replied_text = self.event.reply_to_message.text or self.event.reply_to_message.caption or None
            ai_reason = await generate_restriction_reason(
                connection.db_model,
                message_text=replied_text,
                include_rules=True,
            )
            if ai_reason:
                reason = ai_reason

        log_details: dict[str, Any] = {"target_user_id": user.chat_id, "reason": reason}
        if until_date:
            log_details["duration"] = until_date.total_seconds()

        await log_event(
            connection.tid,
            self.event.from_user.id,
            self.event_type,
            add_offending_message_text(log_details, self.event.reply_to_message),
        )

        doc = Section(
            KeyValue(_("Chat"), connection.title),
            KeyValue(_("User"), UserLink(user.chat_id, user.first_name)),
            KeyValue(str(self.actor_label), UserLink(self.event.from_user.id, self.event.from_user.first_name)),
            KeyValue(_("Duration"), format_timedelta(until_date, locale=self.current_locale)) if until_date else None,
            KeyValue(_("Reason"), reason) if reason else None,
            self._build_fed_ban_notice(federation_ban_info),
            title=str(self.result_title),
        )

        if self.use_common_try:
            reply_message = await reply_or_answer(self.event, doc)
        else:
            reply_message = await self.event.reply(str(doc))

        if self.silent and reply_message:
            schedule_message_deletion(
                connection.tid,
                collect_message_ids_for_cleanup(self.event, reply_message.message_id),
            )

    def _build_fed_ban_notice(self, info: FederationBanInfo | None) -> KeyValue | None:
        if not info:
            return None

        if info.scope == "current":
            return KeyValue(
                _("Notice"),
                Template(str(self.fed_ban_notice_current), fed_name=info.fed_name, fed_id=info.fed_id),
            )

        if info.scope == "subscribed":
            return KeyValue(
                _("Notice"),
                Template(str(self.fed_ban_notice_subscribed), fed_name=info.fed_name, fed_id=info.fed_id),
            )

        return None
