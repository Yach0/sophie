from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sophie_bot.modules.purges.handlers.delete import DelMsgCmdHandler
from sophie_bot.modules.purges.handlers.purge import PurgeMessagesHandler

CHAT_TID = -1001483164428
USER_TID = 5126697778


def _make_event(
    *,
    message_id: int,
    reply_message_id: int | None,
    reply_age: timedelta,
) -> SimpleNamespace:
    now = datetime.now(tz=UTC)
    reply_to_message = (
        None
        if reply_message_id is None
        else SimpleNamespace(message_id=reply_message_id, date=now - reply_age)
    )
    return SimpleNamespace(
        chat=SimpleNamespace(id=CHAT_TID),
        from_user=SimpleNamespace(id=USER_TID),
        message_id=message_id,
        date=now,
        reply_to_message=reply_to_message,
        reply=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_purge_deletes_only_the_replied_message_and_newer(monkeypatch: pytest.MonkeyPatch) -> None:
    event = _make_event(message_id=105, reply_message_id=100, reply_age=timedelta(minutes=5))
    delete_messages = AsyncMock(return_value=True)

    monkeypatch.setattr("sophie_bot.modules.purges.handlers.purge.bot.delete_messages", delete_messages)
    monkeypatch.setattr("sophie_bot.modules.purges.handlers.purge.bot.send_message", AsyncMock())
    monkeypatch.setattr("sophie_bot.modules.purges.handlers.purge.track_purge", lambda count: None)
    monkeypatch.setattr("sophie_bot.modules.purges.handlers.purge.log_event", AsyncMock())
    monkeypatch.setattr("sophie_bot.modules.purges.handlers.purge.sleep", AsyncMock())

    await PurgeMessagesHandler(event).handle()

    deleted: list[int] = []
    for call in delete_messages.await_args_list:
        deleted.extend(call.args[1])

    assert deleted == list(range(100, 106))
    assert 99 not in deleted, "purge must not delete the message before the replied-to one"


@pytest.mark.asyncio
async def test_purge_refuses_when_replied_message_is_older_than_48_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = _make_event(message_id=105, reply_message_id=100, reply_age=timedelta(days=3))
    delete_messages = AsyncMock(return_value=True)
    log_event = AsyncMock()

    monkeypatch.setattr("sophie_bot.modules.purges.handlers.purge.bot.delete_messages", delete_messages)
    monkeypatch.setattr("sophie_bot.modules.purges.handlers.purge.log_event", log_event)

    await PurgeMessagesHandler(event).handle()

    assert delete_messages.await_count == 0
    assert log_event.await_count == 0
    assert "48 hours" in event.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_delete_refuses_when_replied_message_is_older_than_48_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = _make_event(message_id=105, reply_message_id=100, reply_age=timedelta(days=3))
    delete_messages = AsyncMock(return_value=True)
    log_event = AsyncMock()

    monkeypatch.setattr("sophie_bot.modules.purges.handlers.delete.bot.delete_messages", delete_messages)
    monkeypatch.setattr("sophie_bot.modules.purges.handlers.delete.log_event", log_event)

    await DelMsgCmdHandler(event).handle()

    assert delete_messages.await_count == 0, "must not attempt a delete that Telegram will silently skip"
    assert log_event.await_count == 0, "must not log MESSAGE_DELETED for a deletion that never happened"
    assert "48 hours" in event.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_delete_removes_recent_replied_message_and_the_command(monkeypatch: pytest.MonkeyPatch) -> None:
    event = _make_event(message_id=105, reply_message_id=100, reply_age=timedelta(minutes=5))
    delete_messages = AsyncMock(return_value=True)
    log_event = AsyncMock()

    monkeypatch.setattr("sophie_bot.modules.purges.handlers.delete.bot.delete_messages", delete_messages)
    monkeypatch.setattr("sophie_bot.modules.purges.handlers.delete.log_event", log_event)

    await DelMsgCmdHandler(event).handle()

    assert delete_messages.await_args.args[1] == [105, 100]
    assert log_event.await_count == 1
