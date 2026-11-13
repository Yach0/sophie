"""Warning expiration through the real dispatcher, permissions, flags and database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from aiogram.types import Chat, User
from aiogram_test_framework import TestClient
from aiogram_test_framework.types import RequestType

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.warns import WarnModel, WarnSettingsModel
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.feature_flags import set_chat_override, set_enabled
from tests.e2e.helpers import create_test_user_and_group, grant_admin, grant_bot_admin, next_user_id


async def _group(test_client: TestClient) -> tuple[User, Chat, ChatModel, ChatModel]:
    admin, group, _admin_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await grant_admin(group.id, admin.id)
    await grant_bot_admin(group.id)
    target = test_client.create_user(user_id=next_user_id(), first_name="Target")
    await test_client.send_message(text="init", from_user=target.user, chat=group)
    user = await ChatModel.get_by_tid(target.user.id)
    assert user is not None
    return admin, group, chat, user


async def _enable(chat: ChatModel, services: ApplicationServices) -> None:
    await set_chat_override("warn_expiration", chat.tid, True, redis=services.redis)


async def _settings(chat: ChatModel, *, seconds: int | None = 3600, limit: int = 3) -> None:
    settings = await WarnSettingsModel.get_or_create(chat.iid)
    settings.expiration_seconds = seconds
    settings.max_warns = limit
    await settings.save()


async def _warning(chat: ChatModel, user: ChatModel, reason: str, age: timedelta) -> WarnModel:
    warning = WarnModel(chat=chat, user=user, reason=reason, date=datetime.now(UTC) - age)
    await warning.insert()
    return warning


@pytest.mark.asyncio
async def test_configure_view_disable(test_client: TestClient, test_services: ApplicationServices) -> None:
    admin, group, chat, _user = await _group(test_client)
    await _enable(chat, test_services)
    requests = await test_client.send_command("warn_expiration", from_user=admin, chat=group)
    assert any("disabled" in (request.text or "") for request in requests)

    requests = await test_client.send_command("warn_expiration", args="1d12h", from_user=admin, chat=group)
    assert any("Each warning expires" in (request.text or "") for request in requests)
    assert (await WarnSettingsModel.get_or_create(chat.iid)).expiration_seconds == 129600
    requests = await test_client.send_command("warn_expiration", from_user=admin, chat=group)
    assert any("Each warning expires" in (request.text or "") for request in requests)
    assert (await WarnSettingsModel.get_or_create(chat.iid)).expiration_seconds == 129600

    requests = await test_client.send_command("disable_warn_expiration", from_user=admin, chat=group)
    assert any("disabled" in (request.text or "") for request in requests)
    assert (await WarnSettingsModel.get_or_create(chat.iid)).expiration_seconds is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command,args", [("warn_expiration", ""), ("warn_expiration", "2d"), ("disable_warn_expiration", "")]
)
async def test_commands_require_admin(
    test_client: TestClient, test_services: ApplicationServices, command: str, args: str
) -> None:
    _admin, group, chat, user = await _group(test_client)
    await _enable(chat, test_services)
    await _settings(chat)
    member = User(id=user.tid, first_name="Target", is_bot=False)
    requests = await test_client.send_command(command, args=args, from_user=member, chat=group)
    assert any("administrator" in (request.text or "").lower() for request in requests)
    assert (await WarnSettingsModel.get_or_create(chat.iid)).expiration_seconds == 3600


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command,args", [("warn_expiration", "2d"), ("warn_expiration", ""), ("disable_warn_expiration", "")]
)
async def test_commands_are_flagged_off_by_default(test_client: TestClient, command: str, args: str) -> None:
    admin, group, chat, _user = await _group(test_client)
    await _settings(chat)
    requests = await test_client.send_command(command, args=args, from_user=admin, chat=group)
    assert not any("Warning expiration" in (request.text or "") for request in requests)
    assert (await WarnSettingsModel.get_or_create(chat.iid)).expiration_seconds == 3600


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", ["0s", "59s", "3651d", "nonsense"])
async def test_invalid_duration_does_not_change_setting(
    test_client: TestClient, test_services: ApplicationServices, duration: str
) -> None:
    admin, group, chat, _user = await _group(test_client)
    await _enable(chat, test_services)
    await _settings(chat)
    requests = await test_client.send_command("warn_expiration", args=duration, from_user=admin, chat=group)
    assert requests
    assert (await WarnSettingsModel.get_or_create(chat.iid)).expiration_seconds == 3600


@pytest.mark.asyncio
async def test_each_warning_ages_independently_and_active_limit_still_punishes(
    test_client: TestClient, test_services: ApplicationServices
) -> None:
    admin, group, chat, user = await _group(test_client)
    await _enable(chat, test_services)
    await _settings(chat)
    old = await _warning(chat, user, "expired reason", timedelta(hours=2))
    recent = await _warning(chat, user, "recent reason", timedelta(minutes=10))

    requests = await test_client.send_command("warn", args=f"{user.tid} new reason", from_user=admin, chat=group)
    assert not any(request.request_type == RequestType.BAN_CHAT_MEMBER for request in requests)
    assert any("2/3" in (request.text or "") for request in requests)
    assert await WarnModel.get(old.id) is None
    stored_recent = await WarnModel.get(recent.id)
    assert stored_recent is not None
    assert stored_recent.date is not None and recent.date is not None
    assert stored_recent.date.replace(tzinfo=UTC) == recent.date.replace(
        microsecond=(recent.date.microsecond // 1000) * 1000
    )
    assert await WarnModel.count_user_warns(chat.iid, user.iid) == 2

    requests = await test_client.send_command("warns", args=str(user.tid), from_user=admin, chat=group)
    text = "\n".join(request.text or "" for request in requests)
    assert "recent reason" in text and "new reason" in text and "expired reason" not in text

    requests = await test_client.send_command("warn", args=str(user.tid), from_user=admin, chat=group)
    assert any(request.request_type == RequestType.BAN_CHAT_MEMBER for request in requests)
    assert await WarnModel.count_user_warns(chat.iid, user.iid) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("flag_on,seconds", [(False, 3600), (True, None)])
async def test_runtime_disabled_keeps_old_warnings_and_punishment(
    test_client: TestClient, test_services: ApplicationServices, flag_on: bool, seconds: int | None
) -> None:
    admin, group, chat, user = await _group(test_client)
    if flag_on:
        await _enable(chat, test_services)
    await _settings(chat, seconds=seconds, limit=2)
    await _warning(chat, user, "old active reason", timedelta(days=90))
    requests = await test_client.send_command("warns", args=str(user.tid), from_user=admin, chat=group)
    assert any("old active reason" in (request.text or "") for request in requests)
    requests = await test_client.send_command("warn", args=str(user.tid), from_user=admin, chat=group)
    assert any(request.request_type == RequestType.BAN_CHAT_MEMBER for request in requests)


@pytest.mark.asyncio
async def test_group_listing_expires_without_new_warning_and_preserves_legacy(
    test_client: TestClient, test_services: ApplicationServices
) -> None:
    admin, group, chat, user = await _group(test_client)
    await _enable(chat, test_services)
    await _settings(chat, limit=2)
    old = await _warning(chat, user, "expired reason", timedelta(hours=2))
    legacy = WarnModel(chat=chat, user=user, reason="legacy reason")
    await legacy.insert()
    await WarnModel.get_pymongo_collection().update_one(
        {"_id": legacy.id}, {"$unset": {"date": ""}, "$set": {"admin": {}}}
    )
    requests = await test_client.send_command("warns", args=str(user.tid), from_user=admin, chat=group)
    text = "\n".join(request.text or "" for request in requests)
    assert "expired reason" not in text and "legacy reason" in text
    assert await WarnModel.get(old.id) is None
    stored_legacy = await WarnModel.get(legacy.id)
    assert stored_legacy is not None and stored_legacy.date is None and stored_legacy.admin is None
    requests = await test_client.send_command("warn", args=str(user.tid), from_user=admin, chat=group)
    assert any(request.request_type == RequestType.BAN_CHAT_MEMBER for request in requests)


@pytest.mark.asyncio
async def test_pm_listing_resolves_each_chats_flag_and_setting(
    test_client: TestClient, test_services: ApplicationServices
) -> None:
    _admin, _chat, chat, user = await _group(test_client)
    _other_admin, _other_group, other_chat, _other_user = await _group(test_client)
    await set_enabled("warn_expiration", True, redis=test_services.redis)
    await set_chat_override("warn_expiration", other_chat.tid, False, redis=test_services.redis)
    await _settings(chat)
    await _settings(other_chat)
    await _warning(chat, user, "expired private reason", timedelta(hours=2))
    await _warning(chat, user, "fresh private reason", timedelta(minutes=5))
    await _warning(other_chat, user, "unflagged private reason", timedelta(hours=2))
    member = User(id=user.tid, first_name="Target", is_bot=False)
    requests = await test_client.send_command("warns", from_user=member)
    text = "\n".join(request.text or "" for request in requests)
    assert "expired private reason" not in text
    assert "fresh private reason" in text and "unflagged private reason" in text
    assert await WarnModel.count_user_warns(chat.iid, user.iid) == 1
    assert await WarnModel.count_user_warns(other_chat.iid, user.iid) == 1
