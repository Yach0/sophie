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
from sophie_bot.modules.restrictions.utils.restrictions import execute_restriction
from sophie_bot.shared.actions import RestrictionAction


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "bot_method", "expected_kwargs"),
    [
        (
            RestrictionAction.BAN,
            "ban_chat_member",
            {"until_date": None},
        ),
        (
            RestrictionAction.KICK,
            "unban_chat_member",
            {},
        ),
        (
            RestrictionAction.UNBAN,
            "unban_chat_member",
            {"only_if_banned": True},
        ),
    ],
)
async def test_restriction_executor_calls_expected_bot_method(
    mock_bot: AsyncMock,
    action: RestrictionAction,
    bot_method: str,
    expected_kwargs: dict[str, object],
) -> None:
    result = await execute_restriction(
        mock_bot,
        action,
        CHAT_TID,
        USER_TID,
    )

    assert result.action is action
    assert result.applied is True
    getattr(mock_bot, bot_method).assert_awaited_once_with(
        CHAT_TID,
        USER_TID,
        **expected_kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    [
        RestrictionAction.BAN,
        RestrictionAction.MUTE,
        RestrictionAction.RESTRICT,
    ],
)
async def test_restriction_executor_forwards_duration(
    mock_bot: AsyncMock,
    action: RestrictionAction,
) -> None:
    duration = timedelta(hours=24)

    result = await execute_restriction(
        mock_bot,
        action,
        CHAT_TID,
        USER_TID,
        until_date=duration,
    )

    assert result.applied is True
    bot_method = (
        mock_bot.ban_chat_member
        if action is RestrictionAction.BAN
        else mock_bot.restrict_chat_member
    )
    assert bot_method.await_args.kwargs["until_date"] == duration


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "bot_method", "error"),
    [
        (
            RestrictionAction.BAN,
            "ban_chat_member",
            TelegramBadRequest(
                method="test",
                message="Bad Request: not enough rights",
            ),
        ),
        (
            RestrictionAction.KICK,
            "unban_chat_member",
            TelegramForbiddenError(
                method="test",
                message="Forbidden: bot was kicked from the group chat",
            ),
        ),
        (
            RestrictionAction.MUTE,
            "restrict_chat_member",
            TelegramUnauthorizedError(
                method="test",
                message="Unauthorized",
            ),
        ),
        (
            RestrictionAction.UNMUTE,
            "restrict_chat_member",
            TelegramForbiddenError(
                method="test",
                message="Forbidden: bot is not a member",
            ),
        ),
        (
            RestrictionAction.UNBAN,
            "unban_chat_member",
            TelegramBadRequest(
                method="test",
                message="Bad Request: user not found",
            ),
        ),
    ],
)
async def test_restriction_executor_reports_telegram_failures(
    mock_bot: AsyncMock,
    action: RestrictionAction,
    bot_method: str,
    error: Exception,
) -> None:
    getattr(mock_bot, bot_method).side_effect = error

    result = await execute_restriction(
        mock_bot,
        action,
        CHAT_TID,
        USER_TID,
    )

    assert result.action is action
    assert result.applied is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "expected_permissions"),
    [
        (
            RestrictionAction.MUTE,
            {
                "can_send_messages": False,
            },
        ),
        (
            RestrictionAction.UNMUTE,
            {
                "can_send_messages": True,
                "can_send_audios": True,
                "can_send_documents": True,
                "can_send_photos": True,
                "can_send_videos": True,
                "can_send_video_notes": True,
                "can_send_voice_notes": True,
                "can_send_polls": True,
                "can_send_other_messages": True,
                "can_add_web_page_previews": True,
            },
        ),
        (
            RestrictionAction.RESTRICT,
            {
                "can_send_messages": True,
                "can_send_audios": False,
                "can_send_documents": False,
                "can_send_photos": False,
                "can_send_videos": False,
                "can_send_video_notes": False,
                "can_send_voice_notes": False,
                "can_send_polls": False,
                "can_send_other_messages": False,
                "can_add_web_page_previews": False,
            },
        ),
    ],
)
async def test_restriction_executor_builds_expected_permissions(
    mock_bot: AsyncMock,
    action: RestrictionAction,
    expected_permissions: dict[str, bool],
) -> None:
    result = await execute_restriction(
        mock_bot,
        action,
        CHAT_TID,
        USER_TID,
    )

    assert result.applied is True
    call_kwargs = mock_bot.restrict_chat_member.await_args.kwargs
    permissions: ChatPermissions = call_kwargs["permissions"]
    for permission, expected in expected_permissions.items():
        assert getattr(permissions, permission) is expected
    if action is not RestrictionAction.UNMUTE:
        assert call_kwargs["until_date"] is None
