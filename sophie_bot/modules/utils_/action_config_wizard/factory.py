from __future__ import annotations

from typing import Any, cast

from aiogram import F

from sophie_bot.utils.handlers import SophieBaseHandler

from .callback import _ACWCallbackHandler, _ACWNoOpHandler, _ACWSettingsHandler
from .callbacks import ACWCoreCallback, ACWSettingCallback
from .config import ActionWizardConfig
from .context import _ACTION_WIZARD_CONFIGS
from .message import _ACWSetupHandler, _ACWWizardHandler


def create_action_config_system(
    cfg: ActionWizardConfig,
) -> tuple[
    type[SophieBaseHandler[Any]],
    type[SophieBaseHandler[Any]],
    type[SophieBaseHandler[Any]],
    type[SophieBaseHandler[Any]],
    type[SophieBaseHandler[Any]],
    type[SophieBaseHandler[Any]],
]:
    """Create a complete set of handler classes from a single config."""

    _ACTION_WIZARD_CONFIGS[cfg.module_name] = cfg

    wizard_cls = type(
        "ACWWizard",
        (_ACWWizardHandler,),
        {
            "cfg": cfg,
            "filters": staticmethod(lambda: (cfg.command_filter, cfg.admin_filter)),
        },
    )

    callback_cls = type(
        "ACWCallback",
        (_ACWCallbackHandler,),
        {
            "cfg": cfg,
            "filters": staticmethod(lambda: (ACWCoreCallback.filter(F.mod == cfg.callback_prefix),)),
        },
    )

    setup_cls = type(
        "ACWSetup",
        (_ACWSetupHandler,),
        {
            "cfg": cfg,
        },
    )

    settings_cls = type(
        "ACWSettings",
        (_ACWSettingsHandler,),
        {
            "cfg": cfg,
            "filters": staticmethod(lambda: (ACWSettingCallback.filter(F.mod == cfg.callback_prefix),)),
        },
    )

    done_cls = type("ACWDone", (_ACWNoOpHandler,), {})
    cancel_cls = type("ACWCancel", (_ACWNoOpHandler,), {})

    return (
        cast(type[SophieBaseHandler[Any]], wizard_cls),
        cast(type[SophieBaseHandler[Any]], callback_cls),
        cast(type[SophieBaseHandler[Any]], setup_cls),
        cast(type[SophieBaseHandler[Any]], done_cls),
        cast(type[SophieBaseHandler[Any]], cancel_cls),
        cast(type[SophieBaseHandler[Any]], settings_cls),
    )
