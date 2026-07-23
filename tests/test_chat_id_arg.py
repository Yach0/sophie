"""Regression tests for SophieChatIDArg accepting negative chat IDs.

Groups, supergroups and channels have negative Telegram IDs. The arg used to inherit
UserIDArg.check_type, which rejects the leading "-", so `/connect <supergroup_id>` and any
other command taking a chat ID by number silently failed to match.
"""

from __future__ import annotations

import pytest

from sophie_bot.args.chats import SophieChatIDArg


@pytest.mark.parametrize(
    "text,expected",
    [
        ("-1001234567890", True),  # supergroup / channel
        ("123456789", True),  # private chat (same shape as a user id)
        ("-100 ", True),  # trailing whitespace is tolerated
        ("@sophie", False),  # a username is not a numeric id
        ("abc", False),
        ("-", False),  # a lone minus is not a number
        ("", False),
        ("   ", False),  # whitespace-only must not raise
    ],
)
async def test_check_type_accepts_negative_chat_ids(text: str, expected: bool) -> None:
    assert await SophieChatIDArg().check_type(text) is expected
