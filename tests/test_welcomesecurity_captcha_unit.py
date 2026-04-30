from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
        """Invalid input (non-str, non-timedelta) raises ValueError."""
        with pytest.raises(ValueError, match="Cannot convert"):
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
        last_saw=datetime.now(timezone.utc),
    )
    await user_chat.insert()

    group_chat = ChatModel(
        tid=-100222222,
        type="supergroup",
        first_name_or_title="Test Group",
        username="testgroup",
        is_bot=False,
        last_saw=datetime.now(timezone.utc),
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


@pytest.mark.asyncio
async def test_lock_muted_users_middleware_skips_passed_users(monkeypatch: pytest.MonkeyPatch) -> None:
    """Users who have already passed captcha are not blocked by the middleware."""
    from sophie_bot.modules.welcomesecurity.middlewares.lock_muted_users import LockMutedUsers

    user_iid = PydanticObjectId()
    chat_iid = PydanticObjectId()

    # Create a mock WSUserModel that has passed
    passed_model = SimpleNamespace(passed=True)

    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.middlewares.lock_muted_users.WSUserModel.is_user",
        AsyncMock(return_value=passed_model),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.middlewares.lock_muted_users.is_user_admin",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.middlewares.lock_muted_users.is_enabled",
        AsyncMock(return_value=True),
    )

    middleware = LockMutedUsers()
    handler = AsyncMock(return_value="handler_result")

    # Create a mock message event (spec=Message so isinstance check passes)
    message = MagicMock(spec=Message)
    message.from_user = SimpleNamespace(id=12345)
    message.chat = SimpleNamespace(id=-100123, type="private")
    message.delete = AsyncMock()

    # Mock data with chat_db and user_db
    chat_db = SimpleNamespace(tid=-100123, iid=chat_iid)
    user_db = SimpleNamespace(tid=12345, iid=user_iid)
    data: dict[str, Any] = {"chat_db": chat_db, "user_db": user_db}

    result = await middleware(handler, message, data)

    # Handler should be called since the user passed
    assert result == "handler_result"
    handler.assert_awaited_once_with(message, data)
    message.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_lock_muted_users_middleware_blocks_unpassed_users(monkeypatch: pytest.MonkeyPatch) -> None:
    """Users still in captcha (not passed) get their messages deleted and handler is skipped."""
    from aiogram.dispatcher.event.bases import SkipHandler

    from sophie_bot.modules.welcomesecurity.middlewares.lock_muted_users import LockMutedUsers

    user_iid = PydanticObjectId()
    chat_iid = PydanticObjectId()

    # Create a mock WSUserModel that has NOT passed
    unpassed_model = SimpleNamespace(passed=False)

    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.middlewares.lock_muted_users.WSUserModel.is_user",
        AsyncMock(return_value=unpassed_model),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.middlewares.lock_muted_users.is_user_admin",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.middlewares.lock_muted_users.is_enabled",
        AsyncMock(return_value=True),
    )

    async def _consume_common_try(coro, *_args, **_kwargs):
        """Mock common_try that properly consumes the passed coroutine."""
        try:
            await coro
        except Exception:
            pass

    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.middlewares.lock_muted_users.common_try",
        _consume_common_try,
    )

    middleware = LockMutedUsers()
    handler = AsyncMock(return_value="handler_result")

    # Create a mock message event (spec=Message so isinstance check passes)
    message = MagicMock(spec=Message)
    message.from_user = SimpleNamespace(id=12345)
    message.chat = SimpleNamespace(id=-100123, type="private")
    message.delete = AsyncMock()

    # Mock data with chat_db and user_db
    chat_db = SimpleNamespace(tid=-100123, iid=chat_iid)
    user_db = SimpleNamespace(tid=12345, iid=user_iid)
    data: dict[str, Any] = {"chat_db": chat_db, "user_db": user_db}

    with pytest.raises(SkipHandler):
        await middleware(handler, message, data)

    # Handler should NOT have been called
    handler.assert_not_awaited()
