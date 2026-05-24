from __future__ import annotations

from datetime import timedelta
from typing import Any, ClassVar, Optional

from pydantic import BaseModel

from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.restrictions.actions.base import BaseRestrictionModernAction
from sophie_bot.modules.restrictions.utils import ban_user
from sophie_bot.utils.i18n import lazy_gettext as l_


class BanActionDataModel(BaseModel):
    ban_duration: Optional[timedelta]


class BanModernAction(BaseRestrictionModernAction[BanActionDataModel]):
    name = "ban_user"
    icon = "🚷"
    title = l_("Ban")
    data_object = BanActionDataModel
    default_data = BanActionDataModel(ban_duration=None)
    as_flood = True
    allow_warns = True

    action_name: ClassVar[str] = "ban_user"
    action_log_event: ClassVar[LogEvent] = LogEvent.USER_BANNED
    auto_banned_text: ClassVar[str] = l_("User {user} was automatically banned based on a filter action")
    settings_key: ClassVar[str] = "change_ban_duration"
    settings_title = l_("Change ban duration")

    @staticmethod
    def get_duration(data: BanActionDataModel) -> Optional[timedelta]:
        return data.ban_duration

    @staticmethod
    def restriction_func(chat_tid: int, user_tid: int, until_date: Optional[timedelta] = None) -> Any:
        return ban_user(chat_tid, user_tid, until_date=until_date)
