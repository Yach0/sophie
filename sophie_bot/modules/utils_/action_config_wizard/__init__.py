"""
Action Configuration Wizard System

A declarative system for creating interactive action configuration interfaces.
Consumers create an ``ActionWizardConfig`` and call ``create_action_config_system``
to get handler classes for ``__handlers__`` registration.

Example usage:

    from sophie_bot.modules.utils_.action_config_wizard import (
        ActionWizardConfig,
        create_action_config_system,
    )

    cfg = ActionWizardConfig(
        module_name="my_module",
        callback_prefix="my_module_action",
        wizard_title="My Module Action Configuration",
        success_message="Action updated successfully!",
        get_model_func=get_my_model,
        get_actions_func=get_current_actions,
        add_action_func=add_my_action,
        remove_action_func=remove_my_action,
        command_filter=CMDFilter("myaction"),
        admin_filter=UserRestricting(admin=True),
    )

    (
        MyWizard,
        MyCallback,
        MySetup,
        MyDone,
        MyCancel,
        MySettings,
    ) = create_action_config_system(cfg)
"""

from .config import ActionWizardConfig
from .handler import create_action_config_system

__all__ = ["ActionWizardConfig", "create_action_config_system"]
