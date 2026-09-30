from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramUnauthorizedError,
)
from aiogram.types import ChatMemberMember, ChatMemberRestricted, ChatPermissions, User

from sophie_bot.db.models.mute_permissions import MutePermissionsModel
from sophie_bot.modules.restrictions.services.silent import (
    build_silent_action_doc,
    collect_message_ids_for_cleanup,
    log_silent_action,
)
from sophie_bot.modules.restrictions.utils.restrictions import execute_restriction, restore_expired_permissions
from sophie_bot.shared.actions import RestrictionAction

pytestmark = pytest.mark.usefixtures("db_init")


@pytest.fixture(autouse=True)
async def clean_mute_snapshots() -> AsyncGenerator[None]:
    await MutePermissionsModel.delete_all()
    yield
    await MutePermissionsModel.delete_all()


@pytest.fixture
def mock_bot() -> AsyncMock:
    bot = AsyncMock()
    bot.ban_chat_member = AsyncMock(return_value=True)
    bot.unban_chat_member = AsyncMock(return_value=True)
    bot.restrict_chat_member = AsyncMock(return_value=True)
    bot.get_chat_member = AsyncMock(return_value=make_member(True))
    bot.get_chat = AsyncMock(
        return_value=SimpleNamespace(permissions=ChatPermissions(**dict.fromkeys(ChatPermissions.model_fields, True)))
    )
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
    bot_method = mock_bot.ban_chat_member if action is RestrictionAction.BAN else mock_bot.restrict_chat_member
    assert bot_method.await_args.kwargs["until_date"] == (duration if action is RestrictionAction.BAN else None)
    if action is not RestrictionAction.BAN:
        snapshot = await MutePermissionsModel.find_one({"chat_tid": CHAT_TID})
        assert snapshot is not None and snapshot.expires_at is not None


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
                "can_invite_users": True,
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
    if action is RestrictionAction.UNMUTE:
        mock_bot.get_chat_member.return_value = make_member(False)
        await MutePermissionsModel(
            chat_tid=CHAT_TID,
            user_tid=USER_TID,
            permissions=ChatPermissions(**dict.fromkeys(ChatPermissions.model_fields, True)),
            applied=True,
            applied_permissions=ChatPermissions(**dict.fromkeys(ChatPermissions.model_fields, False)),
        ).insert()
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


@pytest.mark.asyncio
async def test_mute_snapshots_existing_restrictions_and_unmute_restores_them(
    mock_bot: AsyncMock,
) -> None:
    existing_permissions = ChatPermissions(
        can_send_messages=True,
        can_send_audios=False,
        can_send_documents=False,
        can_send_photos=True,
        can_send_videos=False,
        can_send_video_notes=False,
        can_send_voice_notes=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
        can_invite_users=False,
    )
    member_data = existing_permissions.model_dump()
    member_data = {field_name: value if value is not None else True for field_name, value in member_data.items()}
    expected_permissions = ChatPermissions(**member_data)
    original_member = ChatMemberRestricted.model_validate(
        {
            "user": User(id=USER_TID, is_bot=False, first_name="Target"),
            "status": "restricted",
            "is_member": True,
            "until_date": datetime.fromtimestamp(0, UTC),
            **member_data,
        }
    )
    muted_member = ChatMemberRestricted.model_validate(
        {
            "user": User(id=USER_TID, is_bot=False, first_name="Target"),
            "status": "restricted",
            "is_member": True,
            "until_date": datetime.fromtimestamp(0, UTC),
            **dict.fromkeys(ChatPermissions.model_fields, False),
        }
    )
    mock_bot.get_chat_member = AsyncMock(side_effect=[original_member, muted_member])

    await execute_restriction(mock_bot, RestrictionAction.MUTE, CHAT_TID, USER_TID)
    snapshot = await MutePermissionsModel.find_one(
        MutePermissionsModel.chat_tid == CHAT_TID,
        MutePermissionsModel.user_tid == USER_TID,
    )
    assert snapshot is not None

    await execute_restriction(mock_bot, RestrictionAction.UNMUTE, CHAT_TID, USER_TID)

    restored_permissions = mock_bot.restrict_chat_member.await_args_list[-1].kwargs["permissions"]
    assert restored_permissions == expected_permissions
    assert (
        await MutePermissionsModel.find_one(
            MutePermissionsModel.chat_tid == CHAT_TID,
            MutePermissionsModel.user_tid == USER_TID,
        )
        is None
    )
    assert mock_bot.get_chat_member.await_count == 2


@pytest.mark.asyncio
async def test_failed_mute_does_not_leave_a_snapshot(mock_bot: AsyncMock) -> None:
    member_permissions = ChatPermissions(**dict.fromkeys(ChatPermissions.model_fields, True))
    mock_bot.get_chat_member = AsyncMock(
        return_value=ChatMemberRestricted(
            user=User(id=USER_TID, is_bot=False, first_name="Target"),
            status="restricted",
            is_member=True,
            until_date=datetime.fromtimestamp(0, UTC),
            **member_permissions.model_dump(),
        )
    )
    mock_bot.restrict_chat_member.side_effect = TelegramBadRequest(method="test", message="not enough rights")

    result = await execute_restriction(mock_bot, RestrictionAction.MUTE, CHAT_TID, USER_TID)

    assert result.applied is False
    assert (
        await MutePermissionsModel.find_one(
            MutePermissionsModel.chat_tid == CHAT_TID,
            MutePermissionsModel.user_tid == USER_TID,
        )
        is None
    )


@pytest.mark.asyncio
async def test_unmute_without_snapshot_does_not_grant_permissions(mock_bot: AsyncMock) -> None:
    result = await execute_restriction(mock_bot, RestrictionAction.UNMUTE, CHAT_TID, USER_TID)

    assert result.applied is False
    mock_bot.restrict_chat_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_mutes_keep_one_original_snapshot(mock_bot: AsyncMock) -> None:
    member_permissions = ChatPermissions(**dict.fromkeys(ChatPermissions.model_fields, True))
    mock_bot.get_chat_member = AsyncMock(
        return_value=ChatMemberRestricted(
            user=User(id=USER_TID, is_bot=False, first_name="Target"),
            status="restricted",
            is_member=True,
            until_date=datetime.fromtimestamp(0, UTC),
            **member_permissions.model_dump(),
        )
    )

    mock_bot.get_chat_member.side_effect = [mock_bot.get_chat_member.return_value, make_member(False)]
    results = await asyncio.gather(
        execute_restriction(mock_bot, RestrictionAction.MUTE, CHAT_TID, USER_TID),
        execute_restriction(mock_bot, RestrictionAction.MUTE, CHAT_TID, USER_TID),
    )

    assert all(result.applied for result in results)
    assert (
        await MutePermissionsModel.find(
            MutePermissionsModel.chat_tid == CHAT_TID,
            MutePermissionsModel.user_tid == USER_TID,
        ).count()
        == 1
    )


@pytest.mark.asyncio
async def test_failed_unmute_keeps_snapshot_for_retry(mock_bot: AsyncMock) -> None:
    permissions = ChatPermissions(**dict.fromkeys(ChatPermissions.model_fields, True))
    await MutePermissionsModel(
        chat_tid=CHAT_TID,
        user_tid=USER_TID,
        permissions=permissions,
        applied=True,
        applied_permissions=ChatPermissions(**dict.fromkeys(ChatPermissions.model_fields, False)),
    ).insert()
    mock_bot.get_chat_member.return_value = make_member(False)
    mock_bot.restrict_chat_member.side_effect = TelegramBadRequest(method="test", message="not enough rights")

    result = await execute_restriction(mock_bot, RestrictionAction.UNMUTE, CHAT_TID, USER_TID)

    assert result.applied is False
    assert (
        await MutePermissionsModel.find_one(
            MutePermissionsModel.chat_tid == CHAT_TID,
            MutePermissionsModel.user_tid == USER_TID,
        )
        is not None
    )


@pytest.mark.asyncio
async def test_mute_of_normal_member_restores_group_defaults_without_all_true_snapshot(
    mock_bot: AsyncMock,
) -> None:
    mock_bot.get_chat_member = AsyncMock(
        return_value=ChatMemberMember(
            user=User(id=USER_TID, is_bot=False, first_name="Target"),
            status="member",
        )
    )

    await execute_restriction(mock_bot, RestrictionAction.MUTE, CHAT_TID, USER_TID)

    snapshot = await MutePermissionsModel.find_one(
        MutePermissionsModel.chat_tid == CHAT_TID,
        MutePermissionsModel.user_tid == USER_TID,
    )
    assert snapshot is not None
    assert snapshot.permissions is None
    assert snapshot.restore_group_defaults is True

    mock_bot.get_chat_member.return_value = make_member(False)
    await execute_restriction(mock_bot, RestrictionAction.UNMUTE, CHAT_TID, USER_TID)

    restored_permissions = mock_bot.restrict_chat_member.await_args_list[-1].kwargs["permissions"]
    assert all(getattr(restored_permissions, field_name) is True for field_name in ChatPermissions.model_fields)


@pytest.mark.asyncio
async def test_false_telegram_result_is_not_treated_as_applied(mock_bot: AsyncMock) -> None:
    mock_bot.restrict_chat_member.return_value = False

    result = await execute_restriction(mock_bot, RestrictionAction.RESTRICT, CHAT_TID, USER_TID)

    assert result.applied is False


def make_member(allowed: bool, until_date: datetime | None = None) -> ChatMemberRestricted:
    return ChatMemberRestricted.model_validate(
        {
            "user": User(id=USER_TID, is_bot=False, first_name="Target"),
            "status": "restricted",
            "is_member": True,
            "until_date": until_date or datetime.fromtimestamp(0, UTC),
            **dict.fromkeys(ChatPermissions.model_fields, allowed),
        }
    )


@pytest.mark.asyncio
async def test_timed_mute_restores_original_at_expiry(mock_bot: AsyncMock) -> None:
    original = make_member(True, datetime.now(UTC).replace(microsecond=0) + timedelta(hours=3))
    original = original.model_copy(update={"can_invite_users": False})
    mock_bot.get_chat_member.side_effect = [original, make_member(False)]
    await execute_restriction(mock_bot, RestrictionAction.MUTE, CHAT_TID, USER_TID, until_date=timedelta(hours=1))
    assert mock_bot.restrict_chat_member.await_args.kwargs["until_date"] is None
    snapshot = await MutePermissionsModel.find_one({"chat_tid": CHAT_TID, "user_tid": USER_TID})
    assert snapshot is not None
    snapshot.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await snapshot.save()
    await restore_expired_permissions(mock_bot)
    restored = mock_bot.restrict_chat_member.await_args.kwargs
    assert restored["permissions"].can_invite_users is False
    assert restored["until_date"] == original.until_date
    assert restored["use_independent_chat_permissions"] is True
    assert await MutePermissionsModel.find_one({"chat_tid": CHAT_TID}) is None


@pytest.mark.asyncio
async def test_expired_original_restriction_releases_to_defaults(mock_bot: AsyncMock) -> None:
    original = make_member(True, datetime.now(UTC) - timedelta(minutes=1))
    original = original.model_copy(update={"can_invite_users": False})
    mock_bot.get_chat_member.side_effect = [original, make_member(False)]
    await execute_restriction(mock_bot, RestrictionAction.MUTE, CHAT_TID, USER_TID)
    await execute_restriction(mock_bot, RestrictionAction.UNMUTE, CHAT_TID, USER_TID)
    assert all(mock_bot.restrict_chat_member.await_args.kwargs["permissions"].model_dump().values())


@pytest.mark.asyncio
async def test_newer_admin_restriction_is_preserved(mock_bot: AsyncMock) -> None:
    changed = make_member(False, datetime.now(UTC) + timedelta(hours=2))
    mock_bot.get_chat_member.side_effect = [make_member(True), changed]
    await execute_restriction(mock_bot, RestrictionAction.MUTE, CHAT_TID, USER_TID)
    result = await execute_restriction(mock_bot, RestrictionAction.UNMUTE, CHAT_TID, USER_TID)
    assert result.applied is False
    assert mock_bot.restrict_chat_member.await_count == 1
    assert await MutePermissionsModel.find_one({"chat_tid": CHAT_TID}) is None


@pytest.mark.asyncio
async def test_newer_admin_release_is_preserved(mock_bot: AsyncMock) -> None:
    mock_bot.get_chat_member.side_effect = [
        make_member(True),
        ChatMemberMember(user=make_member(True).user, status="member"),
    ]
    await execute_restriction(mock_bot, RestrictionAction.MUTE, CHAT_TID, USER_TID)
    result = await execute_restriction(mock_bot, RestrictionAction.UNMUTE, CHAT_TID, USER_TID)
    assert result.applied is True
    assert mock_bot.restrict_chat_member.await_count == 1


@pytest.mark.asyncio
async def test_remute_after_external_change_captures_new_state(mock_bot: AsyncMock) -> None:
    changed = make_member(True)
    changed = changed.model_copy(update={"can_invite_users": False})
    mock_bot.get_chat_member.side_effect = [make_member(True), changed, make_member(False)]
    await execute_restriction(mock_bot, RestrictionAction.MUTE, CHAT_TID, USER_TID)
    await execute_restriction(mock_bot, RestrictionAction.MUTE, CHAT_TID, USER_TID)
    await execute_restriction(mock_bot, RestrictionAction.UNMUTE, CHAT_TID, USER_TID)
    assert mock_bot.restrict_chat_member.await_args.kwargs["permissions"].can_invite_users is False


@pytest.mark.asyncio
async def test_welcome_restriction_reuses_captcha_snapshot(mock_bot: AsyncMock) -> None:
    original = make_member(True)
    original = original.model_copy(update={"can_invite_users": False})
    mock_bot.get_chat_member.side_effect = [original, make_member(False)]
    await execute_restriction(mock_bot, RestrictionAction.MUTE, CHAT_TID, USER_TID)
    await execute_restriction(mock_bot, RestrictionAction.RESTRICT, CHAT_TID, USER_TID, until_date=timedelta(hours=1))
    snapshot = await MutePermissionsModel.find_one({"chat_tid": CHAT_TID})
    assert snapshot is not None
    assert snapshot.permissions.can_invite_users is False
    assert snapshot.applied_permissions.can_invite_users is False
    assert snapshot.expires_at is not None


@pytest.mark.asyncio
async def test_restore_respects_tightened_group_defaults(mock_bot: AsyncMock) -> None:
    mock_bot.get_chat_member.side_effect = [make_member(True), make_member(False)]
    await execute_restriction(mock_bot, RestrictionAction.MUTE, CHAT_TID, USER_TID)
    defaults = ChatPermissions(**dict.fromkeys(ChatPermissions.model_fields, True))
    defaults.can_invite_users = False
    mock_bot.get_chat.return_value.permissions = defaults
    await execute_restriction(mock_bot, RestrictionAction.UNMUTE, CHAT_TID, USER_TID)
    assert mock_bot.restrict_chat_member.await_args.kwargs["permissions"].can_invite_users is False


@pytest.mark.asyncio
async def test_ambiguous_network_failure_retains_snapshot_for_retry(mock_bot: AsyncMock) -> None:
    mock_bot.get_chat_member.side_effect = [make_member(True), make_member(False)]
    mock_bot.restrict_chat_member.side_effect = [TelegramNetworkError(method="test", message="timeout"), True]
    result = await execute_restriction(mock_bot, RestrictionAction.MUTE, CHAT_TID, USER_TID)
    assert result.applied is False
    assert await MutePermissionsModel.find_one({"chat_tid": CHAT_TID}) is not None
    result = await execute_restriction(mock_bot, RestrictionAction.UNMUTE, CHAT_TID, USER_TID)
    assert result.applied is True


@pytest.mark.asyncio
async def test_false_unmute_keeps_snapshot_for_retry(mock_bot: AsyncMock) -> None:
    mock_bot.get_chat_member.side_effect = [make_member(True), make_member(False)]
    await execute_restriction(mock_bot, RestrictionAction.MUTE, CHAT_TID, USER_TID)
    mock_bot.restrict_chat_member.return_value = False
    assert not (await execute_restriction(mock_bot, RestrictionAction.UNMUTE, CHAT_TID, USER_TID)).applied
    assert await MutePermissionsModel.find_one({"chat_tid": CHAT_TID}) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("ambiguous", [False, True])
async def test_nearly_expired_original_restriction_is_released_after_restore(
    mock_bot: AsyncMock, ambiguous: bool
) -> None:
    deadline = datetime.now(UTC) + timedelta(seconds=50)
    original = make_member(True, deadline).model_copy(update={"can_invite_users": False})
    mock_bot.get_chat_member.side_effect = [
        original,
        make_member(False),
        original.model_copy(update={"until_date": datetime.fromtimestamp(0, UTC)}),
        original.model_copy(update={"until_date": datetime.fromtimestamp(0, UTC)}),
    ]
    await execute_restriction(mock_bot, RestrictionAction.MUTE, CHAT_TID, USER_TID)
    if ambiguous:
        mock_bot.restrict_chat_member.side_effect = [TelegramNetworkError(method="test", message="timeout"), True, True]
    await execute_restriction(mock_bot, RestrictionAction.UNMUTE, CHAT_TID, USER_TID)
    snapshot = await MutePermissionsModel.find_one({"chat_tid": CHAT_TID})
    assert snapshot is not None
    assert mock_bot.restrict_chat_member.await_args.kwargs["permissions"].can_invite_users is False
    result = await execute_restriction(mock_bot, RestrictionAction.UNMUTE, CHAT_TID, USER_TID)
    assert result.applied is True
    assert mock_bot.restrict_chat_member.await_args.kwargs["permissions"].can_invite_users is False
    assert mock_bot.restrict_chat_member.await_args.kwargs["until_date"] is None
    snapshot = await MutePermissionsModel.find_one({"chat_tid": CHAT_TID})
    assert snapshot is not None
    assert snapshot.restore_group_defaults is False
    assert snapshot.previous_until == deadline.replace(microsecond=deadline.microsecond // 1000 * 1000)
    assert snapshot.expires_at == snapshot.previous_until
    with patch("sophie_bot.modules.restrictions.utils.restrictions.datetime", wraps=datetime) as clock:
        clock.now.return_value = deadline
        await restore_expired_permissions(mock_bot)
    assert all(mock_bot.restrict_chat_member.await_args.kwargs["permissions"].model_dump().values())
    assert await MutePermissionsModel.find_one({"chat_tid": CHAT_TID}) is None


@pytest.mark.asyncio
async def test_legacy_snapshot_loads_without_inventing_ownership(mock_bot: AsyncMock) -> None:
    await MutePermissionsModel.get_pymongo_collection().insert_one(
        {
            "chat_tid": CHAT_TID,
            "user_tid": USER_TID,
            "permissions": make_member(True).model_dump(include=set(ChatPermissions.model_fields)),
            "applied": True,
        }
    )
    snapshot = await MutePermissionsModel.find_one({"chat_tid": CHAT_TID})
    assert snapshot is not None and snapshot.applied_permissions is None
    mock_bot.get_chat_member.return_value = make_member(False)
    result = await execute_restriction(mock_bot, RestrictionAction.UNMUTE, CHAT_TID, USER_TID)
    assert result.applied is False
    mock_bot.restrict_chat_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_expiry_sweep_does_not_restore_a_renewed_mute(mock_bot: AsyncMock) -> None:
    mock_bot.get_chat_member.side_effect = [make_member(True), make_member(False)]
    await execute_restriction(mock_bot, RestrictionAction.MUTE, CHAT_TID, USER_TID, until_date=timedelta(hours=1))
    result = await execute_restriction(mock_bot, RestrictionAction.UNMUTE, CHAT_TID, USER_TID, expired_only=True)
    assert result.applied is False
    assert mock_bot.get_chat_member.await_count == 1
    assert mock_bot.restrict_chat_member.await_count == 1
    assert await MutePermissionsModel.find_one({"chat_tid": CHAT_TID}) is not None


@pytest.mark.asyncio
async def test_ambiguous_welcome_replacement_can_restore_when_telegram_kept_captcha_mute(mock_bot: AsyncMock) -> None:
    original = make_member(True).model_copy(update={"can_invite_users": False})
    mock_bot.get_chat_member.side_effect = [original, make_member(False), make_member(False)]
    await execute_restriction(mock_bot, RestrictionAction.MUTE, CHAT_TID, USER_TID)
    mock_bot.restrict_chat_member.side_effect = [TelegramNetworkError(method="test", message="timeout"), True]
    result = await execute_restriction(
        mock_bot, RestrictionAction.RESTRICT, CHAT_TID, USER_TID, until_date=timedelta(hours=1)
    )
    assert result.applied is False
    result = await execute_restriction(mock_bot, RestrictionAction.UNMUTE, CHAT_TID, USER_TID)
    assert result.applied is True
    assert mock_bot.restrict_chat_member.await_args.kwargs["permissions"].can_invite_users is False


@pytest.mark.asyncio
async def test_ambiguous_near_expiry_restore_can_release_when_telegram_kept_mute(mock_bot: AsyncMock) -> None:
    original = make_member(True, datetime.now(UTC) + timedelta(seconds=20)).model_copy(
        update={"can_invite_users": False}
    )
    mock_bot.get_chat_member.side_effect = [original, make_member(False), make_member(False)]
    await execute_restriction(mock_bot, RestrictionAction.MUTE, CHAT_TID, USER_TID)
    mock_bot.restrict_chat_member.side_effect = [TelegramNetworkError(method="test", message="timeout"), True]
    await execute_restriction(mock_bot, RestrictionAction.UNMUTE, CHAT_TID, USER_TID)
    snapshot = await MutePermissionsModel.find_one({"chat_tid": CHAT_TID})
    assert snapshot is not None
    with patch("sophie_bot.modules.restrictions.utils.restrictions.datetime", wraps=datetime) as clock:
        clock.now.return_value = original.until_date
        await restore_expired_permissions(mock_bot)
    assert all(mock_bot.restrict_chat_member.await_args.kwargs["permissions"].model_dump().values())
    assert await MutePermissionsModel.find_one({"chat_tid": CHAT_TID}) is None


@pytest.mark.asyncio
async def test_welcome_replacement_respects_tightened_group_defaults(mock_bot: AsyncMock) -> None:
    mock_bot.get_chat_member.side_effect = [make_member(True), make_member(False)]
    await execute_restriction(mock_bot, RestrictionAction.MUTE, CHAT_TID, USER_TID)
    mock_bot.get_chat.return_value.permissions.can_send_messages = False
    result = await execute_restriction(mock_bot, RestrictionAction.RESTRICT, CHAT_TID, USER_TID)
    assert result.applied is True
    assert mock_bot.restrict_chat_member.await_args.kwargs["permissions"].can_send_messages is False


@pytest.mark.asyncio
async def test_successful_welcome_replacement_no_longer_owns_old_mute_state(mock_bot: AsyncMock) -> None:
    mock_bot.get_chat_member.side_effect = [make_member(True), make_member(False), make_member(False)]
    await execute_restriction(mock_bot, RestrictionAction.MUTE, CHAT_TID, USER_TID)
    await execute_restriction(mock_bot, RestrictionAction.RESTRICT, CHAT_TID, USER_TID)
    result = await execute_restriction(mock_bot, RestrictionAction.UNMUTE, CHAT_TID, USER_TID)
    assert result.applied is False
    assert mock_bot.restrict_chat_member.await_count == 2
