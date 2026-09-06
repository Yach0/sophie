from __future__ import annotations

from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory, MessageFactory
from aiogram_test_framework.types import CapturedRequest, RequestType
from redis.asyncio import Redis

from sophie_bot.db.models import ChatModel, GreetingsModel, WSUserModel
from sophie_bot.db.models.group_user_whitelist import GroupUserWhitelistModel
from sophie_bot.db.models.greetings import WelcomeSecurity
from sophie_bot.utils.group_whitelist import group_user_whitelist_cache_key, is_user_group_whitelisted
from tests.e2e.helpers import (
    create_test_user_and_group,
    grant_admin,
    grant_bot_admin,
    next_group_id,
    next_user_id,
    send_join_request,
    send_reply_command,
    set_feature,
)

def _redis(test_client: TestClient) -> Redis:
    return test_client.dispatcher.workflow_data["services"].redis


async def _setup(test_client: TestClient) -> tuple[object, object, object]:
    admin, group, _model = await create_test_user_and_group(
        test_client, first_name="Whitelist Admin", group_title="Whitelist Group"
    )
    await grant_admin(group.id, admin.id)
    target = test_client.create_user(user_id=next_user_id(), first_name="Target", username="whitelist_target")
    await test_client.send_message(text="register", from_user=target.user, chat=group)
    return admin, group, target.user


async def test_whitelist_and_unwhitelist_commands_update_only_current_group(test_client: TestClient) -> None:
    await set_feature(test_client, "group_user_whitelist", True)
    admin, group, target = await _setup(test_client)
    other_group = ChatFactory.create_group(chat_id=next_group_id(), title="Other Whitelist Group")
    await test_client.send_message(text="register", from_user=admin, chat=other_group)
    await grant_admin(other_group.id, admin.id)
    cache_key = group_user_whitelist_cache_key(group.id, target.id)

    assert await is_user_group_whitelisted(group.id, target.id, redis=_redis(test_client)) is False
    assert await _redis(test_client).get(cache_key) == b"0"

    added = await test_client.send_command(command="whitelist", from_user=admin, args=str(target.id), chat=group)
    assert any("now whitelisted in this group" in (request.text or "").lower() for request in added)
    assert await _redis(test_client).get(cache_key) is None
    assert await is_user_group_whitelisted(group.id, target.id, redis=_redis(test_client)) is True
    assert await is_user_group_whitelisted(other_group.id, target.id, redis=_redis(test_client)) is False

    already = await test_client.send_command(command="whitelist", from_user=admin, args=str(target.id), chat=group)
    assert any("already whitelisted in this group" in (request.text or "").lower() for request in already)

    missing = await test_client.send_command(
        command="unwhitelist", from_user=admin, args=str(target.id), chat=other_group
    )
    assert any("was not whitelisted in this group" in (request.text or "").lower() for request in missing)
    assert await is_user_group_whitelisted(group.id, target.id, redis=_redis(test_client)) is True

    removed = await test_client.send_command(command="unwhitelist", from_user=admin, args=str(target.id), chat=group)
    assert any("no longer whitelisted in this group" in (request.text or "").lower() for request in removed)
    assert await _redis(test_client).get(cache_key) is None
    assert await is_user_group_whitelisted(group.id, target.id, redis=_redis(test_client)) is False


async def test_whitelist_supports_reply_target(test_client: TestClient) -> None:
    await set_feature(test_client, "group_user_whitelist", True)
    admin, group, target = await _setup(test_client)
    replied = MessageFactory.create(text="hello", from_user=target, chat=group)

    await send_reply_command(test_client, command="whitelist", from_user=admin, group=group, replied=replied)

    assert await is_user_group_whitelisted(group.id, target.id, redis=_redis(test_client)) is True


async def test_trust_and_untrust_aliases_use_canonical_handlers(test_client: TestClient) -> None:
    await set_feature(test_client, "group_user_whitelist", True)
    admin, group, target = await _setup(test_client)

    trusted = await test_client.send_command(command="trust", from_user=admin, args=str(target.id), chat=group)
    assert any("now whitelisted in this group" in (request.text or "").lower() for request in trusted)
    assert await is_user_group_whitelisted(group.id, target.id, redis=_redis(test_client)) is True

    untrusted = await test_client.send_command(command="untrust", from_user=admin, args=str(target.id), chat=group)
    assert any("no longer whitelisted in this group" in (request.text or "").lower() for request in untrusted)
    assert await is_user_group_whitelisted(group.id, target.id, redis=_redis(test_client)) is False


async def test_whitelist_without_target_lists_only_current_group_users(test_client: TestClient) -> None:
    await set_feature(test_client, "group_user_whitelist", True)
    admin, group, target = await _setup(test_client)
    other_target = test_client.create_user(user_id=next_user_id(), first_name="Other Target").user
    await test_client.send_message(text="register", from_user=other_target, chat=group)
    await GroupUserWhitelistModel.add_user(group.id, target.id)
    await GroupUserWhitelistModel.add_user(next_group_id(), other_target.id)

    requests = await test_client.send_command(command="whitelist", from_user=admin, chat=group)

    text = "\n".join(request.text or "" for request in requests)
    assert "Users whitelisted in this group" in text
    assert "Target" in text
    assert "Other Target" not in text


async def test_whitelist_without_target_reports_empty_group_list(test_client: TestClient) -> None:
    await set_feature(test_client, "group_user_whitelist", True)
    admin, group, _target = await _setup(test_client)

    requests = await test_client.send_command(command="whitelist", from_user=admin, chat=group)

    assert any("no users are whitelisted in this group" in (request.text or "").lower() for request in requests)


async def test_whitelist_unmutes_and_clears_pending_captcha_user(test_client: TestClient) -> None:
    await set_feature(test_client, "group_user_whitelist", True)
    admin, group, target = await _setup(test_client)
    group_db = await ChatModel.get_by_tid(group.id)
    user_db = await ChatModel.get_by_tid(target.id)
    assert group_db is not None and user_db is not None
    await WSUserModel.ensure_user(user_db, group_db, is_join_request=False)

    requests = await test_client.send_command(command="whitelist", from_user=admin, args=str(target.id), chat=group)

    unmutes = [
        request
        for request in requests
        if request.request_type == RequestType.RESTRICT_CHAT_MEMBER
        and request.params.get("user_id") == target.id
        and request.params["permissions"]["can_send_messages"] is True
    ]
    assert unmutes
    assert await WSUserModel.is_user(user_db.iid, group_db.iid) is None


async def test_whitelist_requires_group_admin_permission(test_client: TestClient) -> None:
    await set_feature(test_client, "group_user_whitelist", True)
    regular, group, target = await _setup(test_client)
    await GroupUserWhitelistModel.delete_all()
    non_admin = test_client.create_user(user_id=next_user_id(), first_name="Regular").user
    await test_client.send_message(text="register", from_user=non_admin, chat=group)

    requests = await test_client.send_command(command="whitelist", from_user=non_admin, args=str(target.id), chat=group)

    assert regular.id != non_admin.id
    assert any("administrator" in (request.text or "").lower() for request in requests)
    assert await is_user_group_whitelisted(group.id, target.id, redis=_redis(test_client)) is False


async def test_whitelist_requires_restrict_members_permission(test_client: TestClient) -> None:
    await set_feature(test_client, "group_user_whitelist", True)
    admin, group, target = await _setup(test_client)
    await grant_admin(group.id, admin.id, can_restrict_members=False)

    requests = await test_client.send_command(command="whitelist", from_user=admin, args=str(target.id), chat=group)

    assert any("restrict members" in (request.text or "").lower() for request in requests)
    assert await is_user_group_whitelisted(group.id, target.id, redis=_redis(test_client)) is False


async def test_direct_admin_ban_still_applies_to_whitelisted_user(test_client: TestClient) -> None:
    await set_feature(test_client, "group_user_whitelist", True)
    admin, group, target = await _setup(test_client)
    await grant_bot_admin(group.id)
    await GroupUserWhitelistModel.add_user(group.id, target.id)

    requests = await test_client.send_command(command="ban", from_user=admin, args=str(target.id), chat=group)

    bans = [request for request in requests if request.request_type == RequestType.BAN_CHAT_MEMBER]
    assert bans and bans[0].params["user_id"] == target.id


async def test_whitelist_commands_are_silent_when_feature_flag_disabled(test_client: TestClient) -> None:
    await set_feature(test_client, "group_user_whitelist", False)
    admin, group, target = await _setup(test_client)

    whitelist_requests = await test_client.send_command(
        command="whitelist", from_user=admin, args=str(target.id), chat=group
    )
    assert not whitelist_requests
    assert (
        await GroupUserWhitelistModel.find_one(
            GroupUserWhitelistModel.chat_tid == group.id,
            GroupUserWhitelistModel.user_tid == target.id,
        )
        is None
    )

    await GroupUserWhitelistModel.add_user(group.id, target.id)
    unwhitelist_requests = await test_client.send_command(
        command="unwhitelist", from_user=admin, args=str(target.id), chat=group
    )
    assert not unwhitelist_requests
    assert (
        await GroupUserWhitelistModel.find_one(
            GroupUserWhitelistModel.chat_tid == group.id,
            GroupUserWhitelistModel.user_tid == target.id,
        )
        is not None
    )


def _join_request_approvals(requests: list[CapturedRequest]) -> list[CapturedRequest]:
    return [
        request
        for request in requests
        if request.request_type == RequestType.OTHER and "user_id" in request.params and "chat_id" in request.params
    ]


async def test_whitelisted_join_request_remains_pending_when_welcome_security_is_disabled(
    test_client: TestClient,
) -> None:
    await set_feature(test_client, "group_user_whitelist", True)
    _admin, group, _model = await create_test_user_and_group(test_client, group_title="Manual Approval Group")
    requester = test_client.create_user(user_id=next_user_id(), first_name="Manual Requester").user
    await GroupUserWhitelistModel.add_user(group.id, requester.id)

    requests = await send_join_request(test_client, group, requester)

    assert not _join_request_approvals(requests)


async def test_whitelisted_join_request_bypasses_enabled_welcome_security_captcha(test_client: TestClient) -> None:
    await set_feature(test_client, "group_user_whitelist", True)
    await set_feature(test_client, "welcomecaptcha", True)
    _admin, group, _model = await create_test_user_and_group(test_client, group_title="Captcha Approval Group")
    group_db = await ChatModel.get_by_tid(group.id)
    assert group_db is not None
    await GreetingsModel(chat=group_db.iid, welcome_security=WelcomeSecurity(enabled=True)).save()
    requester = test_client.create_user(user_id=next_user_id(), first_name="Allowed Requester").user
    await GroupUserWhitelistModel.add_user(group.id, requester.id)

    requests = await send_join_request(test_client, group, requester)

    approvals = _join_request_approvals(requests)
    assert len(approvals) == 1
    assert approvals[0].params["chat_id"] == group.id
    assert approvals[0].params["user_id"] == requester.id
