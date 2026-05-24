from __future__ import annotations

from abc import abstractmethod
from datetime import timedelta
from typing import Any, ClassVar, Generic, Optional, TypeVar

from aiogram.types import CallbackQuery, Message
from ass_tg.entities import ArgEntities
from ass_tg.exceptions import ARGS_EXCEPTIONS
from ass_tg.i18n import gettext_ctx
from ass_tg.types import ActionTimeArg
from babel.dates import format_timedelta
from pydantic import BaseModel
from stfu_tg import KeyValue, Template, Title, UserLink
from stfu_tg.doc import Doc, Element

from sophie_bot.config import CONFIG
from sophie_bot.modules.ai.utils.ai_restriction_reasons import generate_restriction_reason
from sophie_bot.modules.filters.types.modern_action_abc import (
    ActionSetupMessage,
    ActionSetupTryAgainException,
    ModernActionABC,
    ModernActionSetting,
)
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.modules.restrictions.utils import is_user_admin
from sophie_bot.modules.restrictions.utils.logging import add_offending_message_text
from sophie_bot.services.i18n import i18n
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.logger import log

ACTION_DATA = TypeVar("ACTION_DATA", bound=BaseModel)


def make_duration_setup_confirm(
    data_cls: type[Any],
    invalid_duration_text: str,
) -> Any:
    async def setup_confirm(event: Message | CallbackQuery, data: dict[str, Any]) -> Any:
        if isinstance(event, CallbackQuery):
            raise ValueError("This handlers setup_confirm can only be used with messages")

        raw_text = event.text or ""

        if raw_text == "0":
            return data_cls(
                **{
                    data_cls.model_fields[next(iter(data_cls.model_fields))].alias
                    or next(iter(data_cls.model_fields)): None
                }
            )

        try:
            gettext_ctx.set(i18n)

            with i18n.context():
                arg: timedelta = (await ActionTimeArg().parse(raw_text, 0, ArgEntities([])))[1]
        except ARGS_EXCEPTIONS:
            await event.reply(invalid_duration_text)
            raise ActionSetupTryAgainException()

        field_name = next(iter(data_cls.model_fields))
        return data_cls(**{field_name: arg})

    return setup_confirm


def make_duration_setup_message(prompt_text: str) -> Any:
    async def setup_message(_event: Message | CallbackQuery, _data: dict[str, Any]) -> ActionSetupMessage:
        return ActionSetupMessage(text=prompt_text)

    return setup_message


class BaseRestrictionModernAction(ModernActionABC[ACTION_DATA], Generic[ACTION_DATA]):
    action_name: ClassVar[str | LazyProxy]
    action_log_event: ClassVar[LogEvent]
    auto_banned_text: ClassVar[str | LazyProxy]
    settings_key: ClassVar[str]
    settings_title: ClassVar[LazyProxy]

    @staticmethod
    @abstractmethod
    def get_duration(data: ACTION_DATA) -> Optional[timedelta]:
        raise NotImplementedError

    @staticmethod
    @abstractmethod
    def restriction_func(chat_tid: int, user_tid: int, until_date: Optional[timedelta] = None) -> Any:
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

    def settings(self, data: ACTION_DATA) -> dict[str, ModernActionSetting]:
        return {
            self.settings_key: ModernActionSetting(
                title=self.settings_title,
                icon="⏰",
                setup_message=make_duration_setup_message(
                    _(
                        "Please write the duration, for example 2h for 2 hours, 7d for 7 days or 2w for 2 weeks. Or 0 for permanent."
                    )
                ),
                setup_confirm=make_duration_setup_confirm(
                    type(data),
                    _("Invalid duration, please try again."),
                ),
            ),
        }

    async def handle(self, message: Message, data: dict, filter_data: ACTION_DATA) -> Optional[Element]:
        if not message.from_user:
            return

        chat_id = message.chat.id
        user_id = message.from_user.id
        locale: str = data["i18n"].current_locale
        reason: Optional[str] = None

        chat_db = data.get("chat_db")
        if chat_db:
            message_text = message.text or message.caption or None
            reason = await generate_restriction_reason(chat_db, message_text=message_text, include_rules=True)

        if await is_user_admin(chat_id, user_id):
            log.debug("%s: user is admin, skipping...", type(self).__name__)
            return

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

        if not await self.restriction_func(chat_id, message.from_user.id, until_date=duration):
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
