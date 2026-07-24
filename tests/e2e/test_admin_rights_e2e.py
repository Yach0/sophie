from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram import F, Router
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory
from aiogram_test_framework.types import RequestType

from sophie_bot.constants import TELEGRAM_ANONYMOUS_ADMIN_BOT_ID
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter
from tests.e2e.helpers import grant_admin

TEST_ROUTER = Router(name="admin_rights_e2e_router")


@pytest.fixture(autouse=True)
def _register_test_router(extra_router: Callable[[Router], Router]) -> None:
    """Attach the handlers below for the duration of each test, then detach them."""
    extra_router(TEST_ROUTER)


@TEST_ROUTER.message(CMDFilter("e2e_admin_required"), UserRestricting(admin=True))
async def e2e_admin_required_handler(message: Message) -> None:
    await message.reply("E2E_ADMIN_OK")


@TEST_ROUTER.message(CMDFilter("e2e_restrict_required"), UserRestricting(can_restrict_members=True))
async def e2e_restrict_required_handler(message: Message) -> None:
    await message.reply("E2E_RESTRICT_OK")


@TEST_ROUTER.callback_query(F.data == "e2e_admin_cb", UserRestricting(admin=True))
async def e2e_admin_cb_handler(callback: CallbackQuery) -> None:
    await callback.answer("E2E_CB_OK")


def _anonymous_message(chat: Chat, *, message_id: int, title: str, thread_id: int) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=chat,
        from_user=User(
            id=TELEGRAM_ANONYMOUS_ADMIN_BOT_ID,
            is_bot=True,
            first_name="GroupAnonymousBot",
            username="GroupAnonymousBot",
        ),
        sender_chat=chat,
        author_signature=title,
        is_topic_message=True,
        message_thread_id=thread_id,
        text="/e2e_restrict_required",
    )


async def _new_requests_for_update(test_client: TestClient, update: Update) -> list[Any]:
    start_index = len(test_client.capture)
    await test_client.dispatcher.feed_update(bot=test_client.bot, update=update)
    return test_client.capture.all_requests[start_index:]


@pytest.mark.asyncio
async def test_admin_required_denies_non_admin(
    test_client: TestClient,
) -> None:
    user_wrapper = test_client.create_user(user_id=910001, first_name="RegularUser", username="regular_user")
    group_chat = ChatFactory.create_group(chat_id=-1002000010001, title="Admin Rights E2E Group")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group_chat)
    requests = await test_client.send_command(
        command="e2e_admin_required",
        from_user=user_wrapper.user,
        chat=group_chat,
    )

    assert requests, "Bot should respond when non-admin uses admin-only command."
    assert any("You must be an administrator" in (request.text or "") for request in requests)


@pytest.mark.asyncio
async def test_admin_required_callback_denies_non_admin_via_alert(
    test_client: TestClient,
) -> None:
    """A non-admin tapping an admin-only inline button must get a private alert
    popup, not a chat message that spams everyone. Regression for the /lang bug.
    """
    user_wrapper = test_client.create_user(user_id=910006, first_name="RegularClicker", username="regular_clicker")
    group_chat = ChatFactory.create_group(chat_id=-1002000010006, title="Admin Rights Callback Group")

    await test_client.send_message(text="init", from_user=user_wrapper.user, chat=group_chat)

    button_message = Message(
        message_id=6001,
        date=datetime.now(UTC),
        chat=group_chat,
        from_user=User(id=123456, is_bot=True, first_name="TestBot"),
        text="Button message",
    )
    requests = await test_client.send_callback(
        data="e2e_admin_cb",
        from_user=user_wrapper.user,
        message=button_message,
    )

    # The denial must be delivered as an alert popup on the clicking user's client.
    callback_answers = [req for req in requests if req.request_type == RequestType.ANSWER_CALLBACK_QUERY]
    assert callback_answers, "Non-admin button click should be answered with a callback alert."
    assert any("You must be an administrator" in (req.text or "") for req in callback_answers)
    assert all(req.params.get("show_alert") for req in callback_answers)

    # It must NOT be posted as a message to the whole chat.
    sent_messages = [req for req in requests if req.request_type == RequestType.SEND_MESSAGE]
    assert not any("You must be an administrator" in (req.text or "") for req in sent_messages), (
        "Admin denial must not be sent as a chat message from a callback query."
    )


@pytest.mark.asyncio
async def test_anonymous_admin_duplicate_title_mixed_permissions_denied(
    test_client: TestClient,
) -> None:
    group_chat = Chat(id=-1002000010002, type="supergroup", title="Forum Group", is_forum=True)

    first_admin = test_client.create_user(user_id=910002, first_name="AdminOne", username="admin_one")
    second_admin = test_client.create_user(user_id=910003, first_name="AdminTwo", username="admin_two")

    await test_client.send_message(text="init", from_user=first_admin.user, chat=group_chat)
    await test_client.send_message(text="init", from_user=second_admin.user, chat=group_chat)

    # Two anonymous admins share a title but disagree on can_restrict_members, so the identity
    # behind the signature is ambiguous and the action must be refused.
    await grant_admin(group_chat.id, first_admin.user.id, is_anonymous=True, custom_title="Moderator")
    await grant_admin(
        group_chat.id, second_admin.user.id, is_anonymous=True, custom_title="Moderator", can_restrict_members=False
    )

    anonymous_message = _anonymous_message(group_chat, message_id=5551, title="Moderator", thread_id=77)
    requests = await _new_requests_for_update(test_client, Update(update_id=88001, message=anonymous_message))

    assert requests, "Bot should respond to ambiguous anonymous admin identity."
    assert any("Multiple anonymous admins share this title" in (request.text or "") for request in requests)


@pytest.mark.asyncio
async def test_anonymous_admin_duplicate_title_all_permissions_allowed(
    test_client: TestClient,
) -> None:
    group_chat = Chat(id=-1002000010003, type="supergroup", title="Forum Group OK", is_forum=True)

    first_admin = test_client.create_user(user_id=910004, first_name="AdminThree", username="admin_three")
    second_admin = test_client.create_user(user_id=910005, first_name="AdminFour", username="admin_four")

    await test_client.send_message(text="init", from_user=first_admin.user, chat=group_chat)
    await test_client.send_message(text="init", from_user=second_admin.user, chat=group_chat)

    # Both anonymous admins share the title and both may restrict, so the signature is
    # unambiguous with respect to the required permission and the action is allowed.
    await grant_admin(group_chat.id, first_admin.user.id, is_anonymous=True, custom_title="Guardian")
    await grant_admin(group_chat.id, second_admin.user.id, is_anonymous=True, custom_title="Guardian")

    anonymous_message = _anonymous_message(group_chat, message_id=5552, title="Guardian", thread_id=91)
    requests = await _new_requests_for_update(test_client, Update(update_id=88002, message=anonymous_message))

    assert requests, "Bot should respond when anonymous admin permissions are valid."
    assert any((request.text or "") == "E2E_RESTRICT_OK" for request in requests)
