from __future__ import annotations

from sophie_bot.constants import ANTIFOOD_MAX_ACTIONS
from sophie_bot.db.models.antiflood import AntifloodModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.filters.types.modern_action_abc import ModernActionABC
from sophie_bot.modules.utils_.action_config_wizard.adapters import ModelActionsContext
from sophie_bot.modules.utils_.action_config_wizard.config import ActionWizardConfig
from sophie_bot.modules.utils_.action_config_wizard.factory import create_action_config_system
from sophie_bot.utils.i18n import lazy_gettext as l_


def antiflood_action_filter(action: ModernActionABC) -> bool:
    """Filter to only allow actions marked for antiflood use."""
    return action.as_flood


_antiflood_context = ModelActionsContext(
    AntifloodModel.get_by_chat_iid,
    "actions",
    maximum_actions=ANTIFOOD_MAX_ACTIONS,
)

_antiflood_cfg = ActionWizardConfig(
    module_name="antiflood",
    callback_prefix="antiflood_action",
    wizard_title=l_("Antiflood Action Configuration"),
    success_message=l_("Antiflood action configured successfully!"),
    context=_antiflood_context,
    command_filter=CMDFilter(("antiflood_action",)),
    admin_filter=UserRestricting(admin=True),
    allow_multiple_actions=(ANTIFOOD_MAX_ACTIONS > 1),
    maximum_actions=ANTIFOOD_MAX_ACTIONS,
    action_filter=antiflood_action_filter,
)

(
    AntifloodActionWizard,
    AntifloodActionCallback,
    AntifloodActionSetup,
    AntifloodActionDone,
    AntifloodActionCancel,
    AntifloodActionSettings,
) = create_action_config_system(_antiflood_cfg)

__all__ = [
    "AntifloodActionCallback",
    "AntifloodActionCancel",
    "AntifloodActionDone",
    "AntifloodActionSettings",
    "AntifloodActionSetup",
    "AntifloodActionWizard",
]
