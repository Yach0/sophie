from __future__ import annotations

from datetime import timedelta
from typing import ClassVar

from pydantic import BaseModel
from stfu_tg.doc import Element

from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.restrictions.actions.base import RestrictionActionMixin, restriction_description
from sophie_bot.modules.utils_.action_config_wizard import (
    ActionWizardSetting,
    ActionWizardSpec,
    make_duration_setup_confirm,
    make_duration_setup_message,
)
from sophie_bot.shared.actions import ActionDefinition, ModernActionABC, RestrictionAction
from sophie_bot.utils.i18n import N_
from sophie_bot.utils.i18n import lazy_gettext as l_


class MuteActionDataModel(BaseModel):
    mute_duration: timedelta | None


def build_action_wizard_specs() -> dict[str, ActionWizardSpec]:
    return {
        MUTE_ACTION.name: ActionWizardSpec(
            interactive_setup=None,
            settings=lambda _data: {
                "change_mute_duration": ActionWizardSetting(
                    title=l_("Change mute duration"),
                    icon="⏰",
                    setup_message=make_duration_setup_message(
                        l_(
                            "Please write the duration, for example 2h for 2 hours, 7d for 7 days or 2w for 2 weeks. Or 0 for permanent."
                        )
                    ),
                    setup_confirm=make_duration_setup_confirm(
                        MuteActionDataModel,
                        l_("Invalid duration, please try again."),
                    ),
                )
            },
        )
    }


MUTE_ACTION = ActionDefinition[MuteActionDataModel](
    name="mute_user",
    icon="🔕",
    title=l_("Mute"),
    data_object=MuteActionDataModel,
    default_data=MuteActionDataModel(mute_duration=None),
    as_flood=True,
    allow_warns=True,
    skip_for_admins=True,
    restriction_action=RestrictionAction.MUTE,
    duration_field="mute_duration",
)


class MuteModernAction(RestrictionActionMixin[MuteActionDataModel], ModernActionABC[MuteActionDataModel]):
    definition = MUTE_ACTION

    action_name: ClassVar[str] = "mute_user"
    action_log_event: ClassVar[LogEvent] = LogEvent.USER_MUTED
    auto_banned_text: ClassVar[str] = N_("User {user} was automatically muted based on a filter action")

    @staticmethod
    def get_duration(data: MuteActionDataModel) -> timedelta | None:
        return data.mute_duration

    @staticmethod
    def description(data: MuteActionDataModel) -> Element | str:
        return restriction_description(data.mute_duration)
