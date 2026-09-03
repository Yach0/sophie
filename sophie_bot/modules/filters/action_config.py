from __future__ import annotations

from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.filters.is_connected import GroupOrConnectedFilter
from sophie_bot.modules.filters.filter_wizard import FilterWizardContext
from sophie_bot.modules.utils_.action_config_wizard.config import ActionWizardConfig
from sophie_bot.modules.utils_.action_config_wizard.factory import create_action_config_system
from sophie_bot.utils.i18n import lazy_gettext as l_

_filter_cfg = ActionWizardConfig(
    module_name="filters",
    callback_prefix="filter_action",
    wizard_title=l_("Filter configuration"),
    success_message=l_("Filter saved."),
    context=FilterWizardContext(),
    command_filter=CMDFilter(("filter_wizard_internal",)),
    admin_filter=UserRestricting(admin=True),
    extra_filters=(FeatureFlagFilter("filters"), GroupOrConnectedFilter()),
    allow_multiple_actions=True,
    maximum_actions=8,
)

(
    FilterActionWizard,
    FilterActionCallback,
    FilterActionSetup,
    FilterActionDone,
    FilterActionCancel,
    FilterActionSettings,
) = create_action_config_system(_filter_cfg)

__all__ = [
    "FilterActionCallback",
    "FilterActionCancel",
    "FilterActionDone",
    "FilterActionSettings",
    "FilterActionSetup",
    "FilterActionWizard",
    "_filter_cfg",
]
