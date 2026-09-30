import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, User
from beanie import PydanticObjectId

from sophie_bot.db.models.chat import ChatModel, ChatType, UserInGroupModel
from sophie_bot.db.models.ws_user import WSUserModel
from sophie_bot.middlewares.save_chats import SaveChatsMiddleware
from sophie_bot.modules.welcomesecurity.schedules.kick_unpassed_users import KickUnpassedUsers
from sophie_bot.modules.welcomesecurity.utils_.on_new_user import ws_on_new_user_mute
from sophie_bot.services.application import ApplicationServices
from sophie_bot.shared.actions import RestrictionAction, RestrictionResult
from tests.utils.db_fixture import cleanup_beanie


@pytest.fixture(autouse=True)
async def _clean_rejoin_db(db_init: Any) -> AsyncIterator[None]:
    await cleanup_beanie()
    yield
    await cleanup_beanie()


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


async def _join_user(user: ChatModel, group: ChatModel, *, message_id: int) -> None:
    await SaveChatsMiddleware()._handle_new_chat_members(
        Message(
            message_id=message_id,
            date=datetime(2026, 9, 30, tzinfo=UTC),
            chat=Chat(id=group.tid, type="supergroup", title=group.first_name_or_title),
            new_chat_members=[User(id=user.tid, first_name=user.first_name_or_title, is_bot=False)],
        ),
        group,
    )


async def _leave_user(user: ChatModel, group: ChatModel, *, message_id: int = 2) -> None:
    await SaveChatsMiddleware()._handle_left_chat_member(
        Message(
            message_id=message_id,
            date=datetime(2026, 9, 30, tzinfo=UTC),
            chat=Chat(id=group.tid, type="supergroup", title=group.first_name_or_title),
            left_chat_member=User(id=user.tid, first_name=user.first_name_or_title, is_bot=False),
        ),
        group,
    )


@pytest.mark.asyncio
async def test_leave_then_rejoin_gets_a_fresh_welcome_security_deadline(db_init: Any) -> None:
    user, group = await _create_user_and_group(991001, -991002)
    await _join_user(user, group, message_id=1)

    pending = await WSUserModel.ensure_user(user, group, is_join_request=False)
    pending.added_at = datetime.now(UTC) - timedelta(hours=100)
    await pending.save()
    assert await WSUserModel.find_all().count() == 1

    await _leave_user(user, group)

    assert await WSUserModel.find_all().count() == 0

    await _join_user(user, group, message_id=3)
    rejoined = await WSUserModel.ensure_user(user, group, is_join_request=False)

    assert rejoined.added_at.replace(tzinfo=UTC) > datetime.now(UTC) - timedelta(minutes=1)
    await _leave_user(user, group, message_id=4)
    assert await WSUserModel.is_user(user.iid, group.iid) is None


@pytest.mark.asyncio
async def test_duplicate_join_delivery_does_not_extend_welcome_security_deadline(db_init: Any) -> None:
    user, group = await _create_user_and_group(991003, -991004)
    await _join_user(user, group, message_id=1)
    rejoined = await WSUserModel.ensure_user(user, group, is_join_request=False)
    added_at = rejoined.added_at

    await _join_user(user, group, message_id=1)
    duplicate_delivery = await WSUserModel.ensure_user(user, group, is_join_request=False)

    assert abs(duplicate_delivery.added_at.replace(tzinfo=UTC) - added_at.replace(tzinfo=UTC)) < timedelta(
        milliseconds=1
    )


@pytest.mark.asyncio
async def test_passed_user_survives_leave_and_rejoin_without_rewriting_join_request_mode(db_init: Any) -> None:
    user, group = await _create_user_and_group(991005, -991006)
    await _join_user(user, group, message_id=1)
    passed_user = await WSUserModel.ensure_user(user, group, is_join_request=True)
    passed_user.passed = True
    await passed_user.save()

    await _leave_user(user, group)
    await _join_user(user, group, message_id=3)
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
@pytest.mark.parametrize("pause_at", ["membership_lookup", "membership_delete", "pending_lookup"])
async def test_old_leave_cannot_delete_a_newer_same_second_rejoin(
    db_init: Any,
    monkeypatch: pytest.MonkeyPatch,
    pause_at: Literal["membership_lookup", "membership_delete", "pending_lookup"],
) -> None:
    user, group = await _create_user_and_group(991007, -991008)
    await _join_user(user, group, message_id=1)
    pending = await WSUserModel.ensure_user(user, group, is_join_request=False)
    pending.added_at = datetime.now(UTC) - timedelta(hours=100)
    await pending.save()

    old_leave_paused = asyncio.Event()
    allow_old_leave_to_finish = asyncio.Event()
    old_leave: asyncio.Task[None] | None = None
    original_membership_lookup = UserInGroupModel.get_user_in_group
    original_ensure_delete = UserInGroupModel.ensure_delete
    original_pending_lookup = WSUserModel.is_user

    async def pause_membership_lookup(
        user_iid: PydanticObjectId, group_iid: PydanticObjectId
    ) -> UserInGroupModel | None:
        if asyncio.current_task() is old_leave:
            old_leave_paused.set()
            await allow_old_leave_to_finish.wait()
        return await original_membership_lookup(user_iid, group_iid)

    async def pause_membership_delete(
        leave_user: ChatModel,
        leave_group: ChatModel,
        membership_id: PydanticObjectId,
        *,
        left_message_id: int,
    ) -> bool:
        old_leave_paused.set()
        await allow_old_leave_to_finish.wait()
        return await original_ensure_delete(
            leave_user, leave_group, membership_id, left_message_id=left_message_id
        )

    async def pause_pending_lookup(
        user_iid: PydanticObjectId, group_iid: PydanticObjectId
    ) -> WSUserModel | None:
        if asyncio.current_task() is old_leave:
            old_leave_paused.set()
            await allow_old_leave_to_finish.wait()
        return await original_pending_lookup(user_iid, group_iid)

    if pause_at == "membership_lookup":
        monkeypatch.setattr(UserInGroupModel, "get_user_in_group", pause_membership_lookup)
    elif pause_at == "membership_delete":
        monkeypatch.setattr(UserInGroupModel, "ensure_delete", pause_membership_delete)
    else:
        monkeypatch.setattr(WSUserModel, "is_user", pause_pending_lookup)
    old_leave = asyncio.create_task(_leave_user(user, group, message_id=2))
    await old_leave_paused.wait()

    try:
        await _join_user(user, group, message_id=3)
        rejoined = await WSUserModel.ensure_user(user, group, is_join_request=False)
        assert rejoined.added_at.replace(tzinfo=UTC) > datetime.now(UTC) - timedelta(minutes=1)
    finally:
        allow_old_leave_to_finish.set()
        await old_leave

    stored_user = await WSUserModel.is_user(user.iid, group.iid)
    assert stored_user is not None
    assert stored_user.id == rejoined.id
    assert stored_user.membership_join_message_id == 3
    assert stored_user.added_at == rejoined.added_at
    membership = await UserInGroupModel.get_user_in_group(user.iid, group.iid)
    assert membership is not None
    assert membership.joined_message_id == 3


@pytest.mark.asyncio
async def test_newer_member_message_survives_older_edit_and_delayed_leave(db_init: Any) -> None:
    user, group = await _create_user_and_group(991011, -991012)
    await _join_user(user, group, message_id=1)
    pending = await WSUserModel.ensure_user(user, group, is_join_request=False)
    message = Message(
        message_id=4,
        date=datetime(2026, 9, 30, tzinfo=UTC),
        chat=Chat(id=group.tid, type="supergroup", title=group.first_name_or_title),
        from_user=User(id=user.tid, first_name=user.first_name_or_title, is_bot=False),
        text="Still here",
    )
    await SaveChatsMiddleware.update_from_user(message, group)
    await SaveChatsMiddleware.update_from_user(message.model_copy(update={"message_id": 1}), group)

    await _leave_user(user, group, message_id=2)

    membership = await UserInGroupModel.get_user_in_group(user.iid, group.iid)
    assert membership is not None
    assert membership.last_message_id == 4
    stored_user = await WSUserModel.is_user(user.iid, group.iid)
    assert stored_user is not None
    assert stored_user.id == pending.id


@pytest.mark.parametrize("expiry_operation", ["kick", "unmute", "decline"])
@pytest.mark.asyncio
async def test_pending_rejoin_initialization_waits_for_inflight_expiry(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
    expiry_operation: Literal["kick", "unmute", "decline"],
) -> None:
    user, group = await _create_user_and_group(991013, -991014)
    if expiry_operation != "decline":
        await _join_user(user, group, message_id=1)
    pending = await WSUserModel.ensure_user(user, group, is_join_request=expiry_operation == "decline")
    pending.added_at = datetime.now(UTC) - timedelta(hours=100)
    await pending.save()

    scheduler_module = "sophie_bot.modules.welcomesecurity.schedules.kick_unpassed_users"
    new_user_module = "sophie_bot.modules.welcomesecurity.utils_.on_new_user"
    expiry_loaded = asyncio.Event()
    finish_expiry = asyncio.Event()
    initialization_waiting = asyncio.Event()
    initialization: asyncio.Task[bool] | None = None
    original_set = test_services.redis.set

    async def observe_lock_attempt(key: str, owner: str, *, nx: bool, px: int) -> bool | None:
        if asyncio.current_task() is initialization:
            initialization_waiting.set()
        return await original_set(key, owner, nx=nx, px=px)

    async def pause_expiry_after_loading_session(chat_iid: PydanticObjectId) -> SimpleNamespace:
        expiry_loaded.set()
        await finish_expiry.wait()
        return SimpleNamespace(welcome_security=SimpleNamespace(expire=None))

    async def pause_whitelist_action(group_tid: int, user_tid: int, reason: str) -> None:
        expiry_loaded.set()
        await finish_expiry.wait()

    monkeypatch.setattr(test_services.redis, "set", observe_lock_attempt)
    monkeypatch.setattr(f"{scheduler_module}.GreetingsModel.get_by_chat_iid", pause_expiry_after_loading_session)
    monkeypatch.setattr(f"{scheduler_module}.is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        f"{scheduler_module}.is_user_group_whitelisted", AsyncMock(return_value=expiry_operation == "unmute")
    )
    monkeypatch.setattr(f"{scheduler_module}.log_group_whitelist_exemption", pause_whitelist_action)
    monkeypatch.setattr(f"{new_user_module}.is_user_group_whitelisted", AsyncMock(return_value=False))
    monkeypatch.setattr(f"{new_user_module}.is_user_admin", AsyncMock(return_value=False))
    expiry_restriction = AsyncMock(return_value=RestrictionResult(action=RestrictionAction.KICK, applied=True))
    monkeypatch.setattr(f"{scheduler_module}.execute_restriction", expiry_restriction)
    decline_request = AsyncMock()
    monkeypatch.setattr(test_services.bot, "decline_chat_join_request", decline_request)
    monkeypatch.setattr(
        f"{new_user_module}.execute_restriction",
        AsyncMock(return_value=RestrictionResult(action=RestrictionAction.MUTE, applied=True)),
    )
    expiry = asyncio.create_task(KickUnpassedUsers(test_services).process_user(pending))
    lock_attempt: asyncio.Task[bool] | None = None
    try:
        await asyncio.wait_for(expiry_loaded.wait(), timeout=2)
        await _join_user(user, group, message_id=3)
        initialization = asyncio.create_task(
            ws_on_new_user_mute(user, group, bot=test_services.bot, redis=test_services.redis)
        )
        lock_attempt = asyncio.create_task(initialization_waiting.wait())
        await asyncio.wait({initialization, lock_attempt}, return_when=asyncio.FIRST_COMPLETED)
        finish_expiry.set()
        await expiry
        assert await initialization
    finally:
        finish_expiry.set()
        for operation in (expiry, initialization, lock_attempt):
            if operation is not None and not operation.done():
                operation.cancel()
        await asyncio.gather(
            *(operation for operation in (expiry, initialization, lock_attempt) if operation is not None),
            return_exceptions=True,
        )

    rejoined = await WSUserModel.is_user(user.iid, group.iid)
    assert rejoined is not None
    assert rejoined.membership_join_message_id == 3
    assert rejoined.added_at.replace(tzinfo=UTC) > datetime.now(UTC) - timedelta(minutes=1)
    expiry_restriction.assert_not_awaited()
    decline_request.assert_not_awaited()
