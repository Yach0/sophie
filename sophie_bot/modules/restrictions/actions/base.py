from __future__ import annotations

from datetime import timedelta
from typing import ClassVar

from aiogram.types import Message
from babel.dates import format_timedelta
from pydantic import BaseModel
from stfu_tg import KeyValue, Template, Title, UserLink
from stfu_tg.doc import Doc, Element

from sophie_bot.config import CONFIG
from sophie_bot.modules.ai.utils.ai_restriction_reasons import generate_restriction_reason
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.modules.restrictions.utils.logging import add_offending_message_text
from sophie_bot.modules.restrictions.utils.restrictions import execute_restriction
from sophie_bot.shared.actions import ModernActionABC
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import gettext as _


class BaseRestrictionModernAction[ACTION_DATA: BaseModel](ModernActionABC[ACTION_DATA]):
    action_name: ClassVar[str | LazyProxy]
    action_log_event: ClassVar[LogEvent]
    auto_banned_text: ClassVar[str]

    @staticmethod
    def get_duration(data: ACTION_DATA) -> timedelta | None:
        raise NotImplementedError

    @classmethod
    def description(cls, data: ACTION_DATA) -> Element | str:
        duration = cls.get_duration(data)
        if duration:
            return Template(
                _("Restricts user for {time}"),
                time=format_timedelta(duration, locale="en_US"),
            )
        return _("Restricts user indefinitely")

    async def handle(self, message: Message, data: dict, filter_data: ACTION_DATA) -> Element | None:
        if not message.from_user:
            return

        chat_id = message.chat.id
        locale: str = data["i18n"].current_locale
        reason: str | None = None

        chat_db = data["context"].event_chat
        if chat_db:
            message_text = message.text or message.caption or None
            reason = await generate_restriction_reason(
                chat_db,
                message_text=message_text,
                include_rules=True,
                services=data["services"],
            )

        duration = self.get_duration(filter_data)

        doc = Doc(
            Title(_("Filter action")),
            Template(
                _(self.auto_banned_text),
                user=UserLink(message.from_user.id, message.from_user.first_name),
            ),
        )

        if duration:
            doc += KeyValue(_("For"), format_timedelta(duration, locale=locale))

        if reason:
            doc += KeyValue(_("Reason"), reason)

        restriction_action = self.definition.restriction_action
        if restriction_action is None:
            raise RuntimeError(f"Restriction action {self.definition.name!r} has no restriction operation")
        restriction_result = await execute_restriction(
            data["services"].bot,
            restriction_action,
            chat_id,
            message.from_user.id,
            until_date=duration,
        )
        if not restriction_result.applied:
            return

        if "filter_id" in data:
            details = add_offending_message_text(
                {
                    "target_user_id": message.from_user.id,
                    "filter_id": data["filter_id"],
                    "action": self.action_name,
                },
                message,
            )

            if reason:
                details["reason"] = reason

            if duration:
                details["duration"] = duration.total_seconds()

            await log_event(
                chat_id,
                CONFIG.bot_id,
                self.action_log_event,
                details,
            )

        return doc
