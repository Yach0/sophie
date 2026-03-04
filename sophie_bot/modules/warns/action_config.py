from __future__ import annotations

from sophie_bot.constants import WARN_MAX_ACTIONS
from sophie_bot.db.models.warns import WarnSettingsModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.filters.types.modern_action_abc import ModernActionABC
from sophie_bot.modules.utils_.action_config_wizard import create_action_config_system
from sophie_bot.utils.i18n import lazy_gettext as l_


def warn_action_filter(action: ModernActionABC) -> bool:
    return action.allow_warns


async def _get_on_each_warn_actions(model: WarnSettingsModel) -> list:
    return list(model.on_each_warn_actions)


async def _get_on_max_warn_actions(model: WarnSettingsModel) -> list:
    return list(model.on_max_warn_actions)


(
    WarnEachActionWizard,
    WarnEachActionCallback,
    WarnEachActionSetup,
    WarnEachActionDone,
    WarnEachActionCancel,
    WarnEachActionSettings,
) = create_action_config_system(
    module_name="warns_each",
    callback_prefix="warn_action_each",
    wizard_title=l_("Warn Action Configuration (On each warn)"),
    success_message=l_("Warn action configured successfully!"),
    get_model_func=WarnSettingsModel.get_by_chat_iid,
    get_actions_func=_get_on_each_warn_actions,
    add_action_func=WarnSettingsModel.add_on_each_warn_action,
    remove_action_func=WarnSettingsModel.remove_on_each_warn_action,
    command_filter=CMDFilter(("warnaction_each", "warn_action_each")),
    admin_filter=UserRestricting(can_restrict_members=True),
    allow_multiple_actions=(WARN_MAX_ACTIONS > 1),
    action_filter=warn_action_filter,
)


(
    WarnMaxActionWizard,
    WarnMaxActionCallback,
    WarnMaxActionSetup,
    WarnMaxActionDone,
    WarnMaxActionCancel,
    WarnMaxActionSettings,
) = create_action_config_system(
    module_name="warns_max",
    callback_prefix="warn_action_max",
    wizard_title=l_("Warn Action Configuration (On max warns)"),
    success_message=l_("Warn action configured successfully!"),
    get_model_func=WarnSettingsModel.get_by_chat_iid,
    get_actions_func=_get_on_max_warn_actions,
    add_action_func=WarnSettingsModel.add_on_max_warn_action,
    remove_action_func=WarnSettingsModel.remove_on_max_warn_action,
    command_filter=CMDFilter(("warnaction_max", "warn_action_max")),
    admin_filter=UserRestricting(can_restrict_members=True),
    allow_multiple_actions=(WARN_MAX_ACTIONS > 1),
    action_filter=warn_action_filter,
)


__all__ = [
    "WarnEachActionWizard",
    "WarnEachActionCallback",
    "WarnEachActionSetup",
    "WarnEachActionDone",
    "WarnEachActionCancel",
    "WarnEachActionSettings",
    "WarnMaxActionWizard",
    "WarnMaxActionCallback",
    "WarnMaxActionSetup",
    "WarnMaxActionDone",
    "WarnMaxActionCancel",
    "WarnMaxActionSettings",
]
