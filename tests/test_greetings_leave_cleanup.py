from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import DeleteMessage
from aiogram.types import Chat, Message, User
from beanie import PydanticObjectId

from sophie_bot.modules.greetings.middlewares.leave_user import LeaveUserMiddleware


@pytest.mark.parametrize("reason", [None, "message can't be deleted", "message to delete not found", "forbidden"])
async def test_leave_cleanup_finishes_for_expected_deletion_outcomes(
    reason: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    message = Message(
        message_id=17,
        date=0,
        chat=Chat(id=-100123, type="supergroup"),
        left_chat_member=User(id=42, first_name="Member", is_bot=False),
    )
    method = DeleteMessage(chat_id=message.chat.id, message_id=message.message_id)
    error = None
    if reason == "forbidden":
        error = TelegramForbiddenError(method=method, message=reason)
    elif reason is not None:
        error = TelegramBadRequest(method=method, message=f"Bad Request: {reason}")
    delete = AsyncMock(side_effect=error, return_value=True)
    monkeypatch.setattr(Message, "delete", delete)
    monkeypatch.setattr(
        "sophie_bot.modules.greetings.middlewares.leave_user.GreetingsModel.get_by_chat_iid",
        AsyncMock(return_value=SimpleNamespace(clean_service=SimpleNamespace(enabled=True))),
    )
    handler = AsyncMock()
    with pytest.raises(SkipHandler):
        await LeaveUserMiddleware()(
            handler, message, {"context": SimpleNamespace(event_chat=SimpleNamespace(iid=PydanticObjectId()))}
        )
    delete.assert_awaited_once()
    handler.assert_not_awaited()


async def test_leave_cleanup_propagates_unexpected_deletion_error(monkeypatch: pytest.MonkeyPatch) -> None:
    message = Message(
        message_id=17,
        date=0,
        chat=Chat(id=-100123, type="supergroup"),
        left_chat_member=User(id=42, first_name="Member", is_bot=False),
    )
    error = TelegramBadRequest(method=DeleteMessage(chat_id=-100123, message_id=17), message="UNEXPECTED")
    monkeypatch.setattr(Message, "delete", AsyncMock(side_effect=error))
    monkeypatch.setattr(
        "sophie_bot.modules.greetings.middlewares.leave_user.GreetingsModel.get_by_chat_iid",
        AsyncMock(return_value=SimpleNamespace(clean_service=SimpleNamespace(enabled=True))),
    )
    with pytest.raises(TelegramBadRequest) as raised:
        await LeaveUserMiddleware()(
            AsyncMock(), message, {"context": SimpleNamespace(event_chat=SimpleNamespace(iid=PydanticObjectId()))}
        )
    assert raised.value is error
