from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from aiogram import Dispatcher, Router
from aiogram.enums import ChatMemberStatus
from aiogram.types import Chat, ChatMemberAdministrator, Message, Update, User
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import ChatFactory

from sophie_bot.constants import TELEGRAM_ANONYMOUS_ADMIN_BOT_ID
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.cmd import CMDFilter


@dataclass
class _FakeUserLink:
    """Mimics a Beanie Link[ChatModel] with an async fetch() method."""

    user_model: Any

    async def fetch(self) -> Any:
        return self.user_model


@dataclass
class _FakeAdminEntry:
    """Mimics a ChatAdminModel document returned by ChatAdminModel.find()."""

    member: Any
    user: _FakeUserLink


class _FakeAdminsQuery:
    """Mimics the Beanie FindMany query object returned by ChatAdminModel.find().

    mongomock cannot handle DBRef sub-field queries (e.g. ``chat.$id``), so this
    stand-in is used to return pre-built admin entries in e2e tests.
    """

    def __init__(self, admin_entries: list[_FakeAdminEntry]) -> None:
        self._entries = admin_entries

    async def to_list(self) -> list[_FakeAdminEntry]:
        return self._entries


TEST_ROUTER = Router(name="admin_rights_e2e_router")


@TEST_ROUTER.message(CMDFilter("e2e_admin_required"), UserRestricting(admin=True))
async def e2e_admin_required_handler(message: Message) -> None:
    await message.reply("E2E_ADMIN_OK")


@TEST_ROUTER.message(CMDFilter("e2e_restrict_required"), UserRestricting(can_restrict_members=True))
async def e2e_restrict_required_handler(message: Message) -> None:
    await message.reply("E2E_RESTRICT_OK")


@pytest_asyncio.fixture
async def register_admin_rights_router(test_dispatcher: Dispatcher) -> None:
    if not getattr(test_dispatcher, "_admin_rights_e2e_router_registered", False):
        test_dispatcher.include_router(TEST_ROUTER)
        setattr(test_dispatcher, "_admin_rights_e2e_router_registered", True)


def _build_admin_member(user: User, can_restrict_members: bool, title: str) -> ChatMemberAdministrator:
    return ChatMemberAdministrator(
        status=ChatMemberStatus.ADMINISTRATOR,
        user=user,
        can_be_edited=False,
        is_anonymous=True,
        can_manage_chat=True,
        can_delete_messages=True,
        can_manage_video_chats=True,
        can_restrict_members=can_restrict_members,
        can_promote_members=False,
        can_change_info=False,
        can_invite_users=True,
        can_post_stories=False,
        can_edit_stories=False,
        can_delete_stories=False,
        can_pin_messages=False,
        can_manage_topics=False,
        custom_title=title,
    )


async def _new_requests_for_update(test_client: TestClient, update: Update) -> list[Any]:
    start_index = len(test_client.capture)
    await test_client.dispatcher.feed_update(bot=test_client.bot, update=update)
    return test_client.capture.all_requests[start_index:]


@pytest.mark.asyncio
async def test_admin_required_denies_non_admin(
    test_client: TestClient,
    register_admin_rights_router: None,
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
async def test_anonymous_admin_duplicate_title_mixed_permissions_denied(
    test_client: TestClient,
    register_admin_rights_router: None,
) -> None:
    group_chat = Chat(id=-1002000010002, type="supergroup", title="Forum Group", is_forum=True)

    first_admin = test_client.create_user(user_id=910002, first_name="AdminOne", username="admin_one")
    second_admin = test_client.create_user(user_id=910003, first_name="AdminTwo", username="admin_two")

    await test_client.send_message(text="init", from_user=first_admin.user, chat=group_chat)
    await test_client.send_message(text="init", from_user=second_admin.user, chat=group_chat)

    chat_model = await ChatModel.get_by_tid(group_chat.id)
    first_admin_model = await ChatModel.get_by_tid(first_admin.user.id)
    second_admin_model = await ChatModel.get_by_tid(second_admin.user.id)
    assert chat_model is not None
    assert first_admin_model is not None
    assert second_admin_model is not None

    member_one = _build_admin_member(first_admin.user, can_restrict_members=True, title="Moderator")
    member_two = _build_admin_member(second_admin.user, can_restrict_members=False, title="Moderator")

    # Build fake admin entries to work around mongomock's inability to query
    # DBRef sub-fields (``chat.$id``) used by ChatAdminModel.find().
    fake_admins = [
        _FakeAdminEntry(member=member_one, user=_FakeUserLink(first_admin_model)),
        _FakeAdminEntry(member=member_two, user=_FakeUserLink(second_admin_model)),
    ]

    anonymous_message = Message(
        message_id=5551,
        date=datetime.now(timezone.utc),
        chat=group_chat,
        from_user=User(
            id=TELEGRAM_ANONYMOUS_ADMIN_BOT_ID,
            is_bot=True,
            first_name="GroupAnonymousBot",
            username="GroupAnonymousBot",
        ),
        sender_chat=group_chat,
        author_signature="Moderator",
        is_topic_message=True,
        message_thread_id=77,
        text="/e2e_restrict_required",
    )

    # Patch needs to target where the object is looked up, not where it's defined.
    # This ensures the patch works correctly in parallel test execution.
    from sophie_bot.filters import admin_rights as admin_rights_module

    with patch.object(admin_rights_module.ChatAdminModel, "find", lambda *_a, **_kw: _FakeAdminsQuery(fake_admins)):
        requests = await _new_requests_for_update(test_client, Update(update_id=88001, message=anonymous_message))

    assert requests, "Bot should respond to ambiguous anonymous admin identity."
    assert any("Multiple anonymous admins share this title" in (request.text or "") for request in requests)


@pytest.mark.asyncio
async def test_anonymous_admin_duplicate_title_all_permissions_allowed(
    test_client: TestClient,
    register_admin_rights_router: None,
) -> None:
    group_chat = Chat(id=-1002000010003, type="supergroup", title="Forum Group OK", is_forum=True)

    first_admin = test_client.create_user(user_id=910004, first_name="AdminThree", username="admin_three")
    second_admin = test_client.create_user(user_id=910005, first_name="AdminFour", username="admin_four")

    await test_client.send_message(text="init", from_user=first_admin.user, chat=group_chat)
    await test_client.send_message(text="init", from_user=second_admin.user, chat=group_chat)

    chat_model = await ChatModel.get_by_tid(group_chat.id)
    first_admin_model = await ChatModel.get_by_tid(first_admin.user.id)
    second_admin_model = await ChatModel.get_by_tid(second_admin.user.id)
    assert chat_model is not None
    assert first_admin_model is not None
    assert second_admin_model is not None

    member_three = _build_admin_member(first_admin.user, can_restrict_members=True, title="Guardian")
    member_four = _build_admin_member(second_admin.user, can_restrict_members=True, title="Guardian")

    # Build fake admin entries to work around mongomock DBRef query limitation.
    fake_admins = [
        _FakeAdminEntry(member=member_three, user=_FakeUserLink(first_admin_model)),
        _FakeAdminEntry(member=member_four, user=_FakeUserLink(second_admin_model)),
    ]

    anonymous_message = Message(
        message_id=5552,
        date=datetime.now(timezone.utc),
        chat=group_chat,
        from_user=User(
            id=TELEGRAM_ANONYMOUS_ADMIN_BOT_ID,
            is_bot=True,
            first_name="GroupAnonymousBot",
            username="GroupAnonymousBot",
        ),
        sender_chat=group_chat,
        author_signature="Guardian",
        is_topic_message=True,
        message_thread_id=91,
        text="/e2e_restrict_required",
    )

    # Patch needs to target where the object is looked up, not where it's defined.
    from sophie_bot.filters import admin_rights as admin_rights_module

    with patch.object(admin_rights_module.ChatAdminModel, "find", lambda *_a, **_kw: _FakeAdminsQuery(fake_admins)):
        requests = await _new_requests_for_update(test_client, Update(update_id=88002, message=anonymous_message))

    assert requests, "Bot should respond when anonymous admin permissions are valid."
    assert any((request.text or "") == "E2E_RESTRICT_OK" for request in requests)
