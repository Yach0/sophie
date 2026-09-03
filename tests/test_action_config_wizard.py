from __future__ import annotations

from typing import Any

import pytest
from aiogram.types import InlineKeyboardButton
from beanie import PydanticObjectId

from sophie_bot.modules.utils_.action_config_wizard.config import ActionDraft, ActionWizardConfig
from sophie_bot.modules.utils_.action_config_wizard.views import render_home_view
from sophie_bot.modules.utils_.wizard import (
    WizardCallback,
    WizardFSM,
    WizardScopeFilter,
    WizardSession,
    WizardView,
    build_wizard_navigation,
)


class DummyFSMContext:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {"unrelated": "kept"}
        self.state: Any = None

    async def get_data(self) -> dict[str, Any]:
        return dict(self.data)

    async def update_data(self, **kwargs: Any) -> None:
        self.data.update(kwargs)

    async def set_data(self, data: dict[str, Any]) -> None:
        self.data = dict(data)

    async def set_state(self, state_value: Any) -> None:
        self.state = state_value


@pytest.mark.asyncio
async def test_session_round_trip_uses_one_key_and_preserves_unrelated_state() -> None:
    state = DummyFSMContext()
    session = WizardSession(state, "filter_action")
    chat_iid = PydanticObjectId()

    await session.start(chat_iid, {"actions": {"kick_user": None}})
    await session.start_input(action_name="ai_text")

    assert set(state.data) == {"unrelated", "wizard"}
    assert await session.get_draft() == {"actions": {"kick_user": None}}
    assert await session.get_input_context() == {"action_name": "ai_text"}
    assert await session.is_active(chat_iid)
    assert state.state == WizardFSM.interactive_input

    await session.clear_input()
    assert set(state.data) == {"unrelated", "wizard"}
    assert await session.get_input_context() is None
    assert await session.get_draft() == {"actions": {"kick_user": None}}
    assert await session.is_active(chat_iid)
    assert state.state is None

    await session.clear()
    assert state.data == {"unrelated": "kept"}
    assert state.state is None

@pytest.mark.asyncio
async def test_scope_filter_only_compares_scope_without_clearing_state() -> None:
    state = DummyFSMContext()
    state.data["wizard"] = {"scope": "warn_action_each"}
    scope_filter = WizardScopeFilter("warn_action_each")

    assert await scope_filter(state) is True
    state.data["wizard"]["scope"] = "filter_action"
    assert await scope_filter(state) is False
    assert state.data["wizard"] == {"scope": "filter_action"}


def test_wizard_navigation_has_only_inline_navigation_controls() -> None:
    pagination = [InlineKeyboardButton(text="Next", callback_data="page:1")]
    markup = build_wizard_navigation(
        pagination=pagination,
        done_callback="done",
        back_callback="back",
        cancel_callback="cancel",
    )

    assert markup is not None
    assert [[button.callback_data for button in row] for row in markup.inline_keyboard] == [
        ["page:1"],
        ["done"],
        ["back", "cancel"],
    ]
    assert [button.text for button in markup.inline_keyboard[1]] == ["✅ Done"]


def test_action_draft_and_callback_contracts() -> None:
    draft = ActionDraft(actions={"reply": {"text": "hello"}})
    assert draft.model_dump(mode="json") == {"actions": {"reply": {"text": "hello"}}}
    callback = WizardCallback(scope="filter_action", op="setting", arg="reply:reply_text")
    assert WizardCallback.unpack(callback.pack()) == callback


def test_home_view_keeps_rich_action_buttons_separate_from_inline_navigation() -> None:
    config = ActionWizardConfig(
        scope="test",
        title="Test",
        done_message="Saved",
        max_actions=1,
        draft_model=ActionDraft,
        load_draft=None,
        save_draft=lambda *_args: None,  # type: ignore[arg-type]
    )
    view = render_home_view(config, ActionDraft())
    assert isinstance(view, WizardView)
    assert view.markup is not None
    assert not any(btn.text == "✅ Done" for row in view.markup.inline_keyboard for btn in row)
    assert view.markup.inline_keyboard[-1][0].text == "❌ Cancel"
    assert "➕ Set action" in view.doc.to_rich()
