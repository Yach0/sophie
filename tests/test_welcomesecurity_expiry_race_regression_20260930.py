from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramAPIError
from beanie import PydanticObjectId

from sophie_bot.db.models.greetings import WELCOMESECURITY_EXPIRE_DEFAULT_TIME
from sophie_bot.modules.welcomesecurity.schedules.kick_unpassed_users import KickUnpassedUsers
from sophie_bot.modules.welcomesecurity.utils_.complete_captcha import complete_captcha
from sophie_bot.modules.welcomesecurity.utils_.pending_user_lock import (
    _pending_user_lock_key,
    _release_pending_user_lock,
    pending_user_lock,
)
from sophie_bot.services.application import ApplicationServices
from sophie_bot.shared.actions import RestrictionAction, RestrictionResult

_MODULE = "sophie_bot.modules.welcomesecurity.schedules.kick_unpassed_users"


def _make_ws_user(*, is_join_request: bool) -> SimpleNamespace:
    return SimpleNamespace(
        id=PydanticObjectId(),
        passed=False,
        is_join_request=is_join_request,
        added_at=datetime.now(UTC) - WELCOMESECURITY_EXPIRE_DEFAULT_TIME - timedelta(hours=1),
        user=SimpleNamespace(ref=SimpleNamespace(id=PydanticObjectId())),
        group=SimpleNamespace(ref=SimpleNamespace(id=PydanticObjectId())),
        delete=AsyncMock(),
    )


def _patch_expired_user(monkeypatch: pytest.MonkeyPatch, *, current_record: object) -> None:
    monkeypatch.setattr(
        f"{_MODULE}.ChatModel.get_by_iid",
        AsyncMock(
            side_effect=[
                SimpleNamespace(id=PydanticObjectId(), iid=PydanticObjectId(), tid=123),
                SimpleNamespace(id=PydanticObjectId(), iid=PydanticObjectId(), tid=-100123),
            ]
        ),
    )
    monkeypatch.setattr(f"{_MODULE}.WSUserModel.is_user", AsyncMock(return_value=current_record))
    monkeypatch.setattr(f"{_MODULE}.is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        f"{_MODULE}.GreetingsModel.get_by_chat_iid",
        AsyncMock(return_value=SimpleNamespace(welcome_security=SimpleNamespace(expire=None))),
    )
    monkeypatch.setattr(f"{_MODULE}.is_user_group_whitelisted", AsyncMock(return_value=False))


@pytest.mark.asyncio
async def test_expiry_scheduler_does_not_act_on_record_removed_by_captcha_completion(
    monkeypatch: pytest.MonkeyPatch,
    test_services: object,
) -> None:
    stale_record = _make_ws_user(is_join_request=False)
    execute_restriction = AsyncMock()
    _patch_expired_user(monkeypatch, current_record=None)
    monkeypatch.setattr(f"{_MODULE}.execute_restriction", execute_restriction)

    await KickUnpassedUsers(test_services).process_user(stale_record)

    execute_restriction.assert_not_awaited()
    stale_record.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_expiry_scheduler_keeps_record_when_kick_was_not_applied(
    monkeypatch: pytest.MonkeyPatch,
    test_services: object,
) -> None:
    ws_user = _make_ws_user(is_join_request=False)
    execute_restriction = AsyncMock(
        return_value=RestrictionResult(action=RestrictionAction.KICK, applied=False),
    )
    _patch_expired_user(monkeypatch, current_record=ws_user)
    monkeypatch.setattr(f"{_MODULE}.execute_restriction", execute_restriction)

    await KickUnpassedUsers(test_services).process_user(ws_user)

    execute_restriction.assert_awaited_once_with(test_services.bot, RestrictionAction.KICK, -100123, 123)
    ws_user.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_expiry_scheduler_keeps_join_request_when_decline_fails(
    monkeypatch: pytest.MonkeyPatch,
    test_services: object,
) -> None:
    ws_user = _make_ws_user(is_join_request=True)
    _patch_expired_user(monkeypatch, current_record=ws_user)
    monkeypatch.setattr(
        test_services.bot,
        "decline_chat_join_request",
        AsyncMock(side_effect=TelegramAPIError(method=SimpleNamespace(), message="temporary failure")),
    )

    await KickUnpassedUsers(test_services).process_user(ws_user)

    ws_user.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_captcha_pass_lifecycle_holds_lock_before_visible_actions(
    monkeypatch: pytest.MonkeyPatch,
    test_services: object,
) -> None:
    user = SimpleNamespace(iid=PydanticObjectId(), id=PydanticObjectId(), tid=123)
    group = SimpleNamespace(iid=PydanticObjectId(), id=PydanticObjectId(), tid=-100123)
    ws_user = _make_ws_user(is_join_request=False)
    current_record: list[object | None] = [ws_user]
    visible_action_started = asyncio.Event()
    allow_captcha_completion = asyncio.Event()
    execute_restriction = AsyncMock(
        return_value=RestrictionResult(action=RestrictionAction.KICK, applied=True),
    )

    monkeypatch.setattr(
        f"{_MODULE}.ChatModel.get_by_iid",
        AsyncMock(side_effect=[user, group]),
    )
    monkeypatch.setattr(f"{_MODULE}.WSUserModel.is_user", AsyncMock(side_effect=lambda *args: current_record[0]))
    monkeypatch.setattr(f"{_MODULE}.is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        f"{_MODULE}.GreetingsModel.get_by_chat_iid",
        AsyncMock(return_value=SimpleNamespace(welcome_security=SimpleNamespace(expire=None))),
    )
    monkeypatch.setattr(f"{_MODULE}.is_user_group_whitelisted", AsyncMock(return_value=False))
    monkeypatch.setattr(f"{_MODULE}.execute_restriction", execute_restriction)

    async def edit_captcha(*args: object, **kwargs: object) -> None:
        visible_action_started.set()
        await allow_captcha_completion.wait()

    monkeypatch.setattr(test_services.bot, "edit_message_media", edit_captcha)
    captcha_pass_started = asyncio.Event()

    async def finish_pass(*args: object, **kwargs: object) -> bool:
        captcha_pass_started.set()
        current_record[0] = None
        return True

    complete_module = "sophie_bot.modules.welcomesecurity.utils_.complete_captcha"
    monkeypatch.setattr(f"{complete_module}.ws_on_user_passed", finish_pass)
    captcha_task = asyncio.create_task(
        complete_captcha(
            user,
            group,
            SimpleNamespace(welcome_mute=None),
            SimpleNamespace(chat=SimpleNamespace(id=user.tid), message_id=42),
            bot=test_services.bot,
            redis=test_services.redis,
        )
    )
    await visible_action_started.wait()

    scheduler_task = asyncio.create_task(KickUnpassedUsers(test_services).process_user(ws_user))
    await asyncio.sleep(0)
    assert not execute_restriction.await_count

    allow_captcha_completion.set()
    await captcha_task
    await scheduler_task
    assert captcha_pass_started.is_set()
    execute_restriction.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_user_lock_renews_and_stale_owner_cannot_release_new_owner(
    monkeypatch: pytest.MonkeyPatch,
    test_services: object,
) -> None:
    module = "sophie_bot.modules.welcomesecurity.utils_.pending_user_lock"
    monkeypatch.setattr(f"{module}.WELCOME_SECURITY_PENDING_LOCK_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(f"{module}.WELCOME_SECURITY_PENDING_LOCK_RETRY_SECONDS", 0.005)
    group_tid, user_tid = -100123, 123
    lock_key = _pending_user_lock_key(group_tid, user_tid)

    async with pending_user_lock(group_tid, user_tid, redis=test_services.redis):
        await asyncio.sleep(0.12)
        assert await test_services.redis.get(lock_key) is not None
        await test_services.redis.set(lock_key, "new-owner")
        await _release_pending_user_lock(lock_key, "old-owner", redis=test_services.redis)
        assert await test_services.redis.get(lock_key) == b"new-owner"


@pytest.mark.asyncio
async def test_contended_pending_user_does_not_starve_later_expired_users(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    contended_user = _make_ws_user(is_join_request=False)
    expired_user = _make_ws_user(is_join_request=False)
    group = SimpleNamespace(id=contended_user.group.ref.id, iid=contended_user.group.ref.id, tid=-100123)
    users = [
        SimpleNamespace(id=contended_user.user.ref.id, iid=contended_user.user.ref.id, tid=123),
        SimpleNamespace(id=expired_user.user.ref.id, iid=expired_user.user.ref.id, tid=124),
    ]
    expired_user.group.ref.id = contended_user.group.ref.id
    linked_chats = {
        users[0].iid: users[0],
        users[1].iid: users[1],
        contended_user.group.ref.id: group,
    }

    async def pending_users() -> AsyncIterator[SimpleNamespace]:
        yield contended_user
        yield expired_user

    _patch_expired_user(monkeypatch, current_record=expired_user)
    monkeypatch.setattr(
        f"{_MODULE}.ChatModel.get_by_iid",
        AsyncMock(side_effect=lambda chat_iid: linked_chats[chat_iid]),
    )
    monkeypatch.setattr(f"{_MODULE}.WSUserModel.find", lambda *args: pending_users())
    execute_restriction = AsyncMock(
        return_value=RestrictionResult(action=RestrictionAction.KICK, applied=True),
    )
    monkeypatch.setattr(f"{_MODULE}.execute_restriction", execute_restriction)
    await test_services.redis.set(_pending_user_lock_key(group.tid, users[0].tid), "captcha-owner")
    clock = iter((0.0, 31.0, 31.0))
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.utils_.pending_user_lock.monotonic",
        lambda: next(clock),
    )

    await KickUnpassedUsers(test_services).handle()

    contended_user.delete.assert_not_awaited()
    execute_restriction.assert_awaited_once_with(
        test_services.bot, RestrictionAction.KICK, group.tid, users[1].tid
    )
    expired_user.delete.assert_awaited_once()
    assert await test_services.redis.get(_pending_user_lock_key(group.tid, users[0].tid)) == b"captcha-owner"
    assert await test_services.redis.get(_pending_user_lock_key(group.tid, users[1].tid)) is None


@pytest.mark.asyncio
async def test_expiry_action_timeout_is_not_mistaken_for_lock_contention(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    ws_user = _make_ws_user(is_join_request=False)
    _patch_expired_user(monkeypatch, current_record=ws_user)
    monkeypatch.setattr(f"{_MODULE}.execute_restriction", AsyncMock(side_effect=TimeoutError("Telegram timeout")))

    with pytest.raises(TimeoutError, match="Telegram timeout"):
        await KickUnpassedUsers(test_services).process_user(ws_user)

    ws_user.delete.assert_not_awaited()
    assert await test_services.redis.get(_pending_user_lock_key(-100123, 123)) is None
