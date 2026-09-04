from __future__ import annotations

from aiogram import F
from aiogram.dispatcher.event.handler import CallbackType
from aiogram.types import CallbackQuery, Message

from sophie_bot.constants import WARN_MAX_ACTIONS
from sophie_bot.db.models.warns import WarnSettingsModel
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
from sophie_bot.utils.handlers import SophieCallbackQueryHandler
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

from .handlers.warnaction import WarnActionRenderer


def warn_action_filter(action: ModernActionABC) -> bool:
    return action.allow_warns


async def _on_warn_action_back(handler: SophieCallbackQueryHandler, callback_query: CallbackQuery) -> None:
    if not callback_query.message or not isinstance(callback_query.message, Message):
        await callback_query.answer(_("Message not found."))
        return
    document, markup = await WarnActionRenderer.render_warnaction_view(handler.connection.db_model.iid)
    await handler.answer_rich(document, reply_markup=markup)


def _command_filters(*commands: str) -> tuple[CallbackType, ...]:
    return (CMDFilter(commands), FeatureFlagFilter("action_config_wizard"), UserRestricting(can_restrict_members=True))


def _callback_filters(scope: str) -> tuple[CallbackType, ...]:
    return (
        WizardCallback.filter(F.scope == scope),
        FeatureFlagFilter("action_config_wizard"),
        UserRestricting(can_restrict_members=True),
    )


def _input_filters(scope: str) -> tuple[CallbackType, ...]:
    return (
        WizardFSM.interactive_input,
        WizardScopeFilter(scope),
        FeatureFlagFilter("action_config_wizard"),
        UserRestricting(can_restrict_members=True),
    )


WARN_EACH_ACTION_WIZARD = model_action_wizard(
    model_loader=WarnSettingsModel.get_by_chat_iid,
    attribute="on_each_warn_actions",
    scope="warn_action_each",
    title=l_("⚙️ Warn Actions - On Each Warn"),
    done_message=l_("Warn action configured successfully!"),
    max_actions=WARN_MAX_ACTIONS,
    action_filter=warn_action_filter,
    on_back=_on_warn_action_back,
)

WARN_MAX_ACTION_WIZARD = model_action_wizard(
    model_loader=WarnSettingsModel.get_by_chat_iid,
    attribute="on_max_warn_actions",
    scope="warn_action_max",
    title=l_("⚠️ Warn Actions - On Max Warns Exceeded"),
    done_message=l_("Warn action configured successfully!"),
    max_actions=WARN_MAX_ACTIONS,
    action_filter=warn_action_filter,
    on_back=_on_warn_action_back,
)


class WarnEachActionWizard(ActionWizardStartHandler):
    wizard = WARN_EACH_ACTION_WIZARD

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return _command_filters("warnaction_each", "warn_action_each")


class WarnEachActionCallback(ActionWizardCallbackHandler):
    wizard = WARN_EACH_ACTION_WIZARD

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return _callback_filters("warn_action_each")


class WarnEachActionInput(ActionWizardInputHandler):
    wizard = WARN_EACH_ACTION_WIZARD

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return _input_filters("warn_action_each")


class WarnEachActionInputCleanup(ActionWizardInputCleanupHandler):
    wizard = WARN_EACH_ACTION_WIZARD

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WizardFSM.interactive_input,
            WizardScopeFilter("warn_action_each"),
        )


class WarnMaxActionWizard(ActionWizardStartHandler):
    wizard = WARN_MAX_ACTION_WIZARD

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return _command_filters("warnaction_max", "warn_action_max")


class WarnMaxActionCallback(ActionWizardCallbackHandler):
    wizard = WARN_MAX_ACTION_WIZARD

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return _callback_filters("warn_action_max")


class WarnMaxActionInput(ActionWizardInputHandler):
    wizard = WARN_MAX_ACTION_WIZARD

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return _input_filters("warn_action_max")


class WarnMaxActionInputCleanup(ActionWizardInputCleanupHandler):
    wizard = WARN_MAX_ACTION_WIZARD

    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            WizardFSM.interactive_input,
            WizardScopeFilter("warn_action_max"),
        )


__all__ = [
    "WARN_EACH_ACTION_WIZARD",
    "WARN_MAX_ACTION_WIZARD",
    "WarnEachActionCallback",
    "WarnEachActionInput",
    "WarnEachActionInputCleanup",
    "WarnEachActionWizard",
    "WarnMaxActionCallback",
    "WarnMaxActionInput",
    "WarnMaxActionInputCleanup",
    "WarnMaxActionWizard",
]
