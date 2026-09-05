from __future__ import annotations

from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import MessageFactory
from aiogram_test_framework.types import RequestType

from sophie_bot.db.models.global_user_whitelist import GlobalUserWhitelistModel
from sophie_bot.utils.global_whitelist import is_user_globally_whitelisted
from tests.e2e.helpers import (
    create_test_user_and_group,
    grant_admin,
    grant_bot_admin,
    next_user_id,
    send_reply_command,
)


async def _setup(test_client: TestClient) -> tuple[object, object, object]:
    admin, group, _model = await create_test_user_and_group(
        test_client, first_name="Whitelist Admin", group_title="Whitelist Group"
    )
    await grant_admin(group.id, admin.id)
    target = test_client.create_user(user_id=next_user_id(), first_name="Target", username="whitelist_target")
    await test_client.send_message(text="register", from_user=target.user, chat=group)
    return admin, group, target.user


async def test_whitelist_and_unwhitelist_commands_update_global_state(test_client: TestClient) -> None:
    admin, group, target = await _setup(test_client)

    added = await test_client.send_command(command="whitelist", from_user=admin, args=str(target.id), chat=group)
    assert any("now globally whitelisted" in (request.text or "").lower() for request in added)
    assert await is_user_globally_whitelisted(target.id) is True

    already = await test_client.send_command(command="whitelist", from_user=admin, args=str(target.id), chat=group)
    assert any("already globally whitelisted" in (request.text or "").lower() for request in already)

    removed = await test_client.send_command(command="unwhitelist", from_user=admin, args=str(target.id), chat=group)
    assert any("no longer globally whitelisted" in (request.text or "").lower() for request in removed)
    assert await is_user_globally_whitelisted(target.id) is False

    missing = await test_client.send_command(command="unwhitelist", from_user=admin, args=str(target.id), chat=group)
    assert any("was not globally whitelisted" in (request.text or "").lower() for request in missing)


async def test_whitelist_supports_reply_target(test_client: TestClient) -> None:
    admin, group, target = await _setup(test_client)
    replied = MessageFactory.create(text="hello", from_user=target, chat=group)

    await send_reply_command(test_client, command="whitelist", from_user=admin, group=group, replied=replied)

    assert await is_user_globally_whitelisted(target.id) is True


async def test_whitelist_requires_group_admin_permission(test_client: TestClient) -> None:
    regular, group, target = await _setup(test_client)
    await GlobalUserWhitelistModel.delete_all()
    non_admin = test_client.create_user(user_id=next_user_id(), first_name="Regular").user
    await test_client.send_message(text="register", from_user=non_admin, chat=group)

    requests = await test_client.send_command(
        command="whitelist", from_user=non_admin, args=str(target.id), chat=group
    )

    assert regular.id != non_admin.id
    assert any("administrator" in (request.text or "").lower() for request in requests)
    assert await is_user_globally_whitelisted(target.id) is False


async def test_direct_admin_ban_still_applies_to_whitelisted_user(test_client: TestClient) -> None:
    admin, group, target = await _setup(test_client)
    await grant_bot_admin(group.id)
    await GlobalUserWhitelistModel.add_user(target.id)

    requests = await test_client.send_command(command="ban", from_user=admin, args=str(target.id), chat=group)

    bans = [request for request in requests if request.request_type == RequestType.BAN_CHAT_MEMBER]
    assert bans and bans[0].params["user_id"] == target.id
