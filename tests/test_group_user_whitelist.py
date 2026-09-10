from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from sophie_bot.db.models.group_user_whitelist import GroupUserWhitelistModel
from sophie_bot.modules.welcomesecurity.utils_ import on_user_passed
from sophie_bot.modules.whitelist.handlers import add as whitelist_add
from sophie_bot.services.application import ApplicationServices
from sophie_bot.shared.actions import RestrictionAction, RestrictionResult
from sophie_bot.utils import group_whitelist
from sophie_bot.utils.feature_flags import delete_override, set_enabled
from sophie_bot.utils.group_whitelist import (
    GROUP_USER_WHITELIST_CACHE_TTL_SECONDS,
    add_user_to_group_whitelist,
    group_user_whitelist_cache_key,
    is_user_group_whitelisted,
    migrate_group_user_whitelist_chat,
    remove_user_from_group_whitelist,
)


async def test_group_whitelist_is_ignored_when_feature_is_disabled(
    db_init: Any,
    test_services: ApplicationServices,
) -> None:
    del db_init
    chat_tid = -1_007_000_000_000
    user_tid = 700_000_000
    await GroupUserWhitelistModel.add_user(chat_tid, user_tid)

    assert await is_user_group_whitelisted(chat_tid, user_tid, redis=test_services.redis) is False


async def test_group_whitelist_add_check_remove_is_idempotent(
    db_init: Any,
    test_services: ApplicationServices,
) -> None:
    del db_init
    first_chat_tid = -1_007_000_000_001
    second_chat_tid = -1_007_000_000_002
    user_tid = 700_000_001
    await set_enabled("group_user_whitelist", True, redis=test_services.redis)

    try:
        assert await is_user_group_whitelisted(first_chat_tid, user_tid, redis=test_services.redis) is False
        assert await add_user_to_group_whitelist(first_chat_tid, user_tid, redis=test_services.redis) is True
        assert await add_user_to_group_whitelist(first_chat_tid, user_tid, redis=test_services.redis) is False
        assert await is_user_group_whitelisted(first_chat_tid, user_tid, redis=test_services.redis) is True
        assert await is_user_group_whitelisted(second_chat_tid, user_tid, redis=test_services.redis) is False

        assert await add_user_to_group_whitelist(second_chat_tid, user_tid, redis=test_services.redis) is True
        assert await remove_user_from_group_whitelist(first_chat_tid, user_tid, redis=test_services.redis) is True
        assert await remove_user_from_group_whitelist(first_chat_tid, user_tid, redis=test_services.redis) is False
        assert await is_user_group_whitelisted(first_chat_tid, user_tid, redis=test_services.redis) is False
        assert await is_user_group_whitelisted(second_chat_tid, user_tid, redis=test_services.redis) is True
    finally:
        await delete_override("group_user_whitelist", redis=test_services.redis)


async def test_group_whitelist_membership_cache_avoids_duplicate_database_reads(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    chat_tid = -1_007_000_000_003
    user_tid = 700_000_003
    find_one = AsyncMock(return_value=object())
    monkeypatch.setattr(GroupUserWhitelistModel, "find_one", find_one)
    monkeypatch.setattr(group_whitelist, "is_enabled", AsyncMock(return_value=True))

    assert await is_user_group_whitelisted(chat_tid, user_tid, redis=test_services.redis) is True
    assert await is_user_group_whitelisted(chat_tid, user_tid, redis=test_services.redis) is True
    find_one.assert_awaited_once()

    cache_key = group_user_whitelist_cache_key(chat_tid, user_tid)
    assert await test_services.redis.get(cache_key) == b"1"
    assert 0 < await test_services.redis.ttl(cache_key) <= GROUP_USER_WHITELIST_CACHE_TTL_SECONDS


async def test_group_whitelist_mutations_invalidate_membership_cache(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    chat_tid = -1_007_000_000_004
    user_tid = 700_000_004
    cache_key = group_user_whitelist_cache_key(chat_tid, user_tid)
    add_user = AsyncMock(return_value=True)
    remove_user = AsyncMock(return_value=True)
    monkeypatch.setattr(GroupUserWhitelistModel, "add_user", add_user)
    monkeypatch.setattr(GroupUserWhitelistModel, "remove_user", remove_user)

    await test_services.redis.set(cache_key, b"0")
    assert await add_user_to_group_whitelist(chat_tid, user_tid, redis=test_services.redis) is True
    assert await test_services.redis.get(cache_key) is None

    await test_services.redis.set(cache_key, b"1")
    assert await remove_user_from_group_whitelist(chat_tid, user_tid, redis=test_services.redis) is True
    assert await test_services.redis.get(cache_key) is None


async def test_mutation_started_before_migration_is_transferred(
    db_init: Any,
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    del db_init
    old_chat_tid = -1_007_000_000_009
    new_chat_tid = -1_007_000_000_010
    user_tid = 700_000_009
    mutation_started = asyncio.Event()
    release_mutation = asyncio.Event()
    original_add_user = GroupUserWhitelistModel.add_user

    async def paused_add_user(
        model: type[GroupUserWhitelistModel],
        chat_tid: int,
        added_user_tid: int,
    ) -> bool:
        del model
        if chat_tid == old_chat_tid and added_user_tid == user_tid:
            mutation_started.set()
            await release_mutation.wait()
        return await original_add_user(chat_tid, added_user_tid)

    monkeypatch.setattr(GroupUserWhitelistModel, "add_user", classmethod(paused_add_user))

    mutation_task = asyncio.create_task(add_user_to_group_whitelist(old_chat_tid, user_tid, redis=test_services.redis))
    await mutation_started.wait()
    migration_task = asyncio.create_task(
        migrate_group_user_whitelist_chat(old_chat_tid, new_chat_tid, redis=test_services.redis)
    )
    await asyncio.sleep(0)

    assert migration_task.done() is False
    release_mutation.set()
    assert await mutation_task is True
    await migration_task

    assert await GroupUserWhitelistModel.find_one({"chat_tid": old_chat_tid, "user_tid": user_tid}) is None
    assert await GroupUserWhitelistModel.find_one({"chat_tid": new_chat_tid, "user_tid": user_tid}) is not None


async def test_cache_miss_cannot_restore_stale_value_after_mutation(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    chat_tid = -1_007_000_000_005
    user_tid = 700_000_005
    cache_key = group_user_whitelist_cache_key(chat_tid, user_tid)
    lookup_started = asyncio.Event()
    release_lookup = asyncio.Event()
    mutation_waiting_for_lock = asyncio.Event()
    lock_attempts = 0
    redis_set = test_services.redis.set

    async def observed_redis_set(
        name: str,
        value: str,
        *,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool | str | bytes | None:
        nonlocal lock_attempts
        if name.endswith(f":lock:{chat_tid}:{user_tid}"):
            lock_attempts += 1
            if lock_attempts == 2:
                mutation_waiting_for_lock.set()
        return await redis_set(name, value, nx=nx, ex=ex)

    async def stale_find_one(*args: object, **kwargs: object) -> None:
        del args, kwargs
        lookup_started.set()
        await release_lookup.wait()

    add_user = AsyncMock(return_value=True)
    monkeypatch.setattr(GroupUserWhitelistModel, "find_one", stale_find_one)
    monkeypatch.setattr(GroupUserWhitelistModel, "add_user", add_user)
    monkeypatch.setattr(group_whitelist, "is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(test_services.redis, "set", observed_redis_set)

    lookup_task = asyncio.create_task(is_user_group_whitelisted(chat_tid, user_tid, redis=test_services.redis))
    await lookup_started.wait()
    mutation_task = asyncio.create_task(add_user_to_group_whitelist(chat_tid, user_tid, redis=test_services.redis))
    await mutation_waiting_for_lock.wait()

    add_user.assert_not_awaited()
    release_lookup.set()
    assert await lookup_task is False
    assert await mutation_task is True
    assert await test_services.redis.get(cache_key) is None


async def test_cache_miss_cannot_restore_present_value_after_removal(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    chat_tid = -1_007_000_000_007
    user_tid = 700_000_007
    cache_key = group_user_whitelist_cache_key(chat_tid, user_tid)
    lookup_started = asyncio.Event()
    release_lookup = asyncio.Event()

    async def present_find_one(*args: object, **kwargs: object) -> object:
        del args, kwargs
        lookup_started.set()
        await release_lookup.wait()
        return object()

    remove_user = AsyncMock(return_value=True)
    monkeypatch.setattr(GroupUserWhitelistModel, "find_one", present_find_one)
    monkeypatch.setattr(GroupUserWhitelistModel, "remove_user", remove_user)
    monkeypatch.setattr(group_whitelist, "is_enabled", AsyncMock(return_value=True))

    lookup_task = asyncio.create_task(is_user_group_whitelisted(chat_tid, user_tid, redis=test_services.redis))
    await lookup_started.wait()
    mutation_task = asyncio.create_task(remove_user_from_group_whitelist(chat_tid, user_tid, redis=test_services.redis))
    await asyncio.sleep(0)

    remove_user.assert_not_awaited()
    release_lookup.set()
    assert await lookup_task is True
    assert await mutation_task is True
    assert await test_services.redis.get(cache_key) is None


async def test_lock_release_does_not_delete_a_new_owners_lock(
    test_services: ApplicationServices,
) -> None:
    chat_tid = -1_007_000_000_006
    user_tid = 700_000_006
    lock_key = group_whitelist._group_user_whitelist_lock_key(chat_tid, user_tid)

    async with group_whitelist._group_user_whitelist_lock(lock_key, redis=test_services.redis):
        await test_services.redis.set(lock_key, b"new-owner")

    assert await test_services.redis.get(lock_key) == b"new-owner"


@pytest.mark.parametrize("unmute_succeeded", [False, True])
async def test_whitelisted_captcha_pass_removes_pending_only_after_successful_unmute(
    monkeypatch: pytest.MonkeyPatch,
    unmute_succeeded: bool,
    test_services: ApplicationServices,
) -> None:
    events: list[str] = []

    async def record_unmute(*args: object, **kwargs: object) -> RestrictionResult:
        del args, kwargs
        events.append("unmute")
        return RestrictionResult(
            action=RestrictionAction.UNMUTE,
            applied=unmute_succeeded,
        )

    async def record_removal(*args: object, **kwargs: object) -> object:
        del args, kwargs
        events.append("remove")
        return object()

    monkeypatch.setattr(on_user_passed, "is_user_admin", AsyncMock(return_value=False))
    monkeypatch.setattr(on_user_passed, "is_user_group_whitelisted", AsyncMock(return_value=True))
    log_exemption = AsyncMock()
    monkeypatch.setattr(on_user_passed, "log_group_whitelist_exemption", log_exemption)
    monkeypatch.setattr(on_user_passed, "execute_restriction", record_unmute)
    monkeypatch.setattr(on_user_passed.WSUserModel, "remove_user", record_removal)
    user = SimpleNamespace(tid=700_000_008, iid="user-iid")
    group = SimpleNamespace(tid=-1_007_000_000_008, iid="group-iid")
    welcome_mute = SimpleNamespace(enabled=True, time=None)

    assert (
        await on_user_passed.ws_on_user_passed(
            user,
            group,
            welcome_mute,
            bot=test_services.bot,
            redis=test_services.redis,
        )
        is True
    )
    log_exemption.assert_awaited_once_with(group.tid, user.tid, "welcome_security_welcome_mute")
    expected_events = ["unmute", "remove"] if unmute_succeeded else ["unmute"]
    assert events == expected_events


@pytest.mark.parametrize("welcome_mute_succeeded", [False, True])
async def test_captcha_pass_removes_pending_only_after_successful_welcome_mute(
    monkeypatch: pytest.MonkeyPatch,
    welcome_mute_succeeded: bool,
    test_services: ApplicationServices,
) -> None:
    user = SimpleNamespace(tid=700_000_012, iid="user-iid")
    group = SimpleNamespace(tid=-1_007_000_000_012, iid="group-iid")
    welcome_mute_time = timedelta(hours=1)
    welcome_mute = SimpleNamespace(enabled=True, time=welcome_mute_time)
    on_welcome_mute = AsyncMock(return_value=welcome_mute_succeeded)
    remove_user = AsyncMock()

    monkeypatch.setattr(on_user_passed, "is_user_admin", AsyncMock(return_value=False))
    monkeypatch.setattr(on_user_passed, "is_user_group_whitelisted", AsyncMock(return_value=False))
    monkeypatch.setattr(on_user_passed, "on_welcomemute", on_welcome_mute)
    monkeypatch.setattr(on_user_passed.WSUserModel, "remove_user", remove_user)

    assert (
        await on_user_passed.ws_on_user_passed(
            user,
            group,
            welcome_mute,
            bot=test_services.bot,
            redis=test_services.redis,
        )
        is True
    )
    on_welcome_mute.assert_awaited_once_with(
        group.tid,
        user.tid,
        on_time=welcome_mute_time,
        bot=test_services.bot,
        redis=test_services.redis,
    )
    if welcome_mute_succeeded:
        remove_user.assert_awaited_once_with(user.iid, group.iid)
    else:
        remove_user.assert_not_awaited()


@pytest.mark.parametrize("unmute_succeeded", [False, True])
async def test_whitelist_pending_captcha_is_removed_only_after_successful_unmute(
    monkeypatch: pytest.MonkeyPatch,
    unmute_succeeded: bool,
    test_services: ApplicationServices,
) -> None:
    group = SimpleNamespace(iid="group-iid")
    user = SimpleNamespace(iid="user-iid")
    monkeypatch.setattr(whitelist_add.ChatModel, "get_by_tid", AsyncMock(side_effect=[group, user]))
    monkeypatch.setattr(whitelist_add.WSUserModel, "is_user", AsyncMock(return_value=object()))
    execute_restriction = AsyncMock(
        return_value=RestrictionResult(
            action=RestrictionAction.UNMUTE,
            applied=unmute_succeeded,
        )
    )
    remove_user = AsyncMock()
    monkeypatch.setattr(whitelist_add, "execute_restriction", execute_restriction)
    monkeypatch.setattr(whitelist_add.WSUserModel, "remove_user", remove_user)

    await whitelist_add._release_pending_captcha_user(
        -1_007_000_000_011,
        700_000_011,
        bot=test_services.bot,
    )

    execute_restriction.assert_awaited_once_with(
        test_services.bot,
        RestrictionAction.UNMUTE,
        -1_007_000_000_011,
        700_000_011,
    )
    if unmute_succeeded:
        remove_user.assert_awaited_once_with(user.iid, group.iid)
    else:
        remove_user.assert_not_awaited()
