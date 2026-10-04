from __future__ import annotations

import pytest
from aiogram.types import Chat
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import MessageFactory, UserFactory
from aiogram_test_framework.types import CapturedRequest, RequestType

from sophie_bot.db.models import ChatModel, FiltersModel
from sophie_bot.modules.filters.callbacks import (
    FilterDeleteConfirmCallback,
    FilterManagementCallback,
    FiltersPageCallback,
)
from sophie_bot.modules.utils_.wizard import WizardCallback
from tests.e2e.helpers import create_test_user_and_group, get_wizard_session_id, grant_admin


def rich_html(requests: list[CapturedRequest], chat_tid: int) -> str:
    request = next(
        request for request in requests if isinstance(request.params, dict) and "rich_message" in request.params
    )
    assert request.params["chat_id"] == chat_tid
    return request.params["rich_message"]["html"]


@pytest.mark.asyncio
@pytest.mark.parametrize("connected", [False, True])
async def test_filters_list_and_page_use_effective_group(test_client: TestClient, connected: bool) -> None:
    user, group, user_model = await create_test_user_and_group(test_client, group_title="Filter Target")
    await grant_admin(group.id, user.id)
    model = await ChatModel.get_by_tid(group.id)
    assert model is not None
    for index in range(9):
        await FiltersModel(chat=model, handler=f"group-filter-{index}", action=None, actions={}).insert()
    await FiltersModel(chat=user_model, handler="dm-only-filter", action=None, actions={}).insert()
    if connected:
        await test_client.send_command(command="connect", from_user=user, args=str(group.id))
    event_chat = Chat(id=user.id, type="private", first_name=user.first_name) if connected else group
    requests = await test_client.send_command(command="filters", from_user=user, chat=event_chat)
    html = rich_html(requests, event_chat.id)
    assert "group-filter-0" in html
    assert "Filter Target" in html
    assert "group-filter-8" not in html
    assert "dm-only-filter" not in html
    message = MessageFactory.create(text="Filters", chat=event_chat, from_user=UserFactory.create(is_bot=True))
    requests = await test_client.send_callback(FiltersPageCallback(page=1).pack(), from_user=user, message=message)
    page_html = rich_html(requests, event_chat.id)
    assert "group-filter-8" in page_html
    assert "group-filter-0" not in page_html
    assert "Filter Target" in page_html
    assert "dm-only-filter" not in page_html


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["command", "delete", "edit"])
async def test_connected_filter_management_uses_group(test_client: TestClient, operation: str) -> None:
    user, group, user_model = await create_test_user_and_group(test_client)
    await grant_admin(group.id, user.id)
    model = await ChatModel.get_by_tid(group.id)
    assert model is not None
    item = await FiltersModel(chat=model, handler="managed-keyword", action=None, actions={"kick_user": None}).insert()
    dm_item = await FiltersModel(chat=user_model, handler="managed-keyword", action=None, actions={}).insert()
    assert item.id is not None
    await test_client.send_command(command="connect", from_user=user, args=str(group.id))
    event_chat = Chat(id=user.id, type="private", first_name=user.first_name)
    message = MessageFactory.create(text="Filters", chat=event_chat, from_user=UserFactory.create(is_bot=True))
    if operation == "command":
        requests = await test_client.send_command(
            command="delfilter", from_user=user, chat=event_chat, args="managed-keyword"
        )
        assert any(
            request.params.get("chat_id") == event_chat.id and "deleted" in (request.text or "") for request in requests
        )
    else:
        requests = await test_client.send_callback(
            FilterManagementCallback(operation=operation, oid=str(item.id)).pack(), from_user=user, message=message
        )
        assert "managed-keyword" in rich_html(requests, event_chat.id)
        if operation == "edit":
            session_id = await get_wizard_session_id(test_client, event_chat.id, user.id)
            await test_client.send_callback(
                WizardCallback(scope="filter_action", op="toggle", session_id=session_id, arg="silent").pack(),
                from_user=user,
                message=message,
            )
            requests = await test_client.send_callback(
                WizardCallback(scope="filter_action", op="done", session_id=session_id).pack(),
                from_user=user,
                message=message,
            )
            assert "saved" in rich_html(requests, event_chat.id)
            saved = await FiltersModel.get_by_id(item.id)
            assert saved is not None and saved.silent is True
            assert saved.chat.ref.id == model.iid
        else:
            assert await FiltersModel.get_by_id(item.id) is not None
            await test_client.send_callback(
                FilterDeleteConfirmCallback(oid=str(item.id)).pack(), from_user=user, message=message
            )
    if operation != "edit":
        assert await FiltersModel.get_by_id(item.id) is None
    private_filters = await FiltersModel.get_filters(user_model.iid)
    assert private_filters is not None and [item.id for item in private_filters] == [dm_item.id]
    assert private_filters[0].silent is False and private_filters[0].actions == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["edit", "delete", "confirm"])
@pytest.mark.parametrize("admin", [False, True])
async def test_connected_filter_callbacks_require_admin_and_matching_chat(
    test_client: TestClient, operation: str, admin: bool
) -> None:
    user, group, _user_model = await create_test_user_and_group(test_client)
    if admin:
        await grant_admin(group.id, user.id)
    _other_user, other_group, _other_model = await create_test_user_and_group(test_client)
    model = await ChatModel.get_by_tid(other_group.id if admin else group.id)
    assert model is not None
    item = await FiltersModel(chat=model, handler="foreign-keyword", action=None, actions={"kick_user": None}).insert()
    assert item.id is not None
    await test_client.send_command(command="connect", from_user=user, args=str(group.id))
    message = MessageFactory.create(
        text="Filters",
        chat=Chat(id=user.id, type="private", first_name=user.first_name),
        from_user=UserFactory.create(is_bot=True),
    )
    payload = (
        FilterDeleteConfirmCallback(oid=str(item.id)).pack()
        if operation == "confirm"
        else FilterManagementCallback(operation=operation, oid=str(item.id)).pack()
    )
    requests = await test_client.send_callback(payload, from_user=user, message=message)
    assert any(
        request.request_type == RequestType.ANSWER_CALLBACK_QUERY
        and request.params.get("show_alert") is True
        and ("not found" if admin else "administrator") in (request.text or "")
        for request in requests
    )
    assert await FiltersModel.get_by_id(item.id) is not None


@pytest.mark.asyncio
async def test_filter_wizard_rejects_save_after_connection_switch(test_client: TestClient) -> None:
    user, group, _user_model = await create_test_user_and_group(test_client)
    await grant_admin(group.id, user.id)
    _other_user, other_group, _other_model = await create_test_user_and_group(test_client)
    await grant_admin(other_group.id, user.id)
    await test_client.send_command(command="connect", from_user=user, args=str(group.id))
    await test_client.send_command(command="addfilter", from_user=user, args="old-keyword")
    session_id = await get_wizard_session_id(test_client, user.id, user.id)
    message = MessageFactory.create(
        text="Wizard",
        chat=Chat(id=user.id, type="private", first_name=user.first_name),
        from_user=UserFactory.create(is_bot=True),
    )
    await test_client.send_callback(
        WizardCallback(scope="filter_action", op="select", session_id=session_id, arg="kick_user").pack(),
        from_user=user,
        message=message,
    )
    await test_client.send_command(command="connect", from_user=user, args=str(other_group.id))
    requests = await test_client.send_callback(
        WizardCallback(scope="filter_action", op="done", session_id=session_id).pack(), from_user=user, message=message
    )
    assert any("expired" in (request.text or "") and request.params.get("show_alert") for request in requests)
    assert await FiltersModel.find_all().to_list() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["addfilter", "delfilter"])
async def test_connected_non_admin_can_list_but_cannot_manage_filters(test_client: TestClient, command: str) -> None:
    user, group, _user_model = await create_test_user_and_group(test_client)
    model = await ChatModel.get_by_tid(group.id)
    assert model is not None
    item = await FiltersModel(chat=model, handler="readable-keyword", action=None, actions={"kick_user": None}).insert()
    assert item.id is not None
    await test_client.send_command(command="connect", from_user=user, args=str(group.id))
    requests = await test_client.send_command(command="filters", from_user=user)
    assert "readable-keyword" in rich_html(requests, user.id)
    requests = await test_client.send_command(command=command, from_user=user, args="readable-keyword")
    assert any("administrator" in (request.text or "") for request in requests)
    assert await FiltersModel.get_by_id(item.id) is not None
    state = test_client.dispatcher.fsm.get_context(bot=test_client.bot, chat_id=user.id, user_id=user.id)
    assert "wizard" not in await state.get_data()
