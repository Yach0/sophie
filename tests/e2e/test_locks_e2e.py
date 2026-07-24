"""End-to-end tests for locks: /lock persists and the enforcer deletes matching messages.

Locks enforcement is an outer middleware registered by the module, so it runs in the real
dispatcher. A locked message from a non-admin is deleted (captured deleteMessage); admins and
unlocked types pass through.
"""

from __future__ import annotations

import pytest
from aiogram.types import InlineKeyboardMarkup
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import MessageFactory, UserFactory
from aiogram_test_framework.types import RequestType

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel, LocksModel
from sophie_bot.modules.locks.callbacks import UnlockAllCallback
from tests.e2e.helpers import create_test_user_and_group, grant_admin, grant_bot_admin, next_user_id


async def _group_with_member(test_client: TestClient) -> tuple[object, object, object]:
    admin, group, _model = await create_test_user_and_group(test_client, group_title="Locks Group")
    await grant_admin(group.id, admin.id)
    await grant_bot_admin(group.id)

    member = test_client.create_user(user_id=next_user_id(), first_name="Member", username="locks_member")
    await test_client.send_message(text="init", from_user=member.user, chat=group)
    return admin, group, member.user


async def _locked_types(group_tid: int) -> set[str]:
    chat = await ChatModel.get_by_tid(group_tid)
    assert chat is not None
    return (await LocksModel.get_by_chat_iid(chat.iid)).locked_types


def _deleted(requests: list) -> list:
    return [request for request in requests if request.request_type == RequestType.DELETE_MESSAGE]


@pytest.mark.asyncio
async def test_lock_command_persists(test_client: TestClient) -> None:
    admin, group, _member = await _group_with_member(test_client)

    requests = await test_client.send_command(command="lock", from_user=admin, args="text", chat=group)

    assert any("lock added" in (request.text or "").lower() for request in requests)
    assert "text" in await _locked_types(group.id)


@pytest.mark.asyncio
async def test_lock_command_requires_admin(test_client: TestClient) -> None:
    _admin, group, member = await _group_with_member(test_client)

    requests = await test_client.send_command(command="lock", from_user=member, args="text", chat=group)

    assert any("administrator" in (request.text or "").lower() for request in requests)
    assert "text" not in await _locked_types(group.id)


@pytest.mark.asyncio
async def test_locked_message_from_member_is_deleted(test_client: TestClient) -> None:
    admin, group, member = await _group_with_member(test_client)
    await test_client.send_command(command="lock", from_user=admin, args="text", chat=group)

    requests = await test_client.send_message(text="just some chatter", from_user=member, chat=group)

    assert _deleted(requests), "A locked text message from a member should be deleted"


@pytest.mark.asyncio
async def test_locked_message_from_admin_is_kept(test_client: TestClient) -> None:
    admin, group, _member = await _group_with_member(test_client)
    await test_client.send_command(command="lock", from_user=admin, args="text", chat=group)

    requests = await test_client.send_message(text="admins may speak freely", from_user=admin, chat=group)

    assert not _deleted(requests), "Admins are exempt from lock enforcement"


@pytest.mark.asyncio
async def test_unlock_stops_enforcement(test_client: TestClient) -> None:
    admin, group, member = await _group_with_member(test_client)
    await test_client.send_command(command="lock", from_user=admin, args="text", chat=group)

    await test_client.send_command(command="unlock", from_user=admin, args="text", chat=group)
    assert "text" not in await _locked_types(group.id)

    requests = await test_client.send_message(text="now allowed", from_user=member, chat=group)
    assert not _deleted(requests), "An unlocked type must no longer be deleted"


@pytest.mark.asyncio
async def test_locks_list_shows_locked_types(test_client: TestClient) -> None:
    admin, group, _member = await _group_with_member(test_client)
    await test_client.send_command(command="lock", from_user=admin, args="text", chat=group)

    requests = await test_client.send_command(command="locks", from_user=admin, chat=group)

    assert any("text" in (request.text or "") for request in requests)


@pytest.mark.asyncio
async def test_unlockall_clears_every_lock_after_confirm(test_client: TestClient) -> None:
    admin, group, _member = await _group_with_member(test_client)
    await test_client.send_command(command="lock", from_user=admin, args="text", chat=group)
    await test_client.send_command(command="lock", from_user=admin, args="url", chat=group)
    assert {"text", "url"} <= await _locked_types(group.id)

    prompt_requests = await test_client.send_command(command="unlockall", from_user=admin, chat=group)
    markup_data = next(
        request.params.get("reply_markup")
        for request in reversed(prompt_requests)
        if request.params.get("reply_markup")
    )
    markup = InlineKeyboardMarkup.model_validate(markup_data)
    confirm_data = UnlockAllCallback(user_id=admin.id).pack()
    assert any(button.callback_data == confirm_data for row in markup.inline_keyboard for button in row), (
        "unlockall should offer a confirmation button"
    )

    bot_user = UserFactory.create(user_id=CONFIG.bot_id, first_name="Sophie", is_bot=True)
    prompt = MessageFactory.create(text="Unlock all?", from_user=bot_user, chat=group, reply_markup=markup)
    await test_client.send_callback(confirm_data, from_user=admin, message=prompt)

    assert not await _locked_types(group.id), "Confirming unlockall should remove every lock"
