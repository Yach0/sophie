from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock

import pytest

from sophie_bot.modules.utils_.status_handler import StatusHandlerABC, StatusIntHandlerABC


class BooleanStatusHandler(StatusHandlerABC[bool]):
    header_text = "Status"
    status_texts: ClassVar[dict[bool, str]] = {True: "enabled", False: "disabled"}

    @staticmethod
    def filters() -> tuple:
        return ()

    async def get_status(self) -> bool:
        return False

    async def set_status(self, new_status: bool) -> None:
        return None


class IntegerStatusHandler(StatusIntHandlerABC):
    header_text = "Status"

    @staticmethod
    def filters() -> tuple:
        return ()

    async def get_status(self) -> int:
        return 1

    async def set_status(self, new_status: int) -> None:
        return None


@pytest.mark.asyncio
async def test_boolean_status_change_returns_successful_reply() -> None:
    reply_message = object()
    event = SimpleNamespace(reply=AsyncMock(return_value=reply_message))
    handler = BooleanStatusHandler(
        event,
        context=SimpleNamespace(connection=SimpleNamespace(title="Test chat")),
    )

    result = await handler.change_status(True)

    assert result is reply_message
    handler.event.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_integer_status_change_returns_successful_reply() -> None:
    reply_message = object()
    event = SimpleNamespace(reply=AsyncMock(return_value=reply_message))
    handler = IntegerStatusHandler(
        event,
        context=SimpleNamespace(connection=SimpleNamespace(title="Test chat")),
    )

    result = await handler.change_status(2)

    assert result is reply_message
    handler.event.reply.assert_awaited_once()
