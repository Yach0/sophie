from __future__ import annotations

from datetime import timedelta
from typing import Any, ClassVar, Optional

from pydantic import BaseModel

from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.restrictions.actions.base import BaseRestrictionModernAction
from sophie_bot.modules.restrictions.utils import mute_user
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_


class MuteActionDataModel(BaseModel):
    mute_duration: Optional[timedelta]


class MuteModernAction(BaseRestrictionModernAction[MuteActionDataModel]):
    name = "mute_user"
    icon = "🔕"
    title = l_("Mute")
    data_object = MuteActionDataModel
    default_data = MuteActionDataModel(mute_duration=None)
    as_flood = True
    allow_warns = True

    action_name: ClassVar[str] = "mute_user"
    action_log_event: ClassVar[LogEvent] = LogEvent.USER_MUTED
    auto_banned_text: ClassVar[str | LazyProxy] = l_("User {user} was automatically muted based on a filter action")
    settings_key: ClassVar[str] = "change_mute_duration"
    settings_title = l_("Change mute duration")

    @staticmethod
    def get_duration(data: MuteActionDataModel) -> Optional[timedelta]:
        return data.mute_duration

    @staticmethod
    def restriction_func(chat_tid: int, user_tid: int, until_date: Optional[timedelta] = None) -> Any:
        return mute_user(chat_tid, user_tid, until_date=until_date)
