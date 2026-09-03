from __future__ import annotations

from typing import Any, cast

from aiogram import F

from sophie_bot.utils.handlers import SophieBaseHandler

from .callback import _ACWCallbackHandler, _ACWNoOpHandler, _ACWSettingsHandler
from .callbacks import ACWCoreCallback, ACWSettingCallback
from .config import ActionWizardConfig
from .context import _ACTION_WIZARD_CONFIGS
from .message import _ACWSetupHandler, _ACWWizardHandler
from .state import ActionConfigFSM


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
    """Create handlers sharing one config and one aggregate draft."""
    _ACTION_WIZARD_CONFIGS[cfg.module_name] = cfg
    wizard_cls = type(
        "ACWWizard",
        (_ACWWizardHandler,),
        {"cfg": cfg, "filters": staticmethod(lambda: (cfg.command_filter, cfg.admin_filter, *cfg.extra_filters))},
    )
    callback_cls = type(
        "ACWCallback",
        (_ACWCallbackHandler,),
        {
            "cfg": cfg,
            "filters": staticmethod(
                lambda: (ACWCoreCallback.filter(F.mod == cfg.callback_prefix), cfg.admin_filter, *cfg.extra_filters)
            ),
        },
    )
    setup_cls = type(
        "ACWSetup",
        (_ACWSetupHandler,),
        {
            "cfg": cfg,
            "filters": staticmethod(lambda: (ActionConfigFSM.interactive_setup, cfg.admin_filter, *cfg.extra_filters)),
        },
    )
    settings_cls = type(
        "ACWSettings",
        (_ACWSettingsHandler,),
        {
            "cfg": cfg,
            "filters": staticmethod(
                lambda: (ACWSettingCallback.filter(F.mod == cfg.callback_prefix), cfg.admin_filter, *cfg.extra_filters)
            ),
        },
    )
    done_cls = type("ACWDone", (_ACWNoOpHandler,), {})
    cancel_cls = type("ACWCancel", (_ACWNoOpHandler,), {})
    return cast(
        tuple[
            type[SophieBaseHandler[Any]],
            type[SophieBaseHandler[Any]],
            type[SophieBaseHandler[Any]],
            type[SophieBaseHandler[Any]],
            type[SophieBaseHandler[Any]],
            type[SophieBaseHandler[Any]],
        ],
        (wizard_cls, callback_cls, setup_cls, done_cls, cancel_cls, settings_cls),
    )
