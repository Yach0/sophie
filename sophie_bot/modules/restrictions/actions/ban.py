from __future__ import annotations

from datetime import timedelta
from typing import ClassVar

from pydantic import BaseModel

from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.restrictions.actions.base import BaseRestrictionModernAction
from sophie_bot.modules.utils_.action_config_wizard import (
    ActionWizardSetting,
    ActionWizardSpec,
    make_duration_setup_confirm,
    make_duration_setup_message,
)
from sophie_bot.shared.actions import ActionDefinition, RestrictionAction
from sophie_bot.utils.i18n import N_
from sophie_bot.utils.i18n import lazy_gettext as l_


class BanActionDataModel(BaseModel):
    ban_duration: timedelta | None


def build_action_wizard_specs() -> dict[str, ActionWizardSpec]:
    return {
        BAN_ACTION.name: ActionWizardSpec(
            interactive_setup=None,
            settings=lambda _data: {
                "change_ban_duration": ActionWizardSetting(
                    title=l_("Change ban duration"),
                    icon="⏰",
                    setup_message=make_duration_setup_message(
                        l_(
                            "Please write the duration, for example 2h for 2 hours, 7d for 7 days or 2w for 2 weeks. Or 0 for permanent."
                        )
                    ),
                    setup_confirm=make_duration_setup_confirm(
                        BanActionDataModel,
                        l_("Invalid duration, please try again."),
                    ),
                )
            },
        )
    }


BAN_ACTION = ActionDefinition[BanActionDataModel](
    name="ban_user",
    icon="🚷",
    title=l_("Ban"),
    data_object=BanActionDataModel,
    default_data=BanActionDataModel(ban_duration=None),
    as_flood=True,
    allow_warns=True,
    skip_for_admins=True,
    restriction_action=RestrictionAction.BAN,
    duration_field="ban_duration",
)


class BanModernAction(BaseRestrictionModernAction[BanActionDataModel]):
    definition = BAN_ACTION

    action_name: ClassVar[str] = "ban_user"
    action_log_event: ClassVar[LogEvent] = LogEvent.USER_BANNED
    auto_banned_text: ClassVar[str] = N_("User {user} was automatically banned based on a filter action")

    @staticmethod
    def get_duration(data: BanActionDataModel) -> timedelta | None:
        return data.ban_duration
