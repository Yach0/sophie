"""End-to-end tests for the restrictions module (kick, ban, mute, unban, unmute)."""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory

from sophie_bot.utils.federation_ban_check import FederationBanInfo

# ---------------------------------------------------------------------------
# Shared patch targets
# ---------------------------------------------------------------------------
_ADMIN_PERMS_PATCH = "sophie_bot.filters.admin_rights.check_user_admin_permissions"
_IS_ADMIN_PATCH = "sophie_bot.modules.restrictions.handlers.base.is_user_admin"
_AI_REASON_PATCH = "sophie_bot.modules.restrictions.handlers.base.generate_restriction_reason"
_LOG_PATCH = "sophie_bot.modules.restrictions.handlers.base.log_event"
_FED_PATCH = "sophie_bot.modules.restrictions.handlers.base.get_user_federation_ban_info"

_ACTION_PATCHES: dict[str, str] = {
    "kick": "sophie_bot.modules.restrictions.handlers.kick._kick_action",
    "ban": "sophie_bot.modules.restrictions.handlers.ban.ban_user",
    "mute": "sophie_bot.modules.restrictions.handlers.mute.mute_user",
    "unban": "sophie_bot.modules.restrictions.handlers.unban._unban_action",
    "unmute": "sophie_bot.modules.restrictions.handlers.unmute._unmute_action",
}

# Commands that require is_admin + action + ai_reason + log patches
_STANDARD_CMDS = ("kick", "ban", "mute")

# Commands that skip is_admin and ai_reason checks, use fed_patch instead
_UNBAN_CMDS = ("unban",)

# Commands that skip is_admin, ai_reason, and fed_patch
_UNMUTE_CMDS = ("unmute",)


def _apply_patches(
    action_patch: str,
    *,
    with_is_admin: bool = True,
    is_admin_return: bool = False,
    with_fed_patch: bool = False,
) -> ExitStack:
    """Stack the standard mocks for a restriction command."""
    stack = ExitStack()
    stack.enter_context(patch(_ADMIN_PERMS_PATCH, AsyncMock(return_value=True)))
    stack.enter_context(patch(_AI_REASON_PATCH, AsyncMock(return_value=None)))
    stack.enter_context(patch(_LOG_PATCH, AsyncMock()))
    stack.enter_context(patch(action_patch, AsyncMock(return_value=True)))
    if with_is_admin:
        stack.enter_context(patch(_IS_ADMIN_PATCH, AsyncMock(return_value=is_admin_return)))
    if with_fed_patch:
        stack.enter_context(patch(_FED_PATCH, AsyncMock(return_value=None)))
    return stack


# ---------------------------------------------------------------------------
# 1. Non-admin gets permission error (parametrized across all commands)
# ---------------------------------------------------------------------------
_PERMISSION_CASES = [
    pytest.param("kick", "927000099", id="kick"),
    pytest.param("ban", "927000099", id="ban"),
    pytest.param("mute", "927000099", id="mute"),
    pytest.param("unban", "927000099", id="unban"),
    pytest.param("unmute", "927000099", id="unmute"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("command,target_args", _PERMISSION_CASES)
async def test_requires_admin_permissions(
    test_client: TestClient,
    command: str,
    target_args: str,
) -> None:
    """Non-admin user should be denied when trying to use a restriction command."""
    group_chat = ChatFactory.create_group(chat_id=-1002700000001, title="Restrictions Perms Group")
    user_wrapper = test_client.create_user(user_id=927000001, first_name="RegularUser", username="regular_user_r")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group_chat)

    requests = await test_client.send_command(
        command=command,
        from_user=user_wrapper.user,
        args=target_args,
        chat=group_chat,
    )

    assert requests, f"Bot should respond when non-admin uses /{command}."
    assert any("administrator" in (r.text or "").lower() for r in requests)


# ---------------------------------------------------------------------------
# 2. Admin cannot target themselves (parametrized)
# ---------------------------------------------------------------------------
_SELF_CASES = [
    pytest.param("kick", "kick", id="kick"),
    pytest.param("ban", "ban", id="ban"),
    pytest.param("mute", "mute", id="mute"),
    pytest.param("unban", "unban", id="unban"),
    pytest.param("unmute", "unmute", id="unmute"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("command,keyword", _SELF_CASES)
async def test_cannot_target_self(
    test_client: TestClient,
    command: str,
    keyword: str,
) -> None:
    """Admin should not be able to restrict themselves."""
    group_chat = ChatFactory.create_group(chat_id=-1002700000003, title=f"Self-{command} Group")
    admin_wrapper = test_client.create_user(user_id=927000004, first_name="SelfAction", username="self_action")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group_chat)

    with _apply_patches(
        _ACTION_PATCHES[command],
        with_is_admin=command in (*_STANDARD_CMDS,),
        with_fed_patch=command in _UNBAN_CMDS,
    ):
        requests = await test_client.send_command(
            command=command,
            from_user=admin_wrapper.user,
            args=str(admin_wrapper.user.id),
            chat=group_chat,
        )

    assert requests, f"Bot should respond when admin tries to /{command} themselves."
    assert any(f"cannot {keyword} yourself" in (r.text or "").lower() for r in requests)


# ---------------------------------------------------------------------------
# 3. Admin cannot target another admin (parametrized for commands that check)
# ---------------------------------------------------------------------------
_ADMIN_TARGET_CASES = [
    pytest.param("kick", "kick", id="kick"),
    pytest.param("ban", "ban", id="ban"),
    pytest.param("mute", "mute", id="mute"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("command,keyword", _ADMIN_TARGET_CASES)
async def test_cannot_target_admin(
    test_client: TestClient,
    command: str,
    keyword: str,
) -> None:
    """Admin should not be able to restrict another admin."""
    group_chat = ChatFactory.create_group(chat_id=-1002700000005, title=f"Admin-{command} Group")
    admin_wrapper = test_client.create_user(user_id=927000007, first_name="AdminUser", username="admin_user")
    target_admin_wrapper = test_client.create_user(user_id=927000008, first_name="TargetAdmin", username="target_admin")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group_chat)
    await test_client.send_message(text="init", from_user=target_admin_wrapper.user, chat=group_chat)

    with _apply_patches(_ACTION_PATCHES[command], with_is_admin=True, is_admin_return=True):
        requests = await test_client.send_command(
            command=command,
            from_user=admin_wrapper.user,
            args=str(target_admin_wrapper.user.id),
            chat=group_chat,
        )

    assert requests, f"Bot should respond when trying to /{command} an admin."
    assert any(f"cannot {keyword} an admin" in (r.text or "").lower() for r in requests)


# ---------------------------------------------------------------------------
# 4. Successful restriction actions (parametrized)
# ---------------------------------------------------------------------------
_SUCCESS_CASES = [
    pytest.param("kick", "kick", "927000003", "kicked", None, id="kick"),
    pytest.param("ban", "ban", "927000006", "banned", None, id="ban"),
    pytest.param("tban", "ban", "927000010", "temporarily banned", "2h", id="tban"),
    pytest.param("mute", "mute", "927000012", "muted", None, id="mute"),
    pytest.param("tmute", "mute", "927000014", "temporarily muted", "7d", id="tmute"),
    pytest.param("unban", "unban", "927000016", "unbanned", None, id="unban"),
    pytest.param("unmute", "unmute", "927000018", "unmuted", None, id="unmute"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("command,action_key,target_id,keyword,duration", _SUCCESS_CASES)
async def test_restriction_success(
    test_client: TestClient,
    command: str,
    action_key: str,
    target_id: str,
    keyword: str,
    duration: str | None,
) -> None:
    """Admin successfully runs a restriction command and gets confirmation."""
    chat_id_offset = {
        "kick": 2,
        "ban": 4,
        "tban": 6,
        "mute": 7,
        "tmute": 8,
        "unban": 9,
        "unmute": 10,
    }[command]
    chat_id = -1002700000000 - chat_id_offset
    group_chat = ChatFactory.create_group(chat_id=chat_id, title=f"{command} Success Group")

    admin_wrapper = test_client.create_user(
        user_id=927000000 + chat_id_offset * 2,
        first_name=f"Admin{command}",
        username=f"admin_{command}",
    )
    target_wrapper = test_client.create_user(
        user_id=927000000 + chat_id_offset * 2 + 1,
        first_name=f"Target{command}",
        username=f"target_{command}",
    )

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group_chat)
    await test_client.send_message(text="init", from_user=target_wrapper.user, chat=group_chat)

    args = f"{target_wrapper.user.id} {duration}" if duration else str(target_wrapper.user.id)

    with _apply_patches(
        _ACTION_PATCHES[action_key],
        with_is_admin=action_key in _STANDARD_CMDS,
        with_fed_patch=action_key in _UNBAN_CMDS,
    ):
        requests = await test_client.send_command(
            command=command,
            from_user=admin_wrapper.user,
            args=args,
            chat=group_chat,
        )

    assert requests, f"Bot should respond on successful /{command}."
    assert any(keyword in (r.text or "").lower() for r in requests)


# ---------------------------------------------------------------------------
# 5. Ban shows federation notice when user is already fed-banned
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ban_shows_federation_notice(test_client: TestClient) -> None:
    """Ban should include a federation notice when the user is already fed-banned."""
    group_chat = ChatFactory.create_group(chat_id=-1002700000011, title="Ban Fed Notice Group")
    admin_wrapper = test_client.create_user(user_id=927000019, first_name="AdminFedBan", username="admin_fed_ban")
    target_wrapper = test_client.create_user(user_id=927000020, first_name="FedBanTarget", username="fed_ban_target")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group_chat)
    await test_client.send_message(text="init", from_user=target_wrapper.user, chat=group_chat)

    fed_ban_info = FederationBanInfo(scope="current", fed_name="Anti-Spam Federation", fed_id="fed-antispam-001")

    with (
        patch(_ADMIN_PERMS_PATCH, AsyncMock(return_value=True)),
        patch(_IS_ADMIN_PATCH, AsyncMock(return_value=False)),
        patch(_ACTION_PATCHES["ban"], AsyncMock(return_value=True)),
        patch(_FED_PATCH, AsyncMock(return_value=fed_ban_info)),
        patch(_AI_REASON_PATCH, AsyncMock(return_value=None)),
        patch(_LOG_PATCH, AsyncMock()),
    ):
        requests = await test_client.send_command(
            command="ban",
            from_user=admin_wrapper.user,
            args=str(target_wrapper.user.id),
            chat=group_chat,
        )

    assert requests, "Bot should respond on ban with federation notice."
    response_texts = " ".join(r.text or "" for r in requests)
    assert "banned" in response_texts.lower(), "Response should confirm the ban."
    assert "Anti-Spam Federation" in response_texts, "Response should include federation name."


# ---------------------------------------------------------------------------
# 6. Kick fails gracefully when action returns False
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_kick_fails_gracefully(test_client: TestClient) -> None:
    """When kick_user returns False, the bot should report a failure message."""
    group_chat = ChatFactory.create_group(chat_id=-1002700000012, title="Kick Fail Group")
    admin_wrapper = test_client.create_user(user_id=927000021, first_name="AdminKickFail", username="admin_kick_fail")
    target_wrapper = test_client.create_user(
        user_id=927000022, first_name="KickFailTarget", username="kick_fail_target"
    )

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group_chat)
    await test_client.send_message(text="init", from_user=target_wrapper.user, chat=group_chat)

    with (
        patch(_ADMIN_PERMS_PATCH, AsyncMock(return_value=True)),
        patch(_IS_ADMIN_PATCH, AsyncMock(return_value=False)),
        patch(_ACTION_PATCHES["kick"], AsyncMock(return_value=False)),
        patch(_AI_REASON_PATCH, AsyncMock(return_value=None)),
        patch(_LOG_PATCH, AsyncMock()),
    ):
        requests = await test_client.send_command(
            command="kick",
            from_user=admin_wrapper.user,
            args=str(target_wrapper.user.id),
            chat=group_chat,
        )

    assert requests, "Bot should respond when kick fails."
    assert any("failed to kick" in (r.text or "").lower() for r in requests)
