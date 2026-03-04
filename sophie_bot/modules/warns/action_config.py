from __future__ import annotations

from typing import Any

from aiogram.types import CallbackQuery, Message
from beanie import PydanticObjectId

from sophie_bot.constants import WARN_MAX_ACTIONS
from sophie_bot.db.models.warns import WarnSettingsModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from sophie_bot.modules.filters.types.modern_action_abc import ModernActionABC
from sophie_bot.modules.utils_.action_config_wizard import ActionWizardConfig, create_action_config_system
from sophie_bot.utils.i18n import gettext as _, lazy_gettext as l_

from .handlers.warnaction import WarnActionRenderer


def warn_action_filter(action: ModernActionABC) -> bool:
    return action.allow_warns


async def _get_on_each_warn_actions(model: WarnSettingsModel) -> list:
    return list(model.on_each_warn_actions)


async def _get_on_max_warn_actions(model: WarnSettingsModel) -> list:
    return list(model.on_max_warn_actions)


async def _on_warn_action_back(handler: Any, callback_query: CallbackQuery) -> None:
    """Custom back handler that returns to warnaction view."""
    if not callback_query.message or not isinstance(callback_query.message, Message):
        await callback_query.answer(_("Message not found."))
        return
    chat_iid: PydanticObjectId = handler.connection.db_model.iid
    text, markup = await WarnActionRenderer.render_warnaction_view(chat_iid)
    await callback_query.message.edit_text(text, reply_markup=markup)
    await callback_query.answer()


_warn_each_cfg = ActionWizardConfig(
    module_name="warns_each",
    callback_prefix="warn_action_each",
    wizard_title=l_("⚙️ Warn Actions - On Each Warn"),
    success_message=l_("Warn action configured successfully!"),
    get_model_func=WarnSettingsModel.get_by_chat_iid,
    get_actions_func=_get_on_each_warn_actions,
    add_action_func=WarnSettingsModel.add_on_each_warn_action,
    remove_action_func=WarnSettingsModel.remove_on_each_warn_action,
    command_filter=CMDFilter(("warnaction_each", "warn_action_each")),
    admin_filter=UserRestricting(can_restrict_members=True),
    allow_multiple_actions=(WARN_MAX_ACTIONS > 1),
    action_filter=warn_action_filter,
    on_back_render=_on_warn_action_back,
)

(
    WarnEachActionWizard,
    WarnEachActionCallback,
    WarnEachActionSetup,
    WarnEachActionDone,
    WarnEachActionCancel,
    WarnEachActionSettings,
) = create_action_config_system(_warn_each_cfg)


_warn_max_cfg = ActionWizardConfig(
    module_name="warns_max",
    callback_prefix="warn_action_max",
    wizard_title=l_("⚠️ Warn Actions - On Max Warns Exceeded"),
    success_message=l_("Warn action configured successfully!"),
    get_model_func=WarnSettingsModel.get_by_chat_iid,
    get_actions_func=_get_on_max_warn_actions,
    add_action_func=WarnSettingsModel.add_on_max_warn_action,
    remove_action_func=WarnSettingsModel.remove_on_max_warn_action,
    command_filter=CMDFilter(("warnaction_max", "warn_action_max")),
    admin_filter=UserRestricting(can_restrict_members=True),
    allow_multiple_actions=(WARN_MAX_ACTIONS > 1),
    action_filter=warn_action_filter,
    on_back_render=_on_warn_action_back,
)

(
    WarnMaxActionWizard,
    WarnMaxActionCallback,
    WarnMaxActionSetup,
    WarnMaxActionDone,
    WarnMaxActionCancel,
    WarnMaxActionSettings,
) = create_action_config_system(_warn_max_cfg)


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
