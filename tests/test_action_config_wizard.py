from __future__ import annotations

from typing import Any

import pytest
from beanie import PydanticObjectId

from sophie_bot.modules.utils_.action_config_wizard.config import ActionWizardConfig
from sophie_bot.modules.utils_.action_config_wizard.handler import (
    _ACTION_WIZARD_CONFIGS,
    _get_active_setup_config,
    _get_interactive_setup_chat_iid_raw,
)
from sophie_bot.modules.utils_.action_config_wizard.state import WizardState


class DummyFSMContext:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.state: Any = None

    async def get_data(self) -> dict[str, Any]:
        return dict(self.data)

    async def update_data(self, **kwargs: Any) -> None:
        self.data = dict(kwargs)

    async def set_state(self, state_value: Any) -> None:
        self.state = state_value

    async def clear(self) -> None:
        self.data = {}
        self.state = None


@pytest.mark.asyncio
async def test_replace_setup_context_clears_stale_keys() -> None:
    fsm_context = DummyFSMContext()
    wizard_state = WizardState(fsm_context)  # type: ignore[arg-type]
    chat_iid = PydanticObjectId()

    await wizard_state.replace_setup_context(
        action_setup_name="ban_user",
        action_setup_chat_tid=str(chat_iid),
        action_setup_callback_prefix="warn_action_max",
    )
    await wizard_state.replace_setup_context(
        setting_setup_action="ban_user",
        setting_setup_setting_id="change_ban_duration",
        setting_setup_chat_tid=str(chat_iid),
        setting_setup_callback_prefix="warn_action_max",
    )

    state_data = await wizard_state.get_data()

    assert "action_setup_name" not in state_data
    assert "action_setup_chat_tid" not in state_data
    assert state_data["setting_setup_action"] == "ban_user"
    assert state_data["setting_setup_chat_tid"] == str(chat_iid)


def test_get_interactive_setup_chat_iid_prefers_active_setting_context() -> None:
    state_data = {
        "action_setup_chat_tid": "stale-action-chat",
        "setting_setup_action": "ban_user",
        "setting_setup_chat_tid": "active-setting-chat",
    }

    assert _get_interactive_setup_chat_iid_raw(state_data) == "active-setting-chat"


def test_get_active_setup_config_uses_state_module() -> None:
    warns_each_cfg = ActionWizardConfig(
        module_name="warns_each",
        callback_prefix="warn_action_each",
        wizard_title="Each",
        success_message="Saved",
        get_model_func=None,  # type: ignore[arg-type]
        get_actions_func=None,  # type: ignore[arg-type]
        add_action_func=None,  # type: ignore[arg-type]
        remove_action_func=None,  # type: ignore[arg-type]
        command_filter=None,  # type: ignore[arg-type]
        admin_filter=None,  # type: ignore[arg-type]
    )
    warns_max_cfg = ActionWizardConfig(
        module_name="warns_max",
        callback_prefix="warn_action_max",
        wizard_title="Max",
        success_message="Saved",
        get_model_func=None,  # type: ignore[arg-type]
        get_actions_func=None,  # type: ignore[arg-type]
        add_action_func=None,  # type: ignore[arg-type]
        remove_action_func=None,  # type: ignore[arg-type]
        command_filter=None,  # type: ignore[arg-type]
        admin_filter=None,  # type: ignore[arg-type]
    )

    _ACTION_WIZARD_CONFIGS["warns_max"] = warns_max_cfg

    state_data = {"acw_module": "warns_max"}

    assert _get_active_setup_config(state_data, warns_each_cfg) is warns_max_cfg
