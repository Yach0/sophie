import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from aiogram.types import Chat, Message, User
from beanie import PydanticObjectId

from sophie_bot.db.models.chat import ChatModel, ChatType, UserInGroupModel
from sophie_bot.db.models.ws_user import WSUserModel
from sophie_bot.middlewares.save_chats import SaveChatsMiddleware


async def _create_user_and_group(user_tid: int, group_tid: int) -> tuple[ChatModel, ChatModel]:
    user = ChatModel(
        tid=user_tid,
        type=ChatType.private,
        first_name_or_title="Rejoining user",
        username=None,
        is_bot=False,
        last_saw=datetime.now(UTC),
    )
    group = ChatModel(
        tid=group_tid,
        type=ChatType.supergroup,
        first_name_or_title="Rejoining group",
        username=None,
        is_bot=False,
        last_saw=datetime.now(UTC),
    )
    await user.insert()
    await group.insert()
    user.iid = user.id
    group.iid = group.id
    await user.save()
    await group.save()
    return user, group


async def _leave_user(user: ChatModel, group: ChatModel) -> None:
    await SaveChatsMiddleware()._handle_left_chat_member(
        Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(id=group.tid, type="supergroup", title=group.first_name_or_title),
            left_chat_member=User(id=user.tid, first_name=user.first_name_or_title, is_bot=False),
        ),
        group,
    )


@pytest.mark.asyncio
async def test_leave_then_rejoin_gets_a_fresh_welcome_security_deadline(db_init: Any) -> None:
    user, group = await _create_user_and_group(991001, -991002)
    await UserInGroupModel.ensure_user_in_group(user, group)

    pending = await WSUserModel.ensure_user(user, group, is_join_request=False)
    pending.added_at = datetime.now(UTC) - timedelta(hours=100)
    await pending.save()
    assert await WSUserModel.find_all().count() == 1

    await _leave_user(user, group)

    assert await WSUserModel.find_all().count() == 0

    await UserInGroupModel.ensure_user_in_group(user, group)
    rejoined = await WSUserModel.ensure_user(user, group, is_join_request=False)

    assert rejoined.added_at.replace(tzinfo=UTC) > datetime.now(UTC) - timedelta(minutes=1)
    await _leave_user(user, group)
    assert await WSUserModel.is_user(user.iid, group.iid) is None


@pytest.mark.asyncio
async def test_duplicate_join_delivery_does_not_extend_welcome_security_deadline(db_init: Any) -> None:
    user, group = await _create_user_and_group(991003, -991004)
    await UserInGroupModel.ensure_user_in_group(user, group)
    rejoined = await WSUserModel.ensure_user(user, group, is_join_request=False)
    added_at = rejoined.added_at

    duplicate_delivery = await WSUserModel.ensure_user(user, group, is_join_request=False)

    assert abs(duplicate_delivery.added_at.replace(tzinfo=UTC) - added_at.replace(tzinfo=UTC)) < timedelta(
        milliseconds=1
    )


@pytest.mark.asyncio
async def test_passed_user_survives_leave_and_rejoin_without_rewriting_join_request_mode(db_init: Any) -> None:
    user, group = await _create_user_and_group(991005, -991006)
    await UserInGroupModel.ensure_user_in_group(user, group)
    passed_user = await WSUserModel.ensure_user(user, group, is_join_request=True)
    passed_user.passed = True
    await passed_user.save()

    await _leave_user(user, group)
    await UserInGroupModel.ensure_user_in_group(user, group)
    rejoined = await WSUserModel.ensure_user(user, group, is_join_request=False)

    stored_user = await WSUserModel.is_user(user.iid, group.iid)
    assert stored_user is not None
    assert stored_user.passed is True
    assert rejoined.passed is True
    assert rejoined.is_join_request is True


@pytest.mark.asyncio
async def test_legacy_membership_generation_is_bound_on_rejoin(db_init: Any) -> None:
    user, group = await _create_user_and_group(991009, -991010)
    membership = await UserInGroupModel.ensure_user_in_group(user, group)
    legacy_user = await WSUserModel(
        user=user,
        group=group,
        is_join_request=True,
        added_at=datetime.now(UTC) - timedelta(hours=100),
    ).insert()
    await WSUserModel.get_pymongo_collection().update_one({"_id": legacy_user.id}, {"$unset": {"membership_id": 1}})

    rejoined = await WSUserModel.ensure_user(user, group, is_join_request=False)

    assert rejoined.membership_id == membership.id
    assert rejoined.is_join_request is False
    assert rejoined.added_at.replace(tzinfo=UTC) > datetime.now(UTC) - timedelta(minutes=1)


@pytest.mark.asyncio
async def test_old_leave_cannot_delete_session_created_after_membership_boundary(
    db_init: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, group = await _create_user_and_group(991007, -991008)
    old_membership = await UserInGroupModel.ensure_user_in_group(user, group)
    pending = await WSUserModel.ensure_user(user, group, is_join_request=False)
    pending.added_at = datetime.now(UTC) - timedelta(hours=100)
    await pending.save()

    membership_captured = asyncio.Event()
    allow_old_leave_to_finish = asyncio.Event()
    original_ensure_delete = UserInGroupModel.ensure_delete

    async def pause_old_leave(leave_user: ChatModel, leave_group: ChatModel, membership_id: PydanticObjectId) -> bool:
        assert membership_id == old_membership.id
        membership_captured.set()
        await allow_old_leave_to_finish.wait()
        return await original_ensure_delete(leave_user, leave_group, membership_id)

    monkeypatch.setattr(UserInGroupModel, "ensure_delete", pause_old_leave)
    old_leave = asyncio.create_task(_leave_user(user, group))
    await membership_captured.wait()

    await old_membership.delete()
    await UserInGroupModel.ensure_user_in_group(user, group)
    rejoined = await WSUserModel.ensure_user(user, group, is_join_request=False)
    assert rejoined.added_at.replace(tzinfo=UTC) > datetime.now(UTC) - timedelta(minutes=1)

    allow_old_leave_to_finish.set()
    await old_leave

    stored_user = await WSUserModel.is_user(user.iid, group.iid)
    assert stored_user is not None
    assert stored_user.id == rejoined.id
    assert await UserInGroupModel.get_user_in_group(user.iid, group.iid) is not None
