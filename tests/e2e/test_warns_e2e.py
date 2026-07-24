"""End-to-end tests for the warns lifecycle.

Drives /warn, /warnlimit, /resetwarns and the inline delete/reset buttons through the real
dispatcher, asserting on WarnModel rows and — when the limit is hit — the captured
banChatMember call.
"""

from __future__ import annotations

import pytest
from aiogram.types import InlineKeyboardMarkup, User
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import MessageFactory, UserFactory
from aiogram_test_framework.types import RequestType

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel
from sophie_bot.db.models.warns import WarnModel, WarnSettingsModel
from sophie_bot.modules.warns.callbacks import ResetWarnsCallback
from tests.e2e.helpers import create_test_user_and_group, grant_admin, grant_bot_admin, next_user_id


async def _moderated_group(test_client: TestClient) -> tuple[User, object, int]:
    """A group with an admin and the bot, plus a plain member to warn."""
    admin, group, _model = await create_test_user_and_group(test_client, group_title="Warns Group")
    await grant_admin(group.id, admin.id)
    await grant_bot_admin(group.id)

    target_id = next_user_id()
    target = test_client.create_user(user_id=target_id, first_name="Target", username=f"target_{target_id}")
    await test_client.send_message(text="init", from_user=target.user, chat=group)
    return admin, group, target_id


async def _warn_count(group_tid: int, user_tid: int) -> int:
    chat = await ChatModel.get_by_tid(group_tid)
    user = await ChatModel.get_by_tid(user_tid)
    assert chat is not None and user is not None
    return await WarnModel.count_user_warns(chat.iid, user.iid)


def _last_markup(requests: list) -> InlineKeyboardMarkup | None:
    for request in reversed(requests):
        markup = request.params.get("reply_markup")
        if markup:
            return InlineKeyboardMarkup.model_validate(markup)
    return None


@pytest.mark.asyncio
async def test_warn_requires_restrict_rights(test_client: TestClient) -> None:
    _admin, group, target_id = await _moderated_group(test_client)
    stranger = test_client.create_user(user_id=next_user_id(), first_name="Stranger", username="warn_stranger")
    await test_client.send_message(text="init", from_user=stranger.user, chat=group)

    requests = await test_client.send_command(command="warn", from_user=stranger.user, args=str(target_id), chat=group)

    assert any("administrator" in (request.text or "").lower() for request in requests)
    assert await _warn_count(group.id, target_id) == 0


@pytest.mark.asyncio
async def test_warn_records_a_warning(test_client: TestClient) -> None:
    admin, group, target_id = await _moderated_group(test_client)

    requests = await test_client.send_command(
        command="warn", from_user=admin, args=f"{target_id} being rude", chat=group
    )

    assert any("warned" in (request.text or "").lower() for request in requests)
    assert await _warn_count(group.id, target_id) == 1


@pytest.mark.asyncio
async def test_warn_cannot_target_admin(test_client: TestClient) -> None:
    admin, group, _target_id = await _moderated_group(test_client)
    other_admin = test_client.create_user(user_id=next_user_id(), first_name="OtherAdmin", username="other_admin")
    await test_client.send_message(text="init", from_user=other_admin.user, chat=group)
    await grant_admin(group.id, other_admin.user.id)

    requests = await test_client.send_command(
        command="warn", from_user=admin, args=str(other_admin.user.id), chat=group
    )

    assert any("cannot warn an admin" in (request.text or "").lower() for request in requests)
    assert await _warn_count(group.id, other_admin.user.id) == 0


@pytest.mark.asyncio
async def test_reaching_the_limit_bans_and_clears_warns(test_client: TestClient) -> None:
    admin, group, target_id = await _moderated_group(test_client)
    # Lower the limit to 2 so the second warn triggers the max action.
    await test_client.send_command(command="warnlimit", from_user=admin, args="2", chat=group)

    await test_client.send_command(command="warn", from_user=admin, args=str(target_id), chat=group)
    assert await _warn_count(group.id, target_id) == 1

    requests = await test_client.send_command(command="warn", from_user=admin, args=str(target_id), chat=group)

    bans = [
        request
        for request in requests
        if request.request_type == RequestType.BAN_CHAT_MEMBER and request.params.get("user_id") == target_id
    ]
    assert bans, "Reaching the warn limit should ban the user"
    assert await _warn_count(group.id, target_id) == 0, "Warns are cleared once the punishment fires"


@pytest.mark.asyncio
async def test_warnlimit_persists(test_client: TestClient) -> None:
    admin, group, _target_id = await _moderated_group(test_client)

    await test_client.send_command(command="warnlimit", from_user=admin, args="5", chat=group)

    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    settings = await WarnSettingsModel.get_by_chat_iid(chat.iid)
    assert settings.max_warns == 5


@pytest.mark.asyncio
async def test_delete_warn_button_removes_the_warning(test_client: TestClient) -> None:
    admin, group, target_id = await _moderated_group(test_client)

    warn_requests = await test_client.send_command(command="warn", from_user=admin, args=str(target_id), chat=group)
    assert await _warn_count(group.id, target_id) == 1

    markup = _last_markup(warn_requests)
    assert markup is not None
    delete_button = next(
        button
        for row in markup.inline_keyboard
        for button in row
        if (button.callback_data or "").startswith("del_warn")
    )

    bot_user = UserFactory.create(user_id=CONFIG.bot_id, first_name="Sophie", is_bot=True)
    warn_message = MessageFactory.create(text="⚠️ User warned", from_user=bot_user, chat=group, reply_markup=markup)
    await test_client.send_callback(delete_button.callback_data, from_user=admin, message=warn_message)

    assert await _warn_count(group.id, target_id) == 0, "The delete-warn button should remove the warning"


@pytest.mark.asyncio
async def test_delete_warn_button_rejects_non_admin(test_client: TestClient) -> None:
    admin, group, target_id = await _moderated_group(test_client)
    warn_requests = await test_client.send_command(command="warn", from_user=admin, args=str(target_id), chat=group)
    markup = _last_markup(warn_requests)
    assert markup is not None
    delete_data = next(
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if (button.callback_data or "").startswith("del_warn")
    )

    stranger = test_client.create_user(user_id=next_user_id(), first_name="Stranger", username="warn_clicker")
    await test_client.send_message(text="init", from_user=stranger.user, chat=group)
    bot_user = UserFactory.create(user_id=CONFIG.bot_id, first_name="Sophie", is_bot=True)
    warn_message = MessageFactory.create(text="⚠️ User warned", from_user=bot_user, chat=group, reply_markup=markup)

    answers = await test_client.send_callback(delete_data, from_user=stranger.user, message=warn_message)

    assert any("only admins" in (answer.text or "").lower() for answer in answers)
    assert await _warn_count(group.id, target_id) == 1, "A non-admin must not be able to delete the warning"


@pytest.mark.asyncio
async def test_resetwarns_confirm_clears_all_warnings(test_client: TestClient) -> None:
    admin, group, target_id = await _moderated_group(test_client)
    await test_client.send_command(command="warn", from_user=admin, args=str(target_id), chat=group)
    await test_client.send_command(command="warn", from_user=admin, args=str(target_id), chat=group)
    assert await _warn_count(group.id, target_id) == 2

    reset_requests = await test_client.send_command(
        command="resetwarns", from_user=admin, args=str(target_id), chat=group
    )
    markup = _last_markup(reset_requests)
    assert markup is not None
    confirm_data = next(
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if (button.callback_data or "").startswith("reset_warns")
    )
    assert confirm_data == ResetWarnsCallback(user_tid=target_id).pack()

    bot_user = UserFactory.create(user_id=CONFIG.bot_id, first_name="Sophie", is_bot=True)
    prompt = MessageFactory.create(text="Reset warns?", from_user=bot_user, chat=group, reply_markup=markup)
    await test_client.send_callback(confirm_data, from_user=admin, message=prompt)

    assert await _warn_count(group.id, target_id) == 0, "Confirming the reset should clear every warning"
