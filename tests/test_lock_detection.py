from __future__ import annotations

from datetime import UTC, datetime

import pytest
from aiogram.enums import MessageEntityType
from aiogram.types import Chat, LinkPreviewOptions, Message, MessageEntity, User

from sophie_bot.modules.locks.utils.detect_lock import LOCK_TYPE_CHECKS, check_locks
from sophie_bot.modules.locks.utils.lock_types import ALL_LOCK_TYPES, LockType
from sophie_bot.shared.lock_constants import ENTITY_TYPES, LOCK_TYPE_DESCRIPTIONS


def _message(**kwargs: object) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=-1001234567890, type="supergroup"),
        from_user=User(id=42, is_bot=False, first_name="User"),
        **kwargs,
    )


def test_webpreview_lock_type_is_registered() -> None:
    assert LockType.WEB_PREVIEW in LOCK_TYPE_CHECKS
    assert LockType.WEB_PREVIEW in ALL_LOCK_TYPES
    assert LockType.WEB_PREVIEW in ENTITY_TYPES
    assert LockType.WEB_PREVIEW in LOCK_TYPE_DESCRIPTIONS


@pytest.mark.asyncio
async def test_command_lock_matches_bot_command_at_start() -> None:
    message = _message(
        text="/start hello", entities=[MessageEntity(type=MessageEntityType.BOT_COMMAND, offset=0, length=6)]
    )

    assert await check_locks(message, {LockType.COMMAND}) == LockType.COMMAND


@pytest.mark.asyncio
async def test_command_lock_ignores_bot_command_in_middle() -> None:
    message = _message(
        text="hello /start",
        entities=[MessageEntity(type=MessageEntityType.BOT_COMMAND, offset=6, length=6)],
    )

    assert await check_locks(message, {LockType.COMMAND}) is None


@pytest.mark.asyncio
async def test_webpreview_lock_matches_link_preview_message() -> None:
    message = _message(text="https://example.com", link_preview_options=LinkPreviewOptions(is_disabled=False))

    assert await check_locks(message, {LockType.WEB_PREVIEW}) == LockType.WEB_PREVIEW


@pytest.mark.asyncio
async def test_webpreview_lock_ignores_disabled_link_preview_message() -> None:
    message = _message(text="https://example.com", link_preview_options=LinkPreviewOptions(is_disabled=True))

    assert await check_locks(message, {LockType.WEB_PREVIEW}) is None
