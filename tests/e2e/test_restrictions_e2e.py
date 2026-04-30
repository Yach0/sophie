"""End-to-end tests for the restrictions module (kick, ban, mute, unban, unmute)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory

from sophie_bot.utils.federation_ban_check import FederationBanInfo

# ---------------------------------------------------------------------------
# Shared patch targets
# Patches target where the function is *used* (the handler module), not where
# it is defined, since handlers use ``from ... import func`` style imports.
# ---------------------------------------------------------------------------
_ADMIN_PERMS_PATCH = "sophie_bot.filters.admin_rights.check_user_admin_permissions"

# kick handler patches
_KICK_IS_ADMIN_PATCH = "sophie_bot.modules.restrictions.handlers.kick.is_user_admin"
_KICK_USER_PATCH = "sophie_bot.modules.restrictions.handlers.kick.kick_user"
_KICK_AI_REASON_PATCH = "sophie_bot.modules.restrictions.handlers.kick.generate_restriction_reason"
_KICK_LOG_PATCH = "sophie_bot.modules.restrictions.handlers.kick.log_event"

# ban handler patches
_BAN_IS_ADMIN_PATCH = "sophie_bot.modules.restrictions.handlers.ban.is_user_admin"
_BAN_USER_PATCH = "sophie_bot.modules.restrictions.handlers.ban.ban_user"
_BAN_AI_REASON_PATCH = "sophie_bot.modules.restrictions.handlers.ban.generate_restriction_reason"
_BAN_FED_PATCH = "sophie_bot.modules.restrictions.handlers.ban.get_user_federation_ban_info"
_BAN_LOG_PATCH = "sophie_bot.modules.restrictions.handlers.ban.log_event"

# mute handler patches
_MUTE_IS_ADMIN_PATCH = "sophie_bot.modules.restrictions.handlers.mute.is_user_admin"
_MUTE_USER_PATCH = "sophie_bot.modules.restrictions.handlers.mute.mute_user"
_MUTE_AI_REASON_PATCH = "sophie_bot.modules.restrictions.handlers.mute.generate_restriction_reason"
_MUTE_LOG_PATCH = "sophie_bot.modules.restrictions.handlers.mute.log_event"

# unban handler patches
_UNBAN_USER_PATCH = "sophie_bot.modules.restrictions.handlers.unban.unban_user"
_UNBAN_FED_PATCH = "sophie_bot.modules.restrictions.handlers.unban.get_user_federation_ban_info"
_UNBAN_LOG_PATCH = "sophie_bot.modules.restrictions.handlers.unban.log_event"

# unmute handler patches
_UNMUTE_USER_PATCH = "sophie_bot.modules.restrictions.handlers.unmute.unmute_user"
_UNMUTE_LOG_PATCH = "sophie_bot.modules.restrictions.handlers.unmute.log_event"


# ---------------------------------------------------------------------------
# 1. /kick — non-admin gets permission error
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_kick_requires_admin_permissions(test_client: TestClient) -> None:
    """Non-admin user should be denied when trying to /kick."""
    group_chat = ChatFactory.create_group(chat_id=-1002700000001, title="Restrictions Perms Group")
    user_wrapper = test_client.create_user(user_id=927000001, first_name="RegularUser", username="regular_user_r")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group_chat)

    requests = await test_client.send_command(
        command="kick",
        from_user=user_wrapper.user,
        args="927000099",
        chat=group_chat,
    )

    assert requests, "Bot should respond when non-admin uses /kick."
    assert any("administrator" in (r.text or "").lower() for r in requests)


# ---------------------------------------------------------------------------
# 2. /kick — admin successfully kicks a user
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_kick_success(test_client: TestClient) -> None:
    """Admin kicks a user and gets 'User kicked' confirmation."""
    group_chat = ChatFactory.create_group(chat_id=-1002700000002, title="Kick Success Group")
    admin_wrapper = test_client.create_user(user_id=927000002, first_name="AdminKicker", username="admin_kicker")
    target_wrapper = test_client.create_user(user_id=927000003, first_name="KickTarget", username="kick_target")

    # Register both users in the database
    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group_chat)
    await test_client.send_message(text="init", from_user=target_wrapper.user, chat=group_chat)

    with (
        patch(_ADMIN_PERMS_PATCH, AsyncMock(return_value=True)),
        patch(_KICK_IS_ADMIN_PATCH, AsyncMock(return_value=False)),
        patch(_KICK_USER_PATCH, AsyncMock(return_value=True)),
        patch(_KICK_AI_REASON_PATCH, AsyncMock(return_value=None)),
        patch(_KICK_LOG_PATCH, AsyncMock()),
    ):
        requests = await test_client.send_command(
            command="kick",
            from_user=admin_wrapper.user,
            args=str(target_wrapper.user.id),
            chat=group_chat,
        )

    assert requests, "Bot should respond on successful kick."
    assert any("kicked" in (r.text or "").lower() for r in requests)


# ---------------------------------------------------------------------------
# 3. /kick — admin tries to kick themselves
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_kick_cannot_kick_self(test_client: TestClient) -> None:
    """Admin should not be able to kick themselves."""
    group_chat = ChatFactory.create_group(chat_id=-1002700000003, title="Kick Self Group")
    admin_wrapper = test_client.create_user(user_id=927000004, first_name="SelfKicker", username="self_kicker")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group_chat)

    with (
        patch(_ADMIN_PERMS_PATCH, AsyncMock(return_value=True)),
        patch(_KICK_IS_ADMIN_PATCH, AsyncMock(return_value=False)),
        patch(_KICK_USER_PATCH, AsyncMock(return_value=True)),
        patch(_KICK_AI_REASON_PATCH, AsyncMock(return_value=None)),
        patch(_KICK_LOG_PATCH, AsyncMock()),
    ):
        requests = await test_client.send_command(
            command="kick",
            from_user=admin_wrapper.user,
            args=str(admin_wrapper.user.id),
            chat=group_chat,
        )

    assert requests, "Bot should respond when admin tries to kick themselves."
    assert any("cannot kick yourself" in (r.text or "").lower() for r in requests)


# ---------------------------------------------------------------------------
# 4. /ban — admin bans a user successfully
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ban_success(test_client: TestClient) -> None:
    """Admin bans a user and gets 'User banned' confirmation."""
    group_chat = ChatFactory.create_group(chat_id=-1002700000004, title="Ban Success Group")
    admin_wrapper = test_client.create_user(user_id=927000005, first_name="AdminBanner", username="admin_banner")
    target_wrapper = test_client.create_user(user_id=927000006, first_name="BanTarget", username="ban_target")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group_chat)
    await test_client.send_message(text="init", from_user=target_wrapper.user, chat=group_chat)

    with (
        patch(_ADMIN_PERMS_PATCH, AsyncMock(return_value=True)),
        patch(_BAN_IS_ADMIN_PATCH, AsyncMock(return_value=False)),
        patch(_BAN_USER_PATCH, AsyncMock(return_value=True)),
        patch(_BAN_FED_PATCH, AsyncMock(return_value=None)),
        patch(_BAN_AI_REASON_PATCH, AsyncMock(return_value=None)),
        patch(_BAN_LOG_PATCH, AsyncMock()),
    ):
        requests = await test_client.send_command(
            command="ban",
            from_user=admin_wrapper.user,
            args=str(target_wrapper.user.id),
            chat=group_chat,
        )

    assert requests, "Bot should respond on successful ban."
    assert any("banned" in (r.text or "").lower() for r in requests)


# ---------------------------------------------------------------------------
# 5. /ban — admin tries to ban another admin
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ban_cannot_ban_admin(test_client: TestClient) -> None:
    """Admin should not be able to ban another admin."""
    group_chat = ChatFactory.create_group(chat_id=-1002700000005, title="Ban Admin Group")
    admin_wrapper = test_client.create_user(user_id=927000007, first_name="AdminBanner2", username="admin_banner2")
    target_admin_wrapper = test_client.create_user(user_id=927000008, first_name="TargetAdmin", username="target_admin")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group_chat)
    await test_client.send_message(text="init", from_user=target_admin_wrapper.user, chat=group_chat)

    with (
        patch(_ADMIN_PERMS_PATCH, AsyncMock(return_value=True)),
        patch(_BAN_IS_ADMIN_PATCH, AsyncMock(return_value=True)),
        patch(_BAN_USER_PATCH, AsyncMock(return_value=True)),
        patch(_BAN_FED_PATCH, AsyncMock(return_value=None)),
        patch(_BAN_AI_REASON_PATCH, AsyncMock(return_value=None)),
        patch(_BAN_LOG_PATCH, AsyncMock()),
    ):
        requests = await test_client.send_command(
            command="ban",
            from_user=admin_wrapper.user,
            args=str(target_admin_wrapper.user.id),
            chat=group_chat,
        )

    assert requests, "Bot should respond when trying to ban an admin."
    assert any("cannot ban an admin" in (r.text or "").lower() for r in requests)


# ---------------------------------------------------------------------------
# 6. /tban — admin temporarily bans with duration
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tban_success(test_client: TestClient) -> None:
    """Admin temp bans a user with a specified duration."""
    group_chat = ChatFactory.create_group(chat_id=-1002700000006, title="TBan Success Group")
    admin_wrapper = test_client.create_user(user_id=927000009, first_name="AdminTBanner", username="admin_tbanner")
    target_wrapper = test_client.create_user(user_id=927000010, first_name="TBanTarget", username="tban_target")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group_chat)
    await test_client.send_message(text="init", from_user=target_wrapper.user, chat=group_chat)

    with (
        patch(_ADMIN_PERMS_PATCH, AsyncMock(return_value=True)),
        patch(_BAN_IS_ADMIN_PATCH, AsyncMock(return_value=False)),
        patch(_BAN_USER_PATCH, AsyncMock(return_value=True)),
        patch(_BAN_FED_PATCH, AsyncMock(return_value=None)),
        patch(_BAN_AI_REASON_PATCH, AsyncMock(return_value=None)),
        patch(_BAN_LOG_PATCH, AsyncMock()),
    ):
        requests = await test_client.send_command(
            command="tban",
            from_user=admin_wrapper.user,
            args=f"{target_wrapper.user.id} 2h",
            chat=group_chat,
        )

    assert requests, "Bot should respond on successful temp ban."
    assert any("temporarily banned" in (r.text or "").lower() for r in requests)


# ---------------------------------------------------------------------------
# 7. /mute — admin mutes a user
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_mute_success(test_client: TestClient) -> None:
    """Admin mutes a user and gets 'User muted' confirmation."""
    group_chat = ChatFactory.create_group(chat_id=-1002700000007, title="Mute Success Group")
    admin_wrapper = test_client.create_user(user_id=927000011, first_name="AdminMuter", username="admin_muter")
    target_wrapper = test_client.create_user(user_id=927000012, first_name="MuteTarget", username="mute_target")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group_chat)
    await test_client.send_message(text="init", from_user=target_wrapper.user, chat=group_chat)

    with (
        patch(_ADMIN_PERMS_PATCH, AsyncMock(return_value=True)),
        patch(_MUTE_IS_ADMIN_PATCH, AsyncMock(return_value=False)),
        patch(_MUTE_USER_PATCH, AsyncMock(return_value=True)),
        patch(_MUTE_AI_REASON_PATCH, AsyncMock(return_value=None)),
        patch(_MUTE_LOG_PATCH, AsyncMock()),
    ):
        requests = await test_client.send_command(
            command="mute",
            from_user=admin_wrapper.user,
            args=str(target_wrapper.user.id),
            chat=group_chat,
        )

    assert requests, "Bot should respond on successful mute."
    assert any("muted" in (r.text or "").lower() for r in requests)


# ---------------------------------------------------------------------------
# 8. /tmute — admin temporarily mutes with duration
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tmute_success(test_client: TestClient) -> None:
    """Admin temp mutes a user with a specified duration."""
    group_chat = ChatFactory.create_group(chat_id=-1002700000008, title="TMute Success Group")
    admin_wrapper = test_client.create_user(user_id=927000013, first_name="AdminTMuter", username="admin_tmuter")
    target_wrapper = test_client.create_user(user_id=927000014, first_name="TMuteTarget", username="tmute_target")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group_chat)
    await test_client.send_message(text="init", from_user=target_wrapper.user, chat=group_chat)

    with (
        patch(_ADMIN_PERMS_PATCH, AsyncMock(return_value=True)),
        patch(_MUTE_IS_ADMIN_PATCH, AsyncMock(return_value=False)),
        patch(_MUTE_USER_PATCH, AsyncMock(return_value=True)),
        patch(_MUTE_AI_REASON_PATCH, AsyncMock(return_value=None)),
        patch(_MUTE_LOG_PATCH, AsyncMock()),
    ):
        requests = await test_client.send_command(
            command="tmute",
            from_user=admin_wrapper.user,
            args=f"{target_wrapper.user.id} 7d",
            chat=group_chat,
        )

    assert requests, "Bot should respond on successful temp mute."
    assert any("temporarily muted" in (r.text or "").lower() for r in requests)


# ---------------------------------------------------------------------------
# 9. /unban — admin unbans a user
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unban_success(test_client: TestClient) -> None:
    """Admin unbans a user and gets 'User unbanned' confirmation."""
    group_chat = ChatFactory.create_group(chat_id=-1002700000009, title="Unban Success Group")
    admin_wrapper = test_client.create_user(user_id=927000015, first_name="AdminUnbanner", username="admin_unbanner")
    target_wrapper = test_client.create_user(user_id=927000016, first_name="UnbanTarget", username="unban_target")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group_chat)
    await test_client.send_message(text="init", from_user=target_wrapper.user, chat=group_chat)

    with (
        patch(_ADMIN_PERMS_PATCH, AsyncMock(return_value=True)),
        patch(_UNBAN_USER_PATCH, AsyncMock(return_value=True)),
        patch(_UNBAN_FED_PATCH, AsyncMock(return_value=None)),
        patch(_UNBAN_LOG_PATCH, AsyncMock()),
    ):
        requests = await test_client.send_command(
            command="unban",
            from_user=admin_wrapper.user,
            args=str(target_wrapper.user.id),
            chat=group_chat,
        )

    assert requests, "Bot should respond on successful unban."
    assert any("unbanned" in (r.text or "").lower() for r in requests)


# ---------------------------------------------------------------------------
# 10. /unmute — admin unmutes a user
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unmute_success(test_client: TestClient) -> None:
    """Admin unmutes a user and gets 'User unmuted' confirmation."""
    group_chat = ChatFactory.create_group(chat_id=-1002700000010, title="Unmute Success Group")
    admin_wrapper = test_client.create_user(user_id=927000017, first_name="AdminUnmuter", username="admin_unmuter")
    target_wrapper = test_client.create_user(user_id=927000018, first_name="UnmuteTarget", username="unmute_target")

    await test_client.send_message(text="init", from_user=admin_wrapper.user, chat=group_chat)
    await test_client.send_message(text="init", from_user=target_wrapper.user, chat=group_chat)

    with (
        patch(_ADMIN_PERMS_PATCH, AsyncMock(return_value=True)),
        patch(_UNMUTE_USER_PATCH, AsyncMock(return_value=True)),
        patch(_UNMUTE_LOG_PATCH, AsyncMock()),
    ):
        requests = await test_client.send_command(
            command="unmute",
            from_user=admin_wrapper.user,
            args=str(target_wrapper.user.id),
            chat=group_chat,
        )

    assert requests, "Bot should respond on successful unmute."
    assert any("unmuted" in (r.text or "").lower() for r in requests)


# ---------------------------------------------------------------------------
# 11. /ban — shows federation notice when user is already fed-banned
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
        patch(_BAN_IS_ADMIN_PATCH, AsyncMock(return_value=False)),
        patch(_BAN_USER_PATCH, AsyncMock(return_value=True)),
        patch(_BAN_FED_PATCH, AsyncMock(return_value=fed_ban_info)),
        patch(_BAN_AI_REASON_PATCH, AsyncMock(return_value=None)),
        patch(_BAN_LOG_PATCH, AsyncMock()),
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
# 12. /kick — fails gracefully when kick_user returns False
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
        patch(_KICK_IS_ADMIN_PATCH, AsyncMock(return_value=False)),
        patch(_KICK_USER_PATCH, AsyncMock(return_value=False)),
        patch(_KICK_AI_REASON_PATCH, AsyncMock(return_value=None)),
        patch(_KICK_LOG_PATCH, AsyncMock()),
    ):
        requests = await test_client.send_command(
            command="kick",
            from_user=admin_wrapper.user,
            args=str(target_wrapper.user.id),
            chat=group_chat,
        )

    assert requests, "Bot should respond when kick fails."
    assert any("failed to kick" in (r.text or "").lower() for r in requests)
