"""End-to-end tests for connections: manage a group from Sophie's DM.

Exercises ConnectionsMiddleware end to end — /connect in a private chat binds the user to a
group, a group-scoped command then run in the DM operates on the connected group, and
/disconnect unbinds.
"""

from __future__ import annotations

import pytest
from aiogram_test_framework import TestClient

from sophie_bot.db.models import ChatModel, RulesModel
from sophie_bot.db.models.chat_connection_settings import ChatConnectionSettingsModel
from sophie_bot.db.models.chat_connections import ChatConnectionModel
from sophie_bot.db.models.notes import Saveable
from tests.e2e.helpers import create_test_user_and_group, grant_admin, next_user_id


async def _connectable_group(test_client: TestClient, *, username: str, title: str):
    """A registered group with a username, so it can be reached via `/connect @username`."""
    admin, group, _user_model = await create_test_user_and_group(test_client, group_title=title)
    group_model = await ChatModel.get_by_tid(group.id)
    assert group_model is not None
    group_model.username = username
    await group_model.save()
    return admin, group, group_model


async def _connected_chat_tid(user_tid: int) -> int | None:
    connection = await ChatConnectionModel.get_by_user_tid(user_tid)
    if not connection or not connection.chat:
        return None
    chat = await connection.chat.fetch()
    return chat.tid if isinstance(chat, ChatModel) else None


@pytest.mark.asyncio
async def test_admin_connects_to_group_by_username(test_client: TestClient) -> None:
    admin, group, _model = await _connectable_group(test_client, username="connectgroup", title="Connect Group")
    await grant_admin(group.id, admin.id)

    requests = await test_client.send_command(command="connect", from_user=admin, args="@connectgroup")

    assert any("connected" in (request.text or "").lower() for request in requests)
    assert await _connected_chat_tid(admin.id) == group.id


@pytest.mark.asyncio
async def test_admin_connects_to_group_by_numeric_id(test_client: TestClient) -> None:
    # Regression: a supergroup's negative ID must be accepted by the /connect arg.
    admin, group, _model = await create_test_user_and_group(test_client, group_title="Connect By ID Group")
    await grant_admin(group.id, admin.id)

    requests = await test_client.send_command(command="connect", from_user=admin, args=str(group.id))

    assert any("connected" in (request.text or "").lower() for request in requests)
    assert await _connected_chat_tid(admin.id) == group.id


@pytest.mark.asyncio
async def test_command_in_dm_targets_the_connected_group(test_client: TestClient) -> None:
    admin, group, model = await _connectable_group(test_client, username="rulesgroup", title="Connect Rules Group")
    await grant_admin(group.id, admin.id)
    await RulesModel.set_rules(model.iid, Saveable(text="Connected-group rules"))

    await test_client.send_command(command="connect", from_user=admin, args="@rulesgroup")

    # /rules sent from the DM (no chat arg) must resolve to the connected group's rules.
    requests = await test_client.send_command(command="rules", from_user=admin)

    assert any("Connected-group rules" in (request.text or "") for request in requests)


@pytest.mark.asyncio
async def test_disconnect_unbinds(test_client: TestClient) -> None:
    admin, group, _model = await _connectable_group(test_client, username="disconngroup", title="Disconnect Group")
    await grant_admin(group.id, admin.id)
    await test_client.send_command(command="connect", from_user=admin, args="@disconngroup")
    assert await _connected_chat_tid(admin.id) == group.id

    requests = await test_client.send_command(command="disconnect", from_user=admin)

    assert any("disconnected" in (request.text or "").lower() for request in requests)
    assert await _connected_chat_tid(admin.id) is None


@pytest.mark.asyncio
async def test_disconnect_without_connection(test_client: TestClient) -> None:
    user = test_client.create_user(user_id=next_user_id(), first_name="Lonely", username="lonely_user")
    await test_client.send_message(text="init", from_user=user.user)

    requests = await test_client.send_command(command="disconnect", from_user=user.user)

    assert any("not currently connected" in (request.text or "").lower() for request in requests)


@pytest.mark.asyncio
async def test_non_admin_cannot_connect_when_disabled(test_client: TestClient) -> None:
    _admin, _group, model = await _connectable_group(test_client, username="lockedgroup", title="Connect Locked Group")
    # Disable open connections; only admins may connect now.
    settings = await ChatConnectionSettingsModel.get_by_chat_iid(model.iid) or ChatConnectionSettingsModel(
        chat=model.iid
    )
    settings.allow_users_connect = False
    await settings.save()

    stranger = test_client.create_user(user_id=next_user_id(), first_name="Stranger", username="connect_stranger")
    # Register the stranger via a DM, not a group message: a group message would re-upsert the
    # group ChatModel from the (username-less) event chat and wipe the username we just set.
    await test_client.send_message(text="init", from_user=stranger.user)

    requests = await test_client.send_command(command="connect", from_user=stranger.user, args="@lockedgroup")

    assert any("not allowed" in (request.text or "").lower() for request in requests)
    assert await _connected_chat_tid(stranger.user.id) is None


@pytest.mark.asyncio
async def test_connect_in_group_from_bot_is_rejected(test_client: TestClient) -> None:
    from aiogram.types import User

    _admin, group, _model = await _connectable_group(test_client, username="botgroup", title="Bot Connect Group")
    bot_user = User(id=next_user_id(), is_bot=True, first_name="OtherBot", username="other_bot")

    requests = await test_client.send_command(command="connect", chat=group, from_user=bot_user)

    assert any("Bots and anonymous admins cannot connect" in (request.text or "") for request in requests)
