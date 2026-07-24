"""End-to-end tests for the restrictions module (kick, ban, mute, unban, unmute).

Admin rights are set up as real ChatAdminModel state and the outcome is asserted against
the Telegram calls the bot actually made, so nothing between the command and the API is
mocked away.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram_test_framework import TestClient
from aiogram_test_framework.types import RequestType

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.federations import Federation, FederationBan
from tests.e2e.helpers import create_test_user_and_group, grant_admin, grant_bot_admin, next_user_id

# What each command must end up asking Telegram to do.
_BAN = RequestType.BAN_CHAT_MEMBER
_UNBAN = RequestType.UNBAN_CHAT_MEMBER
_RESTRICT = RequestType.RESTRICT_CHAT_MEMBER


async def _setup_moderated_group(test_client: TestClient) -> tuple[object, object, int]:
    """A group where the acting user and the bot are both admins, plus a plain member."""
    admin_user, group, _admin_model = await create_test_user_and_group(
        test_client, first_name="Admin", group_title="Restrictions Group"
    )
    await grant_admin(group.id, admin_user.id)
    await grant_bot_admin(group.id)

    target_id = next_user_id()
    target_wrapper = test_client.create_user(user_id=target_id, first_name="Target", username=f"target_{target_id}")
    await test_client.send_message(text="init", from_user=target_wrapper.user, chat=group)

    return admin_user, group, target_id


@pytest.mark.parametrize("command", ["kick", "ban", "mute", "unban", "unmute"])
async def test_requires_admin_permissions(test_client: TestClient, command: str) -> None:
    """A user without admin rights is denied and no Telegram action is taken."""
    user, group, _model = await create_test_user_and_group(
        test_client, first_name="Regular", group_title="Restrictions Perms Group"
    )
    await grant_bot_admin(group.id)
    target_id = next_user_id()

    requests = await test_client.send_command(command=command, from_user=user, args=str(target_id), chat=group)

    assert requests, f"Bot should respond when non-admin uses /{command}."
    assert any("administrator" in (request.text or "").lower() for request in requests)
    assert not [r for r in requests if r.request_type in (_BAN, _UNBAN, _RESTRICT)]


@pytest.mark.parametrize(
    "command,keyword",
    [("kick", "kick"), ("ban", "ban"), ("mute", "mute"), ("unban", "unban"), ("unmute", "unmute")],
)
async def test_cannot_target_self(test_client: TestClient, command: str, keyword: str) -> None:
    admin_user, group, _target_id = await _setup_moderated_group(test_client)

    requests = await test_client.send_command(
        command=command, from_user=admin_user, args=str(admin_user.id), chat=group
    )

    assert any(f"cannot {keyword} yourself" in (request.text or "").lower() for request in requests)
    assert not [r for r in requests if r.request_type in (_BAN, _UNBAN, _RESTRICT)]


@pytest.mark.parametrize("command,keyword", [("kick", "kick"), ("ban", "ban"), ("mute", "mute")])
async def test_cannot_target_admin(test_client: TestClient, command: str, keyword: str) -> None:
    admin_user, group, target_id = await _setup_moderated_group(test_client)
    await grant_admin(group.id, target_id)

    requests = await test_client.send_command(command=command, from_user=admin_user, args=str(target_id), chat=group)

    assert any(f"cannot {keyword} an admin" in (request.text or "").lower() for request in requests)
    assert not [r for r in requests if r.request_type in (_BAN, _UNBAN, _RESTRICT)]


@pytest.mark.parametrize(
    "command,duration,expected_call,keyword",
    [
        pytest.param("kick", None, _UNBAN, "kicked", id="kick"),
        pytest.param("ban", None, _BAN, "banned", id="ban"),
        pytest.param("tban", "2h", _BAN, "temporarily banned", id="tban"),
        pytest.param("mute", None, _RESTRICT, "muted", id="mute"),
        pytest.param("tmute", "7d", _RESTRICT, "temporarily muted", id="tmute"),
        pytest.param("unban", None, _UNBAN, "unbanned", id="unban"),
        pytest.param("unmute", None, _RESTRICT, "unmuted", id="unmute"),
    ],
)
async def test_restriction_calls_telegram_and_confirms(
    test_client: TestClient,
    command: str,
    duration: str | None,
    expected_call: RequestType,
    keyword: str,
) -> None:
    admin_user, group, target_id = await _setup_moderated_group(test_client)
    args = f"{target_id} {duration}" if duration else str(target_id)

    requests = await test_client.send_command(command=command, from_user=admin_user, args=args, chat=group)

    performed = [request for request in requests if request.request_type == expected_call]
    assert performed, f"/{command} should call {expected_call.value}, got {[r.request_type for r in requests]}"
    assert performed[0].params["chat_id"] == group.id
    assert performed[0].params["user_id"] == target_id
    assert bool(performed[0].params.get("until_date")) is bool(duration)
    assert any(keyword in (request.text or "").lower() for request in requests)


async def test_ban_shows_federation_notice(test_client: TestClient) -> None:
    """A ban on someone already fed-banned mentions the federation."""
    admin_user, group, target_id = await _setup_moderated_group(test_client)
    chat_model = await ChatModel.get_by_tid(group.id)
    admin_model = await ChatModel.get_by_tid(admin_user.id)
    assert chat_model is not None and admin_model is not None

    federation = Federation(
        fed_id="fed-antispam-001",
        fed_name="Anti-Spam Federation",
        creator=admin_model,
        chats=[chat_model],
    )
    await federation.insert()
    await FederationBan(
        fed_id=federation.fed_id,
        user_id=target_id,
        time=datetime.now(UTC),
        by=admin_model,
    ).insert()

    requests = await test_client.send_command(command="ban", from_user=admin_user, args=str(target_id), chat=group)

    response = " ".join(request.text or "" for request in requests)
    assert "banned" in response.lower()
    assert "Anti-Spam Federation" in response


async def test_kick_reports_failure_when_telegram_rejects_it(
    test_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin_user, group, target_id = await _setup_moderated_group(test_client)

    async def _reject(*args: object, **kwargs: object) -> bool:
        raise TelegramBadRequest(method=None, message="not enough rights")  # type: ignore[arg-type]

    monkeypatch.setattr(test_client.bot, "unban_chat_member", _reject)

    requests = await test_client.send_command(command="kick", from_user=admin_user, args=str(target_id), chat=group)

    assert any("failed to kick" in (request.text or "").lower() for request in requests)
