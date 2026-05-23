from __future__ import annotations

from typing import Any

from aiogram.fsm.context import FSMContext

from .config import ActionWizardConfig
from .state import WizardState

_ACTION_WIZARD_CONFIGS: dict[str, ActionWizardConfig] = {}


def get_wizard_state(data: dict[str, Any]) -> WizardState | None:
    state = data.get("state")
    if isinstance(state, FSMContext):
        return WizardState(state)
    return None


def get_fsm_context(data: dict[str, Any]) -> FSMContext | None:
    state = data.get("state")
    return state if isinstance(state, FSMContext) else None


def get_interactive_setup_chat_iid_raw(state_data: dict[str, Any]) -> Any:
    """Read the chat context for the currently active interactive setup mode."""
    if "setting_setup_action" in state_data:
        return state_data.get("setting_setup_chat_tid")
    return state_data.get("action_setup_chat_tid")


def get_active_setup_config(state_data: dict[str, Any], fallback_cfg: ActionWizardConfig) -> ActionWizardConfig:
    """Return the config for the active ACW session, falling back to the current handler config."""
    active_module_name = state_data.get("acw_module")
    if isinstance(active_module_name, str):
        return _ACTION_WIZARD_CONFIGS.get(active_module_name, fallback_cfg)
    return fallback_cfg
