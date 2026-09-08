from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from babel.messages.extract import extract_from_file

from sophie_bot.db.models.notes import Saveable
from sophie_bot.modules.filters.api.utils import build_filter_action_catalog
from sophie_bot.modules.filters.utils_.handle_action import (
    EffectiveFilterAction,
    handle_effective_filter_action,
)
from sophie_bot.modules.notes.utils import send as send_module
from sophie_bot.modules.purges.magic_handlers.modern_filter import DelMsgModern
from sophie_bot.modules.restrictions.actions import ban as ban_action_module
from sophie_bot.modules.restrictions.actions import mute as mute_action_module
from sophie_bot.modules.restrictions.actions.ban import BanActionDataModel, BanModernAction
from sophie_bot.modules.restrictions.actions.kick import KickModernAction
from sophie_bot.modules.restrictions.actions.mute import MuteActionDataModel, MuteModernAction
from sophie_bot.modules.rules.handlers.set import SetRulesHandler
from sophie_bot.modules.rules.magic_handlers.modern_filter import SendRulesAction
from sophie_bot.modules.warns.magic_handlers.modern_action import WarnModernAction
from sophie_bot.shared.actions import RestrictionResult

# Actions typed ModernActionABC[None]: they take no data, so data_object must resolve to None
# rather than raising AttributeError.
DATALESS_ACTIONS = (SendRulesAction, KickModernAction, DelMsgModern)


def _make_message(chat_tid: int = -100123, user_tid: int = 777, chat_title: str = "Sophie Chat") -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_tid, title=chat_title, username=None),
        from_user=SimpleNamespace(id=user_tid, first_name="Vasya", last_name=None, username=None),
        new_chat_members=None,
        message_id=42,
        text=None,
        caption=None,
        reply=AsyncMock(),
    )
def _warn_action_data(message: SimpleNamespace) -> dict[str, Any]:
    action = WarnModernAction()
    return {
        "context": SimpleNamespace(
            event_chat=SimpleNamespace(tid=message.chat.id, iid="chat_iid"),
            actor=SimpleNamespace(tid=777, iid="user_iid"),
        ),
        "services": SimpleNamespace(
            modules=SimpleNamespace(
                actions={action.definition.name: action.definition},
                action_handlers={action.definition.name: action},
            )
        ),
    }




@pytest.mark.parametrize(
    "action_cls",
    DATALESS_ACTIONS,
    ids=lambda action_class: action_class.definition.name,
)
def test_dataless_actions_expose_data_object(action_cls: type) -> None:
    assert action_cls.definition.data_object is None


def test_build_filter_action_catalog_handles_dataless_actions() -> None:
    actions = {
        action_class.definition.name: action_class.definition
        for action_class in DATALESS_ACTIONS
    }
    catalog = build_filter_action_catalog(actions)

    assert {item.name for item in catalog} == {
        "send_rules",
        "kick_user",
        "delmsg",
    }
    assert all(item.data_schema is None for item in catalog)


@pytest.mark.parametrize(
    ("action", "action_data", "expected_text"),
    [
        (
            BanModernAction(),
            BanActionDataModel(ban_duration=None),
            "was automatically banned based on a filter action",
        ),
        (
            MuteModernAction(),
            MuteActionDataModel(mute_duration=None),
            "was automatically muted based on a filter action",
        ),
    ],
    ids=("ban", "mute"),
)
@pytest.mark.asyncio
async def test_restriction_filter_action_translates_plain_message_id(
    action: BanModernAction | MuteModernAction,
    action_data: BanActionDataModel | MuteActionDataModel,
    expected_text: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute_restriction_mock = AsyncMock(
        return_value=RestrictionResult(
            action=action.definition.restriction_action,
            applied=True,
        )
    )
    monkeypatch.setattr(
        "sophie_bot.modules.restrictions.actions.base.execute_restriction",
        execute_restriction_mock,
    )
    result = await action.handle(
        _make_message(),
        {
            "context": SimpleNamespace(event_chat=None),
            "i18n": SimpleNamespace(current_locale="en_US"),
            "services": SimpleNamespace(bot=object()),
        },
        action_data,
    )
    assert result is not None
    result_html = result.to_html()
    assert 'tg://user?id=777">Vasya</a>' in result_html
    assert expected_text in result_html

    execute_restriction_mock.assert_awaited_once()

@pytest.mark.parametrize(
    ("module_file", "message_id"),
    [
        (ban_action_module.__file__, BanModernAction.auto_banned_text),
        (mute_action_module.__file__, MuteModernAction.auto_banned_text),
    ],
    ids=("ban", "mute"),
)
def test_restriction_filter_action_message_id_is_extractable(module_file: str, message_id: str) -> None:
    extracted_message_ids = {
        messages
        for _line_number, messages, _comments, _context in extract_from_file("python", Path(module_file))
        if isinstance(messages, str)
    }

    assert message_id in extracted_message_ids


@pytest.mark.asyncio
async def test_warn_filter_action_skips_admins(monkeypatch: pytest.MonkeyPatch) -> None:
    warn_user_mock = AsyncMock(return_value=(1, 3, None, SimpleNamespace(id="warn_iid")))
    monkeypatch.setattr("sophie_bot.modules.warns.magic_handlers.modern_action.warn_user", warn_user_mock)
    monkeypatch.setattr(
        "sophie_bot.modules.filters.utils_.handle_action.is_user_admin",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.warns.magic_handlers.modern_action.ChatModel.get_by_tid",
        AsyncMock(return_value=SimpleNamespace(tid=1234, iid="bot_iid")),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.warns.magic_handlers.modern_action.generate_restriction_reason",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr("sophie_bot.modules.warns.magic_handlers.modern_action.log_event", AsyncMock())

    message = _make_message()
    data = _warn_action_data(message)

    result = await handle_effective_filter_action(
        message,
        EffectiveFilterAction(name="warn_user", data={"reason": None}),
        data,
        SimpleNamespace(id="filter_iid"),
    )

    assert result is None
    warn_user_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_warn_filter_action_still_warns_non_admins(monkeypatch: pytest.MonkeyPatch) -> None:
    warn_user_mock = AsyncMock(return_value=(1, 3, None, SimpleNamespace(id="warn_iid")))
    bot_db = SimpleNamespace(tid=1234, iid="bot_iid")

    monkeypatch.setattr("sophie_bot.modules.warns.magic_handlers.modern_action.warn_user", warn_user_mock)
    monkeypatch.setattr(
        "sophie_bot.modules.filters.utils_.handle_action.is_user_admin",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.warns.magic_handlers.modern_action.ChatModel.get_by_tid",
        AsyncMock(return_value=bot_db),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.warns.magic_handlers.modern_action.generate_restriction_reason",
        AsyncMock(return_value=None),
    )

    message = _make_message()
    data = _warn_action_data(message)

    result = await handle_effective_filter_action(
        message,
        EffectiveFilterAction(name="warn_user", data={"reason": None}),
        data,
        SimpleNamespace(id="filter_iid"),
    )

    assert result is not None
    warn_user_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_warn_action_data_survives_the_admin_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dispatcher must still build the data model when the action defines data_object."""
    captured: dict[str, Any] = {}

    async def fake_warn_user(chat: Any, user: Any, admin: Any, reason: Any, **kwargs: Any) -> tuple:
        captured["reason"] = reason
        return 1, 3, None, SimpleNamespace(id="warn_iid")

    monkeypatch.setattr("sophie_bot.modules.warns.magic_handlers.modern_action.warn_user", fake_warn_user)
    monkeypatch.setattr(
        "sophie_bot.modules.filters.utils_.handle_action.is_user_admin",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.warns.magic_handlers.modern_action.ChatModel.get_by_tid",
        AsyncMock(return_value=SimpleNamespace(tid=1234, iid="bot_iid")),
    )

    message = _make_message()
    data = _warn_action_data(message)

    await handle_effective_filter_action(
        message,
        EffectiveFilterAction(name="warn_user", data={"reason": "No links"}),
        data,
        SimpleNamespace(id="filter_iid"),
    )

    assert captured["reason"] == "No links"


@pytest.mark.asyncio
async def test_send_rules_action_processes_fillings_for_text_only_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_kwargs: dict[str, Any] = {}

    class FakeSendMessage:
        def __init__(self, **kwargs: Any) -> None:
            captured_kwargs.update(kwargs)

        def emit(self, bot: object) -> object:
            async def emit_result() -> object:
                return SimpleNamespace(message_id=7)

            return emit_result()

    monkeypatch.setattr(send_module, "SendMessage", FakeSendMessage)
    monkeypatch.setattr(
        "sophie_bot.modules.rules.magic_handlers.modern_filter.RulesModel.get_rules",
        AsyncMock(return_value=Saveable(text="Welcome {mention} to {chatname}", version=2)),
    )

    message = _make_message()
    connection = SimpleNamespace(db_model=SimpleNamespace(iid="chat_iid", tid=message.chat.id))

    result = await SendRulesAction().handle(
        message,
        {
            "context": SimpleNamespace(connection=connection),
            "services": SimpleNamespace(bot=object()),
        },
        None,
    )

    # The rules are sent as their own message, so the action reports what it sent
    # instead of returning text for the caller to aggregate.
    assert isinstance(result, list)
    assert "{chatname}" not in captured_kwargs["text"]
    assert "{mention}" not in captured_kwargs["text"]
    assert "Sophie Chat" in captured_kwargs["text"]


@pytest.mark.asyncio
async def test_set_rules_rejects_empty_content(monkeypatch: pytest.MonkeyPatch) -> None:
    set_rules_mock = AsyncMock()
    monkeypatch.setattr("sophie_bot.modules.rules.handlers.set.RulesModel.set_rules", set_rules_mock)
    monkeypatch.setattr(
        "sophie_bot.modules.rules.handlers.set.parse_saveable",
        AsyncMock(return_value=Saveable(text=None, version=2)),
    )

    message = _make_message()
    message.reply_to_message = None
    connection = SimpleNamespace(db_model=SimpleNamespace(iid="chat_iid", tid=message.chat.id), title="Sophie Chat")

    handler = SetRulesHandler(
        message,
        context=SimpleNamespace(connection=connection),
        content=None,
    )
    await handler.handle()

    set_rules_mock.assert_not_awaited()
    message.reply.assert_awaited_once()
