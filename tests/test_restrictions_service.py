from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramUnauthorizedError
from aiogram.types import ChatPermissions

from sophie_bot.modules.restrictions.utils.restrictions import (
    ban_user,
    kick_user,
    mute_user,
    restrict_user,
    unban_user,
    unmute_user,
)


@pytest.fixture
def mock_bot() -> AsyncMock:
    bot = AsyncMock()
    bot.ban_chat_member = AsyncMock(return_value=True)
    bot.unban_chat_member = AsyncMock(return_value=True)
    bot.restrict_chat_member = AsyncMock(return_value=True)
    return bot


CHAT_TID = -1001234567890
USER_TID = 123456789


# --- ban_user tests ---


@pytest.mark.asyncio
async def test_ban_user_success(monkeypatch: pytest.MonkeyPatch, mock_bot: AsyncMock) -> None:
    monkeypatch.setattr("sophie_bot.modules.restrictions.utils.restrictions.bot", mock_bot)

    result = await ban_user(CHAT_TID, USER_TID)

    assert result is True
    mock_bot.ban_chat_member.assert_awaited_once_with(CHAT_TID, USER_TID, until_date=None)


@pytest.mark.asyncio
async def test_ban_user_with_duration(monkeypatch: pytest.MonkeyPatch, mock_bot: AsyncMock) -> None:
    monkeypatch.setattr("sophie_bot.modules.restrictions.utils.restrictions.bot", mock_bot)
    duration = timedelta(hours=24)

    result = await ban_user(CHAT_TID, USER_TID, until_date=duration)

    assert result is True
    mock_bot.ban_chat_member.assert_awaited_once_with(CHAT_TID, USER_TID, until_date=duration)


@pytest.mark.asyncio
async def test_ban_user_telegram_bad_request(monkeypatch: pytest.MonkeyPatch, mock_bot: AsyncMock) -> None:
    monkeypatch.setattr("sophie_bot.modules.restrictions.utils.restrictions.bot", mock_bot)
    mock_bot.ban_chat_member.side_effect = TelegramBadRequest(method="test", message="Bad Request: not enough rights")

    result = await ban_user(CHAT_TID, USER_TID)

    assert result is False


@pytest.mark.asyncio
async def test_ban_user_telegram_forbidden(monkeypatch: pytest.MonkeyPatch, mock_bot: AsyncMock) -> None:
    monkeypatch.setattr("sophie_bot.modules.restrictions.utils.restrictions.bot", mock_bot)
    mock_bot.ban_chat_member.side_effect = TelegramForbiddenError(
        method="test", message="Forbidden: bot was kicked from the group chat"
    )

    result = await ban_user(CHAT_TID, USER_TID)

    assert result is False


# --- kick_user tests ---


@pytest.mark.asyncio
async def test_kick_user_success(monkeypatch: pytest.MonkeyPatch, mock_bot: AsyncMock) -> None:
    monkeypatch.setattr("sophie_bot.modules.restrictions.utils.restrictions.bot", mock_bot)

    result = await kick_user(CHAT_TID, USER_TID)

    assert result is True
    mock_bot.unban_chat_member.assert_awaited_once_with(CHAT_TID, USER_TID)


@pytest.mark.asyncio
async def test_kick_user_failure(monkeypatch: pytest.MonkeyPatch, mock_bot: AsyncMock) -> None:
    monkeypatch.setattr("sophie_bot.modules.restrictions.utils.restrictions.bot", mock_bot)
    mock_bot.unban_chat_member.side_effect = TelegramBadRequest(method="test", message="Bad Request: not enough rights")

    result = await kick_user(CHAT_TID, USER_TID)

    assert result is False


# --- mute_user tests ---


@pytest.mark.asyncio
async def test_mute_user_success(monkeypatch: pytest.MonkeyPatch, mock_bot: AsyncMock) -> None:
    monkeypatch.setattr("sophie_bot.modules.restrictions.utils.restrictions.bot", mock_bot)

    result = await mute_user(CHAT_TID, USER_TID)

    assert result is True
    mock_bot.restrict_chat_member.assert_awaited_once()
    call_kwargs = mock_bot.restrict_chat_member.call_args.kwargs
    permissions: ChatPermissions = call_kwargs["permissions"]
    assert permissions.can_send_messages is False
    assert call_kwargs["until_date"] is None


@pytest.mark.asyncio
async def test_mute_user_with_duration(monkeypatch: pytest.MonkeyPatch, mock_bot: AsyncMock) -> None:
    monkeypatch.setattr("sophie_bot.modules.restrictions.utils.restrictions.bot", mock_bot)
    duration = timedelta(minutes=30)

    result = await mute_user(CHAT_TID, USER_TID, until_date=duration)

    assert result is True
    call_kwargs = mock_bot.restrict_chat_member.call_args.kwargs
    assert call_kwargs["until_date"] == duration


@pytest.mark.asyncio
async def test_mute_user_failure(monkeypatch: pytest.MonkeyPatch, mock_bot: AsyncMock) -> None:
    monkeypatch.setattr("sophie_bot.modules.restrictions.utils.restrictions.bot", mock_bot)
    mock_bot.restrict_chat_member.side_effect = TelegramUnauthorizedError(method="test", message="Unauthorized")

    result = await mute_user(CHAT_TID, USER_TID)

    assert result is False


# --- unmute_user tests ---


@pytest.mark.asyncio
async def test_unmute_user_success(monkeypatch: pytest.MonkeyPatch, mock_bot: AsyncMock) -> None:
    monkeypatch.setattr("sophie_bot.modules.restrictions.utils.restrictions.bot", mock_bot)

    result = await unmute_user(CHAT_TID, USER_TID)

    assert result is True
    mock_bot.restrict_chat_member.assert_awaited_once()
    call_kwargs = mock_bot.restrict_chat_member.call_args.kwargs
    permissions: ChatPermissions = call_kwargs["permissions"]
    assert permissions.can_send_messages is True
    assert permissions.can_send_audios is True
    assert permissions.can_send_documents is True
    assert permissions.can_send_photos is True
    assert permissions.can_send_videos is True
    assert permissions.can_send_video_notes is True
    assert permissions.can_send_voice_notes is True
    assert permissions.can_send_polls is True
    assert permissions.can_send_other_messages is True
    assert permissions.can_add_web_page_previews is True


@pytest.mark.asyncio
async def test_unmute_user_failure(monkeypatch: pytest.MonkeyPatch, mock_bot: AsyncMock) -> None:
    monkeypatch.setattr("sophie_bot.modules.restrictions.utils.restrictions.bot", mock_bot)
    mock_bot.restrict_chat_member.side_effect = TelegramForbiddenError(
        method="test", message="Forbidden: bot is not a member"
    )

    result = await unmute_user(CHAT_TID, USER_TID)

    assert result is False


# --- unban_user tests ---


@pytest.mark.asyncio
async def test_unban_user_success(monkeypatch: pytest.MonkeyPatch, mock_bot: AsyncMock) -> None:
    monkeypatch.setattr("sophie_bot.modules.restrictions.utils.restrictions.bot", mock_bot)

    result = await unban_user(CHAT_TID, USER_TID)

    assert result is True
    mock_bot.unban_chat_member.assert_awaited_once_with(CHAT_TID, USER_TID, only_if_banned=True)


@pytest.mark.asyncio
async def test_unban_user_failure(monkeypatch: pytest.MonkeyPatch, mock_bot: AsyncMock) -> None:
    monkeypatch.setattr("sophie_bot.modules.restrictions.utils.restrictions.bot", mock_bot)
    mock_bot.unban_chat_member.side_effect = TelegramBadRequest(method="test", message="Bad Request: user not found")

    result = await unban_user(CHAT_TID, USER_TID)

    assert result is False


# --- restrict_user tests ---


@pytest.mark.asyncio
async def test_restrict_user_success(monkeypatch: pytest.MonkeyPatch, mock_bot: AsyncMock) -> None:
    monkeypatch.setattr("sophie_bot.modules.restrictions.utils.restrictions.bot", mock_bot)

    result = await restrict_user(CHAT_TID, USER_TID)

    assert result is True
    mock_bot.restrict_chat_member.assert_awaited_once()
    call_kwargs = mock_bot.restrict_chat_member.call_args.kwargs
    permissions: ChatPermissions = call_kwargs["permissions"]
    # Text-only: can send messages but no media
    assert permissions.can_send_messages is True
    assert permissions.can_send_audios is False
    assert permissions.can_send_documents is False
    assert permissions.can_send_photos is False
    assert permissions.can_send_videos is False
    assert permissions.can_send_video_notes is False
    assert permissions.can_send_voice_notes is False
    assert permissions.can_send_polls is False
    assert permissions.can_send_other_messages is False
    assert permissions.can_add_web_page_previews is False
    assert call_kwargs["until_date"] is None


@pytest.mark.asyncio
async def test_restrict_user_with_duration(monkeypatch: pytest.MonkeyPatch, mock_bot: AsyncMock) -> None:
    monkeypatch.setattr("sophie_bot.modules.restrictions.utils.restrictions.bot", mock_bot)
    duration = timedelta(days=7)

    result = await restrict_user(CHAT_TID, USER_TID, until_date=duration)

    assert result is True
    call_kwargs = mock_bot.restrict_chat_member.call_args.kwargs
    assert call_kwargs["until_date"] == duration
