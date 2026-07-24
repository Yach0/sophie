from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ChatMemberStatus
from aiogram.types import ChatMemberAdministrator, ResultChatMemberUnion, User
from bson import ObjectId

from sophie_bot.config import CONFIG
from sophie_bot.constants import TELEGRAM_ANONYMOUS_ADMIN_BOT_ID
from sophie_bot.db.models.chat import ChatModel, ChatType
from sophie_bot.db.models.chat_admin import ChatAdminModel
from sophie_bot.filters.admin_rights import BotHasPermissions, UserRestricting
from sophie_bot.middlewares.connections import ChatConnection

GROUP_TID = -100987654321


@dataclass
class FakeUserLink:
    user_model: Any

    async def fetch(self) -> Any:
        return self.user_model


@dataclass
class FakeAdminEntry:
    member: Any
    user: FakeUserLink


class FakeAdminsQuery:
    def __init__(self, admin_entries: list[FakeAdminEntry]) -> None:
        self.admin_entries = admin_entries

    async def to_list(self) -> list[FakeAdminEntry]:
        return self.admin_entries


def build_group_model(tid: int = GROUP_TID) -> ChatModel:
    return ChatModel(
        tid=tid,
        type=ChatType.supergroup,
        first_name_or_title="Forum Chat",
        username=None,
        is_bot=False,
        last_saw=datetime.now(UTC),
    )


def build_user_model(tid: int, is_bot: bool = False) -> ChatModel:
    return ChatModel(
        tid=tid,
        type=ChatType.private,
        first_name_or_title=f"User {tid}",
        username=None,
        is_bot=is_bot,
        last_saw=datetime.now(UTC),
    )


def build_admin_member(tid: int, **permissions: bool) -> ChatMemberAdministrator:
    granted = {
        "can_be_edited": False,
        "is_anonymous": False,
        "can_manage_chat": True,
        "can_delete_messages": False,
        "can_manage_video_chats": False,
        "can_restrict_members": False,
        "can_promote_members": False,
        "can_change_info": False,
        "can_invite_users": False,
        "can_post_stories": False,
        "can_edit_stories": False,
        "can_delete_stories": False,
    }
    granted.update(permissions)
    return ChatMemberAdministrator(
        user=User(id=tid, is_bot=False, first_name=f"User {tid}"),
        **granted,
    )


def build_anonymous_message(author_signature: str = "Moderator") -> SimpleNamespace:
    reply_method = AsyncMock()
    return SimpleNamespace(
        from_user=SimpleNamespace(id=TELEGRAM_ANONYMOUS_ADMIN_BOT_ID, first_name="GroupAnonymousBot"),
        sender_chat=SimpleNamespace(id=GROUP_TID),
        author_signature=author_signature,
        chat=SimpleNamespace(id=GROUP_TID, type="supergroup"),
        reply=reply_method,
        answer=AsyncMock(),
    )


def build_group_message(sender_tid: int) -> SimpleNamespace:
    return SimpleNamespace(
        from_user=SimpleNamespace(id=sender_tid, first_name="Sender"),
        chat=SimpleNamespace(id=GROUP_TID, type="supergroup"),
        reply=AsyncMock(),
        answer=AsyncMock(),
    )


def build_connection(chat_model: ChatModel) -> ChatConnection:
    return ChatConnection(
        type=chat_model.type,
        is_connected=False,
        tid=chat_model.tid,
        title=chat_model.first_name_or_title,
        db_model=chat_model,
    )


@pytest.mark.asyncio
async def test_anonymous_admin_title_detection_normalizes_whitespace(
    monkeypatch: pytest.MonkeyPatch,
    db_init: Any,
) -> None:
    admin_filter = UserRestricting(can_restrict_members=True)

    message = build_anonymous_message(author_signature="  Moderator  ")
    connection = build_connection(build_group_model())

    matched_admins = [
        FakeAdminEntry(
            member=SimpleNamespace(
                status=ChatMemberStatus.ADMINISTRATOR,
                is_anonymous=True,
                custom_title="Moderator",
                can_restrict_members=True,
            ),
            user=FakeUserLink(user_model=SimpleNamespace(iid="resolved_admin_iid", tid=111111)),
        )
    ]

    monkeypatch.setattr(ChatAdminModel, "find", lambda *args, **kwargs: FakeAdminsQuery(matched_admins))

    result = await admin_filter(message, connection=connection, user_db=None)

    assert isinstance(result, dict)
    assert result["user_db"] == matched_admins[0].user.user_model
    assert message.reply.await_count == 0


@pytest.mark.asyncio
async def test_anonymous_admin_duplicate_title_all_have_permissions(
    monkeypatch: pytest.MonkeyPatch,
    db_init: Any,
) -> None:
    admin_filter = UserRestricting(can_restrict_members=True)

    message = build_anonymous_message()
    connection = build_connection(build_group_model())

    matched_admins = [
        FakeAdminEntry(
            member=SimpleNamespace(
                status=ChatMemberStatus.ADMINISTRATOR,
                is_anonymous=True,
                custom_title="Moderator",
                can_restrict_members=True,
            ),
            user=FakeUserLink(user_model=SimpleNamespace(iid="resolved_admin_iid", tid=111111)),
        ),
        FakeAdminEntry(
            member=SimpleNamespace(
                status=ChatMemberStatus.ADMINISTRATOR,
                is_anonymous=True,
                custom_title="Moderator",
                can_restrict_members=True,
            ),
            user=FakeUserLink(user_model=None),
        ),
    ]

    monkeypatch.setattr(ChatAdminModel, "find", lambda *args, **kwargs: FakeAdminsQuery(matched_admins))

    result = await admin_filter(message, connection=connection, user_db=None)

    assert isinstance(result, dict)
    assert result["user_db"] == matched_admins[0].user.user_model
    assert message.reply.await_count == 0


@pytest.mark.asyncio
async def test_anonymous_admin_duplicate_title_mixed_permissions_denied(
    monkeypatch: pytest.MonkeyPatch,
    db_init: Any,
) -> None:
    admin_filter = UserRestricting(can_restrict_members=True)

    message = build_anonymous_message()
    connection = build_connection(build_group_model())

    matched_admins = [
        FakeAdminEntry(
            member=SimpleNamespace(
                status=ChatMemberStatus.ADMINISTRATOR,
                is_anonymous=True,
                custom_title="Moderator",
                can_restrict_members=True,
            ),
            user=FakeUserLink(user_model=None),
        ),
        FakeAdminEntry(
            member=SimpleNamespace(
                status=ChatMemberStatus.ADMINISTRATOR,
                is_anonymous=True,
                custom_title="Moderator",
                can_restrict_members=False,
            ),
            user=FakeUserLink(user_model=None),
        ),
    ]

    monkeypatch.setattr(ChatAdminModel, "find", lambda *args, **kwargs: FakeAdminsQuery(matched_admins))

    with pytest.raises(SkipHandler):
        await admin_filter(message, connection=connection, user_db=None)

    assert message.reply.await_count >= 1
    first_reply_call = message.reply.await_args_list[0]
    assert "Multiple anonymous admins share this title" in first_reply_call.args[0]


class FakeAdminTable:
    """In-memory stand-in for ChatAdminModel.find_one.

    mongomock cannot resolve DBRef sub-field queries (``chat._id``), so the real
    lookup always returns None under tests.
    """

    def __init__(self) -> None:
        self.rows: dict[tuple[ObjectId, ObjectId], ChatAdminModel] = {}

    def add(self, chat: ChatModel, user: ChatModel, member: ResultChatMemberUnion) -> None:
        self.rows[(chat.iid, user.iid)] = ChatAdminModel(chat=chat, user=user, member=member)

    async def find_one(self, *args: Any, **kwargs: Any) -> ChatAdminModel | None:
        query: dict[str, Any] = {}
        for expression in args:
            query.update(expression)
        return self.rows.get((query["chat._id"], query["user._id"]))


@dataclass
class AdminRightsScenario:
    group: ChatModel
    sender: ChatModel
    bot_user: ChatModel
    admins: FakeAdminTable


@pytest.fixture
async def scenario(monkeypatch: pytest.MonkeyPatch, db_init: Any) -> AdminRightsScenario:
    """A group whose sender can restrict members, while the bot is not an admin at all."""
    await ChatModel.find_all().delete()

    group = build_group_model()
    sender = build_user_model(tid=111111)
    bot_user = build_user_model(tid=CONFIG.bot_id, is_bot=True)
    for model in (group, sender, bot_user):
        await model.save()

    admins = FakeAdminTable()
    admins.add(group, sender, build_admin_member(sender.tid, can_restrict_members=True))
    monkeypatch.setattr(ChatAdminModel, "find_one", admins.find_one)

    return AdminRightsScenario(group=group, sender=sender, bot_user=bot_user, admins=admins)


@pytest.mark.asyncio
async def test_bot_has_permissions_rejects_when_only_the_sender_is_admin(scenario: AdminRightsScenario) -> None:
    """The bot's own rights decide, never the sender's: an admin running /ban while Sophie is not
    an admin must be told the bot lacks rights."""
    message = build_group_message(sender_tid=scenario.sender.tid)

    with pytest.raises(SkipHandler):
        await BotHasPermissions(can_restrict_members=True)(
            message, connection=build_connection(scenario.group), user_db=scenario.sender
        )

    assert "I must be an administrator" in message.reply.await_args_list[0].args[0]


@pytest.mark.asyncio
async def test_bot_has_permissions_reports_the_bots_missing_permission(scenario: AdminRightsScenario) -> None:
    scenario.admins.add(
        scenario.group, scenario.bot_user, build_admin_member(scenario.bot_user.tid, can_delete_messages=True)
    )
    message = build_group_message(sender_tid=scenario.sender.tid)

    with pytest.raises(SkipHandler):
        await BotHasPermissions(can_restrict_members=True)(
            message, connection=build_connection(scenario.group), user_db=scenario.sender
        )

    reply_text = message.reply.await_args_list[0].args[0]
    assert "I don't have the following permissions" in reply_text
    assert "restrict members" in reply_text


@pytest.mark.asyncio
async def test_bot_has_permissions_passes_when_the_bot_is_privileged(scenario: AdminRightsScenario) -> None:
    """Only the bot holds the permission here, so a sender-based check would wrongly reject."""
    scenario.admins.add(
        scenario.group, scenario.sender, build_admin_member(scenario.sender.tid, can_delete_messages=True)
    )
    scenario.admins.add(
        scenario.group, scenario.bot_user, build_admin_member(scenario.bot_user.tid, can_restrict_members=True)
    )
    message = build_group_message(sender_tid=scenario.sender.tid)

    result = await BotHasPermissions(can_restrict_members=True)(
        message, connection=build_connection(scenario.group), user_db=scenario.sender
    )

    assert result is True
    assert message.reply.await_count == 0


@pytest.mark.asyncio
async def test_user_restricting_still_checks_the_sender(scenario: AdminRightsScenario) -> None:
    """The sender-facing filter keeps using the sender's rights, not the bot's."""
    message = build_group_message(sender_tid=scenario.sender.tid)

    result = await UserRestricting(can_restrict_members=True)(
        message, connection=build_connection(scenario.group), user_db=scenario.sender
    )
    assert result is True

    non_admin = build_user_model(tid=222222)
    await non_admin.save()
    other_message = build_group_message(sender_tid=non_admin.tid)

    with pytest.raises(SkipHandler):
        await UserRestricting(can_restrict_members=True)(
            other_message, connection=build_connection(scenario.group), user_db=non_admin
        )
    assert "You must be an administrator" in other_message.reply.await_args_list[0].args[0]
