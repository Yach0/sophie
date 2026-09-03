from __future__ import annotations

from typing import Any

import pytest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from beanie import PydanticObjectId

from sophie_bot.modules.utils_.action_config_wizard.config import ActionWizardConfig
from sophie_bot.modules.utils_.action_config_wizard.context import (
    _ACTION_WIZARD_CONFIGS,
    get_active_setup_config,
    get_interactive_setup_chat_iid_raw,
)
from sophie_bot.modules.utils_.action_config_wizard.renderer import WizardRenderer
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


class DummyActionContext:
    async def load(self, chat_iid: Any) -> Any:
        del chat_iid
        return None

    async def validate(self, chat_iid: Any, draft: Any, event: Any = None, connection: Any = None) -> None:
        del chat_iid, draft, event, connection

    async def commit(self, chat_iid: Any, draft: Any, event: Any = None, connection: Any = None) -> None:
        del chat_iid, draft, event, connection

    def update_control(self, draft: Any, control_name: str) -> bool:
        del draft, control_name
        return False

    def render_details(self, draft: Any) -> list[tuple[str, str]]:
        del draft
        return []

    def render_controls(self, draft: Any, callback_prefix: str) -> list[list[Any]]:
        del draft, callback_prefix
        return []


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


def test_setup_message_uses_rich_buttons() -> None:
    document = WizardRenderer.rich_setup_message(
        "Configure",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Save", callback_data="save")],
            ]
        ),
    )

    rich_text = document.to_rich()

    assert '<tg-button type="callback_data" data="save">Save</tg-button>' in rich_text


@pytest.mark.asyncio
async def test_aggregate_draft_is_replaced_and_cleared() -> None:
    fsm_context = DummyFSMContext()
    wizard_state = WizardState(fsm_context)  # type: ignore[arg-type]
    chat_iid = PydanticObjectId()

    await wizard_state.start_session("filters", chat_iid, {"actions": {"mute": {}}, "metadata": {"handler": "spam"}})
    assert await wizard_state.get_draft() == {"actions": {"mute": {}}, "metadata": {"handler": "spam"}}
    await wizard_state.clear()
    assert await wizard_state.get_draft() is None


def test_get_interactive_setup_chat_iid_prefers_active_setting_context() -> None:
    state_data = {
        "action_setup_chat_tid": "stale-action-chat",
        "setting_setup_action": "ban_user",
        "setting_setup_chat_tid": "active-setting-chat",
    }

    assert get_interactive_setup_chat_iid_raw(state_data) == "active-setting-chat"


def test_get_active_setup_config_uses_state_module() -> None:
    warns_each_cfg = ActionWizardConfig(
        module_name="warns_each",
        callback_prefix="warn_action_each",
        wizard_title="Each",
        success_message="Saved",
        context=DummyActionContext(),  # type: ignore[arg-type]
        command_filter=None,  # type: ignore[arg-type]
        admin_filter=None,  # type: ignore[arg-type]
    )
    warns_max_cfg = ActionWizardConfig(
        module_name="warns_max",
        callback_prefix="warn_action_max",
        wizard_title="Max",
        success_message="Saved",
        context=DummyActionContext(),  # type: ignore[arg-type]
        command_filter=None,  # type: ignore[arg-type]
        admin_filter=None,  # type: ignore[arg-type]
    )

    _ACTION_WIZARD_CONFIGS["warns_max"] = warns_max_cfg
    state_data = {"acw_module": "warns_max"}
    assert get_active_setup_config(state_data, warns_each_cfg) is warns_max_cfg
