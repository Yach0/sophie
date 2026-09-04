from __future__ import annotations

from aiogram import F
from aiogram.dispatcher.event.handler import CallbackType

from sophie_bot.constants import ANTIFOOD_MAX_ACTIONS
from sophie_bot.db.models.antiflood import AntifloodModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.modules.utils_.action_config_wizard import (
    ActionWizardCallbackHandler,
    ActionWizardInputCleanupHandler,
    ActionWizardInputHandler,
    ActionWizardStartHandler,
    model_action_wizard,
)
from sophie_bot.modules.utils_.wizard import WizardCallback, WizardFSM, WizardScopeFilter
from sophie_bot.shared.actions import ModernActionABC
from sophie_bot.utils.i18n import lazy_gettext as l_


def antiflood_action_filter(action: ModernActionABC) -> bool:
    return action.as_flood


ANTIFLOOD_ACTION_WIZARD = model_action_wizard(
    model_loader=AntifloodModel.get_by_chat_iid,
    attribute="actions",
    scope="antiflood_action",
    title=l_("Antiflood Action Configuration"),
    done_message=l_("Antiflood action configured successfully!"),
    max_actions=ANTIFOOD_MAX_ACTIONS,
    action_filter=antiflood_action_filter,
)


class AntifloodActionWizard(ActionWizardStartHandler):
    wizard = ANTIFLOOD_ACTION_WIZARD

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            CMDFilter(("antiflood_action",)),
            FeatureFlagFilter("action_config_wizard"),
            UserRestricting(admin=True),
        )


class AntifloodActionCallback(ActionWizardCallbackHandler):
    wizard = ANTIFLOOD_ACTION_WIZARD

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WizardCallback.filter(F.scope == "antiflood_action"),
            FeatureFlagFilter("action_config_wizard"),
            UserRestricting(admin=True),
        )


class AntifloodActionInput(ActionWizardInputHandler):
    wizard = ANTIFLOOD_ACTION_WIZARD

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WizardFSM.interactive_input,
            WizardScopeFilter("antiflood_action"),
            FeatureFlagFilter("action_config_wizard"),
            UserRestricting(admin=True),
        )


class AntifloodActionInputCleanup(ActionWizardInputCleanupHandler):
    wizard = ANTIFLOOD_ACTION_WIZARD

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WizardFSM.interactive_input,
            WizardScopeFilter("antiflood_action"),
        )


__all__ = [
    "ANTIFLOOD_ACTION_WIZARD",
    "AntifloodActionCallback",
    "AntifloodActionInput",
    "AntifloodActionInputCleanup",
    "AntifloodActionWizard",
]
