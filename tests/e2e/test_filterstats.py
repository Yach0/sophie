from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.enums import ContentType
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import BanChatMember, DeleteMessage, SendMessage
from aiogram.types import Chat, User
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import MessageFactory
from redis.exceptions import ConnectionError as RedisConnectionError

from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.filters import FiltersModel
from sophie_bot.db.models.notes import NoteFile, NoteModel, Saveable
from sophie_bot.db.models.rules import RulesModel
from sophie_bot.modules.filters.utils_.stats import get_filter_stats, record_filter_action
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.feature_flags import get_default_value, set_chat_override
from tests.e2e.helpers import create_test_user_and_group, grant_admin, set_feature


async def _create_group(test_client: TestClient) -> tuple[User, Chat, ChatModel]:
    user, group, _actor = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    return user, group, chat


async def test_filterstats_counts_completed_actions_and_isolates_chats(
    test_client: TestClient, test_services: ApplicationServices
) -> None:
    await set_feature(test_client, "filter_stats", True)
    user, group, chat = await _create_group(test_client)
    other_user, other_group, other_chat = await _create_group(test_client)
    first = await FiltersModel(
        chat=chat,
        handler="spam",
        action=None,
        actions={"warn_user": {"reason": "spam"}, "delmsg": None, "reply": {"text": "Stop"}},
    ).insert()
    second = await FiltersModel(
        chat=chat, handler="word:spam", action=None, actions={"reply": {"text": "duplicate"}}
    ).insert()
    await FiltersModel(chat=other_chat, handler="spam", action=None, actions={"reply": {"text": "Other"}}).insert()
    await test_client.send_message(text="hello", from_user=user, chat=group)
    assert await get_filter_stats(chat.iid, redis=test_services.redis) is None
    await test_client.send_message(text="spam", from_user=user, chat=group)
    stats = await get_filter_stats(chat.iid, redis=test_services.redis)
    assert stats is not None
    assert stats.total == 3
    assert stats.counts == {str(first.id): 3}
    assert str(second.id) not in stats.counts
    assert await get_filter_stats(other_chat.iid, redis=test_services.redis) is None
    await test_client.send_message(text="spam", from_user=other_user, chat=other_group)
    assert (await get_filter_stats(other_chat.iid, redis=test_services.redis)).total == 1
    assert (await get_filter_stats(chat.iid, redis=test_services.redis)).total == 3
    first.handler = "renamed <spam>"
    await first.save()
    await test_client.send_message(text="renamed <spam>", from_user=user, chat=group)
    assert (await get_filter_stats(chat.iid, redis=test_services.redis)).counts[str(first.id)] == 6


async def test_filterstats_failures_and_exemptions_do_not_count(
    test_client: TestClient, test_services: ApplicationServices
) -> None:
    await set_feature(test_client, "filter_stats", True)
    user, group, chat = await _create_group(test_client)
    filter_item = await FiltersModel(chat=chat, handler="spam", action=None, actions={"delmsg": None}).insert()
    error = TelegramForbiddenError(method=DeleteMessage(chat_id=group.id, message_id=1), message="Forbidden")
    with patch("aiogram.types.Message.delete", AsyncMock(side_effect=error)):
        await test_client.send_message(text="spam", from_user=user, chat=group)
    assert await get_filter_stats(chat.iid, redis=test_services.redis) is None
    await grant_admin(group.id, user.id)
    await test_client.send_message(text="spam", from_user=user, chat=group)
    assert await get_filter_stats(chat.iid, redis=test_services.redis) is None
    filter_item.actions = {"send_note": {"notename": "missing"}, "send_rules": None}
    await filter_item.save()
    await test_client.send_message(text="spam", from_user=user, chat=group)
    assert await get_filter_stats(chat.iid, redis=test_services.redis) is None


async def test_filterstats_reply_delivery_failure_does_not_count(
    test_client: TestClient, test_services: ApplicationServices
) -> None:
    await set_feature(test_client, "filter_stats", True)
    user, group, chat = await _create_group(test_client)
    await FiltersModel(chat=chat, handler="spam", action=None, actions={"reply": {"text": "Stop"}}).insert()
    error = TelegramForbiddenError(method=SendMessage(chat_id=group.id, text="Stop"), message="Forbidden")
    with patch("aiogram.types.Message.reply", AsyncMock(side_effect=error)):
        await test_client.send_message(text="spam", from_user=user, chat=group)
    assert await get_filter_stats(chat.iid, redis=test_services.redis) is None


async def test_filterstats_admin_report_and_feature_gates(
    test_client: TestClient, test_services: ApplicationServices
) -> None:
    assert get_default_value("filter_stats") is False
    user, group, chat = await _create_group(test_client)
    filter_item = await FiltersModel(
        chat=chat, handler="spam <tag>", action=None, actions={"reply": {"text": "Stop"}}
    ).insert()
    await test_client.send_message(text="spam <tag>", from_user=user, chat=group)
    assert await get_filter_stats(chat.iid, redis=test_services.redis) is None
    assert not await test_client.send_command(command="filterstats", from_user=user, chat=group)
    await set_feature(test_client, "filter_stats", True)
    denied = await test_client.send_command(command="filterstats", from_user=user, chat=group)
    assert any("must be an administrator" in str(request.params) for request in denied)
    assert not any("Total successful" in str(request.params) for request in denied)
    await grant_admin(group.id, user.id)
    empty = await test_client.send_command(command="filterstats", from_user=user, chat=group)
    assert any("Total successful action executions: 0" in str(request.params) for request in empty)
    await test_client.send_message(text="spam <tag>", from_user=user, chat=group)
    report = await test_client.send_command(command="filterstats", from_user=user, chat=group)
    rendered = str([request.params for request in report])
    assert "Total successful action executions: 1" in rendered
    assert "spam &lt;tag&gt;" in rendered
    assert str(filter_item.id) in rendered
    await filter_item.delete()
    report = await test_client.send_command(command="filterstats", from_user=user, chat=group)
    assert any(str(filter_item.id) in str(request.params) for request in report)
    services = test_client.dispatcher.workflow_data["services"]
    await set_chat_override("filter_stats", group.id, False, redis=services.redis)
    assert not await test_client.send_command(command="filterstats", from_user=user, chat=group)
    await set_chat_override("filter_stats", group.id, True, redis=services.redis)
    await set_feature(test_client, "filters", False)
    assert not await test_client.send_command(command="filterstats", from_user=user, chat=group)


async def test_filterstats_atomic_increments_retain_deleted_filter_counts(
    test_client: TestClient, test_services: ApplicationServices
) -> None:
    _user, _group, chat = await _create_group(test_client)
    first = await FiltersModel(chat=chat, handler="first", action=None).insert()
    second = await FiltersModel(chat=chat, handler="second", action=None).insert()
    await asyncio.gather(*(record_filter_action(chat.iid, first, redis=test_services.redis) for _index in range(20)))
    await asyncio.gather(*(record_filter_action(chat.iid, second, redis=test_services.redis) for _index in range(10)))
    await first.delete()
    stats = await get_filter_stats(chat.iid, redis=test_services.redis)
    assert stats is not None
    assert stats.total == 30
    assert stats.counts == {str(first.id): 20, str(second.id): 10}


async def test_filterstats_chat_flag_stops_counting_without_backfill(
    test_client: TestClient, test_services: ApplicationServices
) -> None:
    await set_feature(test_client, "filter_stats", True)
    user, group, chat = await _create_group(test_client)
    await FiltersModel(chat=chat, handler="spam", action=None, actions={"reply": {"text": "Stop"}}).insert()
    await test_client.send_message(text="spam", from_user=user, chat=group)
    await set_chat_override("filter_stats", group.id, False, redis=test_services.redis)
    requests = await test_client.send_message(text="spam", from_user=user, chat=group)
    assert any("Stop" in str(request.params) for request in requests)
    assert (await get_filter_stats(chat.iid, redis=test_services.redis)).total == 1
    await set_chat_override("filter_stats", group.id, True, redis=test_services.redis)
    await test_client.send_message(text="spam", from_user=user, chat=group)
    assert (await get_filter_stats(chat.iid, redis=test_services.redis)).total == 2
    await set_feature(test_client, "filters", False)
    assert not await test_client.send_message(text="spam", from_user=user, chat=group)
    assert (await get_filter_stats(chat.iid, redis=test_services.redis)).total == 2


@pytest.mark.parametrize("action_name", ["ban_user", "mute_user", "kick_user"])
async def test_filterstats_counts_successful_restrictions_even_when_report_delivery_fails(
    test_client: TestClient, test_services: ApplicationServices, action_name: str
) -> None:
    await set_feature(test_client, "filter_stats", True)
    user, group, chat = await _create_group(test_client)
    await FiltersModel(chat=chat, handler="spam", action=None, actions={action_name: None}).insert()
    error = TelegramForbiddenError(method=SendMessage(chat_id=group.id, text="Report"), message="Forbidden")
    with patch("aiogram.types.Message.reply", AsyncMock(side_effect=error)):
        await test_client.send_message(text="spam", from_user=user, chat=group)
    assert (await get_filter_stats(chat.iid, redis=test_services.redis)).total == 1


async def test_filterstats_failed_restriction_does_not_count(
    test_client: TestClient, test_services: ApplicationServices
) -> None:
    await set_feature(test_client, "filter_stats", True)
    user, group, chat = await _create_group(test_client)
    await FiltersModel(chat=chat, handler="spam", action=None, actions={"ban_user": None}).insert()
    error = TelegramForbiddenError(method=BanChatMember(chat_id=group.id, user_id=user.id), message="Forbidden")
    with patch.object(test_services.bot, "ban_chat_member", AsyncMock(side_effect=error)) as ban:
        await test_client.send_message(text="spam", from_user=user, chat=group)
    ban.assert_awaited_once()
    assert await get_filter_stats(chat.iid, redis=test_services.redis) is None


@pytest.mark.parametrize("action_name", ["reply", "send_note", "send_rules"])
async def test_filterstats_counts_delivered_saveables_once_and_excludes_partial_delivery(
    test_client: TestClient, test_services: ApplicationServices, action_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await set_feature(test_client, "filter_stats", True)
    user, group, chat = await _create_group(test_client)
    # The upstream Telegram mock returns True for albums; Telegram returns Message[].
    monkeypatch.setattr(
        "aiogram.methods.SendMediaGroup.emit",
        AsyncMock(
            return_value=[
                MessageFactory.create(text="", from_user=user, chat=group),
                MessageFactory.create(text="", from_user=user, chat=group),
            ]
        ),
    )
    saveable = Saveable(
        text="Long caption " * 150,
        files=[NoteFile(id="first", type=ContentType.PHOTO), NoteFile(id="second", type=ContentType.PHOTO)],
    )
    if action_name == "reply":
        action_data = saveable.model_dump(mode="json")
    elif action_name == "send_note":
        await NoteModel(chat=chat, chat_id=group.id, names=("notice",), **saveable.model_dump()).insert()
        action_data = {"notename": "notice"}
    else:
        await RulesModel(chat=chat, **saveable.model_dump()).insert()
        action_data = None
    await FiltersModel(chat=chat, handler="spam", action=None, actions={action_name: action_data}).insert()
    error = TelegramForbiddenError(method=SendMessage(chat_id=group.id, text="Caption"), message="Forbidden")
    with patch("aiogram.methods.SendMessage.emit", AsyncMock(side_effect=error)) as send_caption:
        await test_client.send_message(text="spam", from_user=user, chat=group)
    send_caption.assert_awaited_once()
    assert await get_filter_stats(chat.iid, redis=test_services.redis) is None
    await test_client.send_message(text="spam", from_user=user, chat=group)
    assert (await get_filter_stats(chat.iid, redis=test_services.redis)).total == 1


async def test_filterstats_redis_write_failure_does_not_interrupt_actions(
    test_client: TestClient, test_services: ApplicationServices
) -> None:
    await set_feature(test_client, "filter_stats", True)
    user, group, chat = await _create_group(test_client)
    await FiltersModel(
        chat=chat, handler="spam", action=None, actions={"delmsg": None, "reply": {"text": "Stop"}}
    ).insert()
    with patch.object(test_services.redis, "pipeline", side_effect=RedisConnectionError("unavailable")):
        requests = await test_client.send_message(text="spam", from_user=user, chat=group)
    assert any("Stop" in str(request.params) for request in requests)
    assert await get_filter_stats(chat.iid, redis=test_services.redis) is None


async def test_filterstats_flag_lookup_failure_does_not_interrupt_following_actions(
    test_client: TestClient, test_services: ApplicationServices
) -> None:
    await set_feature(test_client, "filter_stats", True)
    user, group, chat = await _create_group(test_client)
    await FiltersModel(
        chat=chat, handler="spam", action=None, actions={"delmsg": None, "reply": {"text": "Stop"}}
    ).insert()
    original_hget = test_services.redis.hget

    async def hget(name: str, key: str) -> bytes | None:
        if key == "filter_stats":
            raise RedisConnectionError("unavailable")
        return await original_hget(name, key)

    with patch.object(test_services.redis, "hget", side_effect=hget):
        requests = await test_client.send_message(text="spam", from_user=user, chat=group)
    assert any("Stop" in str(request.params) for request in requests)
    assert await get_filter_stats(chat.iid, redis=test_services.redis) is None


async def test_filterstats_report_read_failure_shows_unavailable(
    test_client: TestClient, test_services: ApplicationServices
) -> None:
    await set_feature(test_client, "filter_stats", True)
    user, group, _chat = await _create_group(test_client)
    await grant_admin(group.id, user.id)
    with patch.object(test_services.redis, "hgetall", AsyncMock(side_effect=RedisConnectionError("unavailable"))):
        report = await test_client.send_command(command="filterstats", from_user=user, chat=group)
    assert any("temporarily unavailable" in str(request.params) for request in report)


async def test_filterstats_report_bounds_large_reports(
    test_client: TestClient, test_services: ApplicationServices
) -> None:
    await set_feature(test_client, "filter_stats", True)
    user, group, chat = await _create_group(test_client)
    await grant_admin(group.id, user.id)
    identities: list[str] = []
    for index in range(17):
        filter_item = await FiltersModel(chat=chat, handler=f"{index}:" + "<&😀" * 1000, action=None).insert()
        identities.append(str(filter_item.id))
        await record_filter_action(chat.iid, filter_item, redis=test_services.redis)
    report = await test_client.send_command(command="filterstats", from_user=user, chat=group)
    documents = [request.params["rich_message"]["html"] for request in report if "rich_message" in request.params]
    assert len(documents) == 3
    assert all(len(document) < 4096 for document in documents)
    assert all(identity in "".join(documents) for identity in identities)


async def test_filterstats_connected_report_uses_group_permissions_and_flag(
    test_client: TestClient, test_services: ApplicationServices
) -> None:
    user, group, chat = await _create_group(test_client)
    await grant_admin(group.id, user.id)
    await set_chat_override("filter_stats", group.id, True, redis=test_services.redis)
    await FiltersModel(chat=chat, handler="spam", action=None, actions={"reply": {"text": "Stop"}}).insert()
    await test_client.send_message(text="spam", from_user=user, chat=group)
    await test_client.send_command(command="connect", from_user=user, args=str(group.id))
    private = Chat(id=user.id, type="private", first_name=user.first_name)
    report = await test_client.send_command(command="filterstats", from_user=user, chat=private)
    assert any("Total successful action executions: 1" in str(request.params) for request in report)
    await set_chat_override("filter_stats", group.id, False, redis=test_services.redis)
    assert not await test_client.send_command(command="filterstats", from_user=user, chat=private)
