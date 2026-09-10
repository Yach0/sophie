from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from sophie_bot.modules.logging.events import LOG_EVENT_STRINGS, LogEvent
from sophie_bot.utils import group_whitelist_logging


@pytest.mark.asyncio
async def test_group_whitelist_exemption_helper_writes_debug_and_database_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    debug = Mock()
    event_logger = AsyncMock()
    monkeypatch.setattr(group_whitelist_logging, "log", SimpleNamespace(debug=debug))
    monkeypatch.setattr(group_whitelist_logging, "log_event", event_logger)

    await group_whitelist_logging.log_group_whitelist_exemption(-100123, 123456, "message_locks")

    debug.assert_called_once_with(
        "Group whitelist exemption",
        chat_tid=-100123,
        user_tid=123456,
        subsystem="message_locks",
    )
    event_logger.assert_awaited_once_with(
        -100123,
        123456,
        LogEvent.GROUP_WHITELIST_EXEMPTION,
        {
            "subsystem": "message_locks",
            "chat_tid": -100123,
            "user_tid": 123456,
        },
    )


def test_group_whitelist_exemption_event_has_display_name() -> None:
    assert LogEvent.GROUP_WHITELIST_EXEMPTION.value == "group_whitelist_exemption"
    assert LogEvent.GROUP_WHITELIST_EXEMPTION in LOG_EVENT_STRINGS
