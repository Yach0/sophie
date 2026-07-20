from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from sophie_bot.db.models.notes import Saveable
from sophie_bot.modules.filters.api.utils import build_filter_action_catalog
from sophie_bot.modules.filters.utils_.handle_action import (
    EffectiveFilterAction,
    handle_effective_filter_action,
)
from sophie_bot.modules.notes.utils import send as send_module
from sophie_bot.modules.purges.magic_handlers.modern_filter import DelMsgModern
from sophie_bot.modules.restrictions.actions.kick import KickModernAction
from sophie_bot.modules.rules.handlers.set import SetRulesHandler
from sophie_bot.modules.rules.magic_handlers.modern_filter import SendRulesAction
from sophie_bot.modules.warns.magic_handlers.modern_action import WarnModernAction

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


@pytest.mark.parametrize("action_cls", DATALESS_ACTIONS, ids=lambda cls: cls.name)
def test_dataless_actions_expose_data_object(action_cls: type) -> None:
    assert action_cls().data_object is None


def test_build_filter_action_catalog_handles_dataless_actions() -> None:
    actions = {action.name: action for action in (cls() for cls in DATALESS_ACTIONS)}

    with patch("sophie_bot.modules.filters.api.utils.ALL_MODERN_ACTIONS", actions):
        catalog = build_filter_action_catalog()

    assert {item.name for item in catalog} == {"send_rules", "kick_user", "delmsg"}
    assert all(item.data_schema is None for item in catalog)


@pytest.mark.asyncio
async def test_warn_filter_action_skips_admins(monkeypatch: pytest.MonkeyPatch) -> None:
    warn_user_mock = AsyncMock(return_value=(1, 3, None, SimpleNamespace(id="warn_iid")))
    monkeypatch.setattr("sophie_bot.modules.warns.magic_handlers.modern_action.warn_user", warn_user_mock)
    monkeypatch.setattr("sophie_bot.modules.utils_.admin.check_user_admin_permissions", AsyncMock(return_value=True))
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
    data: dict[str, Any] = {
        "chat_db": SimpleNamespace(tid=message.chat.id, iid="chat_iid"),
        "user_db": SimpleNamespace(tid=777, iid="user_iid"),
    }

    with patch(
        "sophie_bot.modules.filters.utils_.handle_action.ALL_MODERN_ACTIONS",
        {"warn_user": WarnModernAction()},
    ):
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
    target_db = SimpleNamespace(tid=777, iid="user_iid")

    monkeypatch.setattr("sophie_bot.modules.warns.magic_handlers.modern_action.warn_user", warn_user_mock)
    monkeypatch.setattr(
        "sophie_bot.modules.utils_.admin.check_user_admin_permissions", AsyncMock(return_value=False)
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
    data: dict[str, Any] = {
        "chat_db": SimpleNamespace(tid=message.chat.id, iid="chat_iid"),
        "user_db": target_db,
    }

    with patch(
        "sophie_bot.modules.filters.utils_.handle_action.ALL_MODERN_ACTIONS",
        {"warn_user": WarnModernAction()},
    ):
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
        "sophie_bot.modules.utils_.admin.check_user_admin_permissions", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        "sophie_bot.modules.warns.magic_handlers.modern_action.ChatModel.get_by_tid",
        AsyncMock(return_value=SimpleNamespace(tid=1234, iid="bot_iid")),
    )

    message = _make_message()
    data: dict[str, Any] = {
        "chat_db": SimpleNamespace(tid=message.chat.id, iid="chat_iid"),
        "user_db": SimpleNamespace(tid=777, iid="user_iid"),
    }

    with patch(
        "sophie_bot.modules.filters.utils_.handle_action.ALL_MODERN_ACTIONS",
        {"warn_user": WarnModernAction()},
    ):
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

    result = await SendRulesAction().handle(message, {"connection": connection}, None)

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

    handler = SetRulesHandler(message, connection=connection, content=None)
    await handler.handle()

    set_rules_mock.assert_not_awaited()
    message.reply.assert_awaited_once()
