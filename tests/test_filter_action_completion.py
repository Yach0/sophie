from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.types import Chat, Message, User
from pymongo.errors import OperationFailure
from redis.exceptions import ConnectionError as RedisConnectionError

from sophie_bot.modules.ai.magic_handlers import modern_action as ai_module
from sophie_bot.modules.filters.utils_ import handle_action as dispatch_module
from sophie_bot.modules.filters.utils_.handle_action import EffectiveFilterAction, handle_effective_filter_action
from sophie_bot.modules.notes.magic_handlers.reply_action import ReplyModernAction
from sophie_bot.modules.purges.magic_handlers.modern_filter import DelMsgModern
from sophie_bot.modules.restrictions.actions.ban import BanModernAction
from sophie_bot.modules.restrictions.actions.kick import KickModernAction
from sophie_bot.modules.restrictions.actions.mute import MuteModernAction
from sophie_bot.modules.warns import utils as warn_utils
from sophie_bot.modules.warns.magic_handlers.modern_action import WarnModernAction
from sophie_bot.shared.actions import ActionCompletion, ModernActionABC, RestrictionResult, StoredAction
from tests.test_modern_actions_regressions import _make_message, _warn_action_data


@pytest.mark.parametrize(
    ("action", "module_path"),
    [
        (BanModernAction(), "sophie_bot.modules.restrictions.actions.base"),
        (MuteModernAction(), "sophie_bot.modules.restrictions.actions.base"),
        (KickModernAction(), "sophie_bot.modules.restrictions.actions.kick"),
        (DelMsgModern(), "sophie_bot.modules.purges.magic_handlers.modern_filter"),
        (WarnModernAction(), "sophie_bot.modules.warns.magic_handlers.modern_action"),
    ],
    ids=("ban", "mute", "kick", "delete", "warn"),
)
@pytest.mark.parametrize("applied", [True, False], ids=("success", "failure"))
async def test_filter_stats_survive_logging_failure(
    action: ModernActionABC, module_path: str, applied: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    message = _make_message()
    message.delete = AsyncMock(return_value=applied)
    data = _warn_action_data(message)
    data["services"].bot = Mock()
    data.update(filter_id="filter_iid", i18n=SimpleNamespace(current_locale="en_US"))
    data["services"].modules = SimpleNamespace(
        actions={action.definition.name: action.definition}, action_handlers={action.definition.name: action}
    )
    monkeypatch.setattr(dispatch_module, "is_user_admin", AsyncMock(return_value=False))
    monkeypatch.setattr(dispatch_module, "is_user_group_whitelisted", AsyncMock(return_value=False))
    monkeypatch.setattr(dispatch_module, "is_enabled", AsyncMock(return_value=True))
    record = AsyncMock()
    monkeypatch.setattr(dispatch_module, "record_filter_action", record)
    logging_error = OperationFailure("logging unavailable")
    monkeypatch.setattr(f"{module_path}.log_event", AsyncMock(side_effect=logging_error))
    if isinstance(action, WarnModernAction):
        monkeypatch.setattr(f"{module_path}.ChatModel.get_by_tid", AsyncMock(return_value=SimpleNamespace(tid=123)))
        monkeypatch.setattr(f"{module_path}.generate_restriction_reason", AsyncMock(return_value=None))
        monkeypatch.setattr(
            f"{module_path}.warn_user",
            AsyncMock(
                return_value=(1, 3, None, None), side_effect=None if applied else OperationFailure("warn failed")
            ),
        )
    elif not isinstance(action, DelMsgModern):
        monkeypatch.setattr(f"{module_path}.generate_restriction_reason", AsyncMock(return_value=None))
        monkeypatch.setattr(
            f"{module_path}.execute_restriction",
            AsyncMock(return_value=RestrictionResult(action=action.definition.restriction_action, applied=applied)),
        )
    matched_filter = SimpleNamespace(id="filter_iid")
    if applied or isinstance(action, WarnModernAction):
        with pytest.raises(OperationFailure) as raised:
            await handle_effective_filter_action(
                message, EffectiveFilterAction(action.definition.name, None), data, matched_filter
            )
        if applied:
            assert raised.value is logging_error
    else:
        await handle_effective_filter_action(
            message, EffectiveFilterAction(action.definition.name, None), data, matched_filter
        )
    if applied:
        record.assert_awaited_once_with("chat_iid", matched_filter, redis=data["services"].redis)
    else:
        record.assert_not_awaited()


@pytest.mark.parametrize("streaming", [False, True])
async def test_ai_filter_stats_survive_history_cache_failure(streaming: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    message = Message(
        message_id=42,
        date=0,
        chat=Chat(id=-100123, type="supergroup"),
        from_user=User(id=777, is_bot=False, first_name="Test"),
        text="hello",
    )
    data = _warn_action_data(message)
    data["context"].connection = SimpleNamespace(tid=message.chat.id, db_model=data["context"].event_chat)
    action = ai_module.AIReplyAction()
    data["services"].modules = SimpleNamespace(
        actions={action.definition.name: action.definition}, action_handlers={action.definition.name: action}
    )
    monkeypatch.setattr(dispatch_module, "is_enabled", AsyncMock(return_value=True))
    record = AsyncMock()
    monkeypatch.setattr(dispatch_module, "record_filter_action", record)
    monkeypatch.setattr(ai_module.ChatModel, "get_by_tid", AsyncMock(return_value=data["context"].event_chat))
    monkeypatch.setattr(ai_module.AIQuotaFilter, "__call__", AsyncMock(return_value=True))
    history = Mock(message_history=[], prompt="hello")
    history.add_from_cache = AsyncMock()
    history.add_from_message = AsyncMock()
    monkeypatch.setattr(ai_module, "OldContext", Mock(return_value=history))
    monkeypatch.setattr(
        ai_module, "get_chat_default_model_plan", AsyncMock(return_value=SimpleNamespace(primary="test"))
    )
    monkeypatch.setattr(ai_module, "is_enabled", AsyncMock(return_value=False))
    streamer = AsyncMock() if streaming else None
    if streamer:
        streamer.send_final.return_value = message
    monkeypatch.setattr(ai_module, "build_message_streamer", AsyncMock(return_value=streamer))
    monkeypatch.setattr(ai_module, "Agent", Mock())
    result = SimpleNamespace(usage=None, served_model=None, output="hello")
    monkeypatch.setattr(ai_module, "run_ai_text", AsyncMock(return_value=result))
    monkeypatch.setattr(ai_module, "run_ai_stream", AsyncMock(return_value=result))
    monkeypatch.setattr(ai_module, "build_chatbot_header", AsyncMock(return_value=""))
    monkeypatch.setattr(ai_module, "_build_fitting_reply_doc", AsyncMock(return_value=(Mock(), "hello")))
    monkeypatch.setattr(ai_module, "send_ai_rich_message", AsyncMock(return_value=message))
    cache_error = RedisConnectionError("history unavailable")
    monkeypatch.setattr(ai_module, "_cache_chatbot_reply", AsyncMock(side_effect=cache_error))
    matched_filter = SimpleNamespace(id="filter_iid")
    with pytest.raises(RedisConnectionError) as raised:
        await handle_effective_filter_action(
            message, EffectiveFilterAction("ai_text", {"prompt": "hello"}), data, matched_filter
        )
    assert raised.value is cache_error
    record.assert_awaited_once_with("chat_iid", matched_filter, redis=data["services"].redis)


async def test_warn_completion_precedes_count_bookkeeping(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _make_message()
    data = _warn_action_data(message)
    completion = ActionCompletion()
    data["action_completion"] = completion
    warn = SimpleNamespace(save=AsyncMock())
    monkeypatch.setattr(warn_utils.WarnSettingsModel, "get_or_create", AsyncMock(return_value=Mock()))
    monkeypatch.setattr(warn_utils, "WarnModel", Mock(return_value=warn))
    warn_utils.WarnModel.count_user_warns = AsyncMock(side_effect=OperationFailure("count unavailable"))
    with pytest.raises(OperationFailure, match="count unavailable"):
        await warn_utils.warn_user(
            data["context"].event_chat, data["context"].actor, Mock(), action_context=data, services=data["services"]
        )
    warn.save.assert_awaited_once()
    assert completion.completed


async def test_nested_warn_reply_does_not_defer_completed_warning() -> None:
    message = _make_message()
    data = _warn_action_data(message)
    completion = ActionCompletion(completed=True)
    data["action_completion"] = completion
    action = ReplyModernAction()
    data["services"].modules = SimpleNamespace(
        actions={action.definition.name: action.definition}, action_handlers={action.definition.name: action}
    )
    await warn_utils._execute_warn_actions(
        [StoredAction(name="reply", data={"text": "Warning"})],
        data["context"].event_chat,
        data["context"].actor,
        Mock(),
        reason=None,
        trigger_message=message,
        action_context=data,
        services=data["services"],
    )
    assert completion.completed
    assert not completion.deferred
