from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramUnauthorizedError
from aiogram.types import ChatPermissions

from sophie_bot.modules.restrictions.services.silent import (
    build_silent_action_doc,
    collect_message_ids_for_cleanup,
    log_silent_action,
)
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


def test_build_silent_action_doc_renders_duration_and_reason() -> None:
    doc = build_silent_action_doc(
        chat_title="Moderation chat",
        target_user_id=USER_TID,
        target_user_name="Target",
        actor_user_id=987,
        actor_user_name="Moderator",
        actor_label="Banned by",
        title="User temporarily banned",
        reason="spam",
        duration_text="2 hours",
    )

    rendered = str(doc)

    assert "Moderation chat" in rendered
    assert "Target" in rendered
    assert "Moderator" in rendered
    assert "2 hours" in rendered
    assert "spam" in rendered


def test_collect_message_ids_for_cleanup_includes_reply_message() -> None:
    reply_to_message = SimpleNamespace(message_id=101)
    message = SimpleNamespace(message_id=100, reply_to_message=reply_to_message)

    assert collect_message_ids_for_cleanup(message, 102) == [100, 102, 101]


@pytest.mark.asyncio
async def test_log_silent_action_includes_duration(monkeypatch: pytest.MonkeyPatch) -> None:
    log_event_mock = AsyncMock()

    monkeypatch.setattr("sophie_bot.modules.restrictions.services.silent.log_event", log_event_mock)

    reply_to_message = SimpleNamespace(text="bad message")
    duration = timedelta(hours=2)

    await log_silent_action(
        chat_tid=CHAT_TID,
        actor_user_id=987,
        event_type=None,
        target_user_id=USER_TID,
        reply_to_message=reply_to_message,
        reason="spam",
        until_date=duration,
    )

    assert log_event_mock.await_count == 1
    payload = log_event_mock.await_args.args[3]
    assert payload["target_user_id"] == USER_TID
    assert payload["reason"] == "spam"
    assert payload["duration"] == duration.total_seconds()


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
