from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message
from beanie import PydanticObjectId

from sophie_bot.modules.welcomesecurity.utils_.db_time_convert import convert_timedelta_or_str
from sophie_bot.modules.welcomesecurity.utils_.emoji_captcha import EmojiCaptchaData

# ─── db_time_convert tests ────────────────────────────────────────────────────


class TestDbTimeConvert:
    def test_db_time_convert_hours(self) -> None:
        """'2h' converts to timedelta(hours=2)."""
        result = convert_timedelta_or_str("2h")
        assert result == timedelta(hours=2)

    def test_db_time_convert_days(self) -> None:
        """'1d' converts to timedelta(days=1)."""
        result = convert_timedelta_or_str("1d")
        assert result == timedelta(days=1)

    def test_db_time_convert_weeks(self) -> None:
        """'1w' converts to timedelta(weeks=1)."""
        result = convert_timedelta_or_str("1w")
        assert result == timedelta(weeks=1)

    def test_db_time_convert_minutes(self) -> None:
        """'30m' converts to timedelta(minutes=30)."""
        result = convert_timedelta_or_str("30m")
        assert result == timedelta(minutes=30)

    def test_db_time_convert_invalid(self) -> None:
        """Invalid input (non-str, non-timedelta) raises TypeError."""
        with pytest.raises(TypeError, match="Cannot convert"):
            convert_timedelta_or_str(12345)  # type: ignore[arg-type]

    def test_db_time_convert_unknown_suffix_returns_zero(self) -> None:
        """Unknown suffix returns timedelta(seconds=0) as the default."""
        result = convert_timedelta_or_str("10x")
        assert result == timedelta(seconds=0)

    def test_db_time_convert_passthrough_timedelta(self) -> None:
        """If already a timedelta, returns it unchanged."""
        original = timedelta(hours=5)
        result = convert_timedelta_or_str(original)
        assert result is original


# ─── EmojiCaptchaData correctness tests ──────────────────────────────────────


class TestEmojiCaptchaData:
    def test_captcha_data_is_correct_when_aligned(self) -> None:
        """When base_emoji is at the same index in both rows, is_correct is True."""
        base = "🍎"
        back_row = ["🍇", "🍉", "🍎", "🍒", "🍌"]
        front_row = ["🍍", "🥝", "🍎", "🥭", "🥥"]

        data = EmojiCaptchaData(base_emoji=base, back_row=back_row, front_row=front_row)
        assert data.is_correct is True

    def test_captcha_data_is_incorrect_when_misaligned(self) -> None:
        """When base_emoji indices differ between rows, is_correct is False."""
        base = "🍎"
        back_row = ["🍇", "🍉", "🍎", "🍒", "🍌"]  # index 2
        front_row = ["🍎", "🥝", "🍍", "🥭", "🥥"]  # index 0

        data = EmojiCaptchaData(base_emoji=base, back_row=back_row, front_row=front_row)
        assert data.is_correct is False

    def test_captcha_move_to_right_shifts_front_row(self) -> None:
        """move_to_right rotates front_row so the last element moves to front."""
        data = EmojiCaptchaData(
            base_emoji="🍎",
            back_row=["🍇", "🍉", "🍎", "🍒", "🍌"],
            front_row=["🍍", "🥝", "🍎", "🥭", "🥥"],
        )
        data.move_to_right()
        assert data.front_row == ["🥥", "🍍", "🥝", "🍎", "🥭"]

    def test_captcha_move_to_left_shifts_front_row(self) -> None:
        """move_to_left rotates front_row so the first element moves to end."""
        data = EmojiCaptchaData(
            base_emoji="🍎",
            back_row=["🍇", "🍉", "🍎", "🍒", "🍌"],
            front_row=["🍍", "🥝", "🍎", "🥭", "🥥"],
        )
        data.move_to_left()
        assert data.front_row == ["🥝", "🍎", "🥭", "🥥", "🍍"]

    def test_captcha_alignment_after_moves(self) -> None:
        """Rotating front_row can change is_correct from True to False."""
        base = "🍎"
        # Aligned at index 2
        data = EmojiCaptchaData(
            base_emoji=base,
            back_row=["🍇", "🍉", "🍎", "🍒", "🍌"],
            front_row=["🍍", "🥝", "🍎", "🥭", "🥥"],
        )
        assert data.is_correct is True

        data.move_to_left()
        assert data.is_correct is False


# ─── WSUserModel basic creation test (with DB) ───────────────────────────────


@pytest.mark.asyncio
async def test_ws_user_model_basic_creation(db_init: Any) -> None:
    """WSUserModel can be created with required fields using mocked DB."""
    from sophie_bot.db.models import ChatModel
    from sophie_bot.db.models.ws_user import WSUserModel

    # Create mock chat documents for user and group
    user_chat = ChatModel(
        tid=11111,
        type="private",
        first_name_or_title="TestUser",
        username="testuser",
        is_bot=False,
        last_saw=datetime.now(UTC),
    )
    await user_chat.insert()

    group_chat = ChatModel(
        tid=-100222222,
        type="supergroup",
        first_name_or_title="Test Group",
        username="testgroup",
        is_bot=False,
        last_saw=datetime.now(UTC),
    )
    await group_chat.insert()

    # Create WSUserModel
    ws_user = WSUserModel(user=user_chat, group=group_chat, passed=False, is_join_request=False)
    await ws_user.insert()

    # Verify it was stored by retrieving by its own ID
    found = await WSUserModel.get(ws_user.id)
    assert found is not None
    assert found.passed is False
    assert found.is_join_request is False

    # Cleanup
    await ws_user.delete()
    await user_chat.delete()
    await group_chat.delete()


# ─── LockMutedUsers middleware tests ─────────────────────────────────────────


async def _consume_common_try(coro: Any, *_args: Any, **_kwargs: Any) -> None:
    """Stand-in for common_try that consumes the passed coroutine."""
    await coro


def _group_message(chat_tid: int, user_tid: int, chat_type: str = "supergroup") -> Any:
    message = MagicMock(spec=Message)
    message.from_user = SimpleNamespace(id=user_tid)
    message.chat = SimpleNamespace(id=chat_tid, type=chat_type)
    message.delete = AsyncMock()
    return message


@pytest.fixture
def lock_middleware_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.middlewares.lock_muted_users.is_enabled",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.middlewares.lock_muted_users.common_try",
        _consume_common_try,
    )


@pytest.mark.asyncio
async def test_lock_muted_users_middleware_skips_passed_users(
    monkeypatch: pytest.MonkeyPatch, lock_middleware_env: None
) -> None:
    """Users who have already passed captcha are not blocked by the middleware."""
    from sophie_bot.modules.welcomesecurity.middlewares.lock_muted_users import LockMutedUsers

    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.middlewares.lock_muted_users.WSUserModel.is_user",
        AsyncMock(return_value=SimpleNamespace(passed=True)),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.middlewares.lock_muted_users.is_user_admin",
        AsyncMock(return_value=False),
    )

    middleware = LockMutedUsers()
    handler = AsyncMock(return_value="handler_result")
    message = _group_message(-100123, 12345)
    data: dict[str, Any] = {
        "chat_db": SimpleNamespace(tid=-100123, iid=PydanticObjectId()),
        "user_db": SimpleNamespace(tid=12345, iid=PydanticObjectId()),
    }

    result = await middleware(handler, message, data)

    assert result == "handler_result"
    handler.assert_awaited_once_with(message, data)
    message.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_lock_muted_users_middleware_blocks_unpassed_users(
    monkeypatch: pytest.MonkeyPatch, lock_middleware_env: None
) -> None:
    """Users still in captcha (not passed) get their messages deleted and the handler is skipped."""
    from aiogram.dispatcher.event.bases import SkipHandler

    from sophie_bot.modules.welcomesecurity.middlewares.lock_muted_users import LockMutedUsers

    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.middlewares.lock_muted_users.WSUserModel.is_user",
        AsyncMock(return_value=SimpleNamespace(passed=False)),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.middlewares.lock_muted_users.is_user_admin",
        AsyncMock(return_value=False),
    )

    middleware = LockMutedUsers()
    handler = AsyncMock(return_value="handler_result")
    message = _group_message(-100123, 12345)
    data: dict[str, Any] = {
        "chat_db": SimpleNamespace(tid=-100123, iid=PydanticObjectId()),
        "user_db": SimpleNamespace(tid=12345, iid=PydanticObjectId()),
    }

    with pytest.raises(SkipHandler):
        await middleware(handler, message, data)

    handler.assert_not_awaited()
    message.delete.assert_awaited_once()


async def _saved_chat(tid: int, chat_type: str, title: str) -> Any:
    from sophie_bot.db.models import ChatModel

    chat = ChatModel(
        tid=tid,
        type=chat_type,
        first_name_or_title=title,
        username=None,
        is_bot=False,
        last_saw=datetime.now(UTC),
    )
    await chat.insert()
    # Re-read so tid/iid match what SaveChatsMiddleware puts in the context
    return await ChatModel.get_by_tid(tid)


@pytest.mark.asyncio
async def test_lock_muted_users_middleware_blocks_unpassed_users_with_real_admin_check(
    db_init: Any, monkeypatch: pytest.MonkeyPatch, lock_middleware_env: None
) -> None:
    """Regression: the real is_user_admin must not exempt an ordinary group member.

    ``is_user_admin`` is deliberately NOT mocked here — it is the call that used to
    short-circuit the middleware on every path. Only ``WSUserModel.is_user`` is
    stubbed, because its DBRef predicate cannot run under mongomock.
    """
    from aiogram.dispatcher.event.bases import SkipHandler

    from sophie_bot.modules.welcomesecurity.middlewares.lock_muted_users import LockMutedUsers

    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.middlewares.lock_muted_users.WSUserModel.is_user",
        AsyncMock(return_value=SimpleNamespace(passed=False)),
    )

    user_chat = await _saved_chat(44444, "private", "Unpassed User")
    group_chat = await _saved_chat(-100444444, "supergroup", "Locked Group")

    middleware = LockMutedUsers()
    handler = AsyncMock(return_value="handler_result")
    message = _group_message(group_chat.tid, user_chat.tid)
    data: dict[str, Any] = {"chat_db": group_chat, "user_db": user_chat}

    try:
        with pytest.raises(SkipHandler):
            await middleware(handler, message, data)

        handler.assert_not_awaited()
        message.delete.assert_awaited_once()
    finally:
        await user_chat.delete()
        await group_chat.delete()


@pytest.mark.asyncio
async def test_lock_muted_users_middleware_ignores_private_messages(
    db_init: Any, monkeypatch: pytest.MonkeyPatch, lock_middleware_env: None
) -> None:
    """In PMs SaveChatsMiddleware sets chat_db to the user itself; nothing may be locked."""
    from sophie_bot.modules.welcomesecurity.middlewares.lock_muted_users import LockMutedUsers

    is_user = AsyncMock(return_value=SimpleNamespace(passed=False))
    monkeypatch.setattr("sophie_bot.modules.welcomesecurity.middlewares.lock_muted_users.WSUserModel.is_user", is_user)

    user_chat = await _saved_chat(55555, "private", "PM User")

    middleware = LockMutedUsers()
    handler = AsyncMock(return_value="handler_result")
    message = _group_message(user_chat.tid, user_chat.tid, chat_type="private")
    data: dict[str, Any] = {"chat_db": user_chat, "user_db": user_chat}

    try:
        assert await middleware(handler, message, data) == "handler_result"
        message.delete.assert_not_awaited()
        is_user.assert_not_awaited()
    finally:
        await user_chat.delete()


@pytest.mark.asyncio
async def test_lock_muted_users_middleware_ignores_anonymous_admins(lock_middleware_env: None) -> None:
    """Anonymous admins have no user_db; the middleware must let them through."""
    from sophie_bot.modules.welcomesecurity.middlewares.lock_muted_users import LockMutedUsers

    middleware = LockMutedUsers()
    handler = AsyncMock(return_value="handler_result")
    message = _group_message(-100123, 12345)
    data: dict[str, Any] = {"chat_db": SimpleNamespace(tid=-100123, iid=PydanticObjectId()), "user_db": None}

    assert await middleware(handler, message, data) == "handler_result"
    message.delete.assert_not_awaited()
