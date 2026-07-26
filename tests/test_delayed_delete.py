from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from sophie_bot.modules.utils_ import delayed_delete
from sophie_bot.modules.utils_.delayed_delete import delete_messages_after_delay, schedule_message_deletion

CHAT_TID = -1001234567890


async def test_delete_messages_after_delay_uses_bot_delete_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    delete_mock = AsyncMock()
    sleep_mock = AsyncMock()

    monkeypatch.setattr("sophie_bot.modules.utils_.delayed_delete.bot.delete_messages", delete_mock)
    monkeypatch.setattr("sophie_bot.modules.utils_.delayed_delete.asyncio.sleep", sleep_mock)

    await delete_messages_after_delay(CHAT_TID, [1, 2, 3], delay_seconds=7)

    sleep_mock.assert_awaited_once_with(7)
    delete_mock.assert_awaited_once_with(CHAT_TID, [1, 2, 3])


async def test_schedule_message_deletion_retains_and_clears_task(monkeypatch: pytest.MonkeyPatch) -> None:
    delete_mock = AsyncMock()
    started = asyncio.Event()

    real_sleep = asyncio.sleep

    async def fake_sleep(_delay: float) -> None:
        started.set()
        await real_sleep(0)

    monkeypatch.setattr("sophie_bot.modules.utils_.delayed_delete.bot.delete_messages", delete_mock)
    monkeypatch.setattr("sophie_bot.modules.utils_.delayed_delete.asyncio.sleep", fake_sleep)

    schedule_message_deletion(CHAT_TID, [1], delay_seconds=99)

    # The task must be strongly referenced while it sleeps, or the loop may collect it mid-delay.
    assert len(delayed_delete._background_tasks) == 1
    await started.wait()
    await asyncio.gather(*delayed_delete._background_tasks)

    delete_mock.assert_awaited_once_with(CHAT_TID, [1])
    assert delayed_delete._background_tasks == set()


async def test_schedule_message_deletion_ignores_empty_list() -> None:
    schedule_message_deletion(CHAT_TID, [])

    assert delayed_delete._background_tasks == set()
