from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from beanie import PydanticObjectId

from sophie_bot.db.models.ws_user import WSUserModel
from sophie_bot.modules.welcomesecurity.schedules.kick_unpassed_users import KickUnpassedUsers
from sophie_bot.modules.welcomesecurity.utils_.complete_captcha import complete_captcha
from sophie_bot.modules.welcomesecurity.utils_.on_new_user import ws_on_new_user_mute
from sophie_bot.services.application import ApplicationServices
from sophie_bot.shared.actions import RestrictionAction, RestrictionResult
from tests.test_welcomesecurity_rejoin import _create_user_and_group, _join_user
from tests.utils.db_fixture import cleanup_beanie


@pytest.fixture(autouse=True)
async def clean_pending_db(db_init: object) -> AsyncIterator[None]:
    await cleanup_beanie()
    yield
    await cleanup_beanie()


@pytest.mark.asyncio
async def test_completion_and_expiry_have_one_durable_winner() -> None:
    user, group = await _create_user_and_group(881001, -881002)
    pending = await WSUserModel.ensure_user(user, group, False)
    completed, expired = await asyncio.gather(
        pending.claim_transition("completing"), pending.claim_transition("expiring")
    )
    assert (completed is None) != (expired is None)
    stored = await WSUserModel.get(pending.id)
    assert stored is not None
    assert stored.transition == ("completing" if completed else "expiring")
    # Reloading after a process crash retains the decision for same-operation retry.
    opposite = "expiring" if completed else "completing"
    assert await stored.claim_transition(opposite) is None


@pytest.mark.asyncio
async def test_old_claim_cannot_finish_or_claim_rejoined_session() -> None:
    user, group = await _create_user_and_group(881003, -881004)
    await _join_user(user, group, message_id=1)
    pending = await WSUserModel.ensure_user(user, group, False)
    claimed = await pending.claim_transition("expiring")
    assert claimed is not None
    await _join_user(user, group, message_id=3)
    rejoined = await WSUserModel.ensure_user(user, group, False)
    assert rejoined.transition is None
    assert not await claimed.finish_transition()
    assert await claimed.claim_transition("expiring") is None
    assert await WSUserModel.get(rejoined.id) is not None


@pytest.mark.asyncio
async def test_legacy_document_can_be_claimed_and_failed_action_keeps_decision() -> None:
    user, group = await _create_user_and_group(881005, -881006)
    pending = await WSUserModel.ensure_user(user, group, True)
    await WSUserModel.get_pymongo_collection().update_one({"_id": pending.id}, {"$unset": {"transition": 1}})
    claimed = await pending.claim_transition("expiring")
    assert claimed is not None
    # No finish on a failed Telegram action: the next sweep retries expiry only.
    stored = await WSUserModel.get(pending.id)
    assert stored is not None
    assert stored.transition == "expiring"
    assert await stored.claim_transition("completing") is None
    assert await stored.finish_transition()


@pytest.mark.asyncio
async def test_duplicate_initialization_does_not_remute_completion(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    user, group = await _create_user_and_group(881007, -881008)
    pending = await WSUserModel.ensure_user(user, group, False)
    assert await pending.claim_transition("completing") is not None
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.utils_.on_new_user.is_user_admin", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        "sophie_bot.modules.welcomesecurity.utils_.on_new_user.is_user_group_whitelisted", AsyncMock(return_value=False)
    )
    mute = AsyncMock(return_value=RestrictionResult(action=RestrictionAction.MUTE, applied=True))
    monkeypatch.setattr("sophie_bot.modules.welcomesecurity.utils_.on_new_user.execute_restriction", mute)
    assert not await ws_on_new_user_mute(user, group, bot=test_services.bot, redis=test_services.redis)
    mute.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_whitelist_unmute_is_retried_by_scheduler(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    user, group = await _create_user_and_group(881009, -881010)
    pending = await WSUserModel.ensure_user(user, group, False)
    module = "sophie_bot.modules.welcomesecurity.schedules.kick_unpassed_users"
    monkeypatch.setattr(f"{module}.is_user_group_whitelisted", AsyncMock(return_value=True))
    unmute = AsyncMock(
        side_effect=[
            RestrictionResult(action=RestrictionAction.UNMUTE, applied=False),
            RestrictionResult(action=RestrictionAction.UNMUTE, applied=True),
        ]
    )
    monkeypatch.setattr(f"{module}.execute_restriction", unmute)
    scheduler = KickUnpassedUsers(test_services)
    await scheduler.process_user(pending)
    stored = await WSUserModel.get(pending.id)
    assert stored is not None and stored.transition == "exempting"
    monkeypatch.setattr(f"{module}.is_user_group_whitelisted", AsyncMock(return_value=False))
    await scheduler.process_user(stored)
    assert unmute.await_count == 2
    assert await WSUserModel.get(pending.id) is None


@pytest.mark.asyncio
async def test_completion_claim_precedes_visible_action_and_blocks_expiry(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    user, group = await _create_user_and_group(881011, -881012)
    pending = await WSUserModel.ensure_user(user, group, False)
    pending.added_at = datetime.now(UTC) - timedelta(hours=100)
    await pending.save()
    visible_action = asyncio.Event()
    finish_completion = asyncio.Event()

    async def pause_media(*args: object, **kwargs: object) -> None:
        visible_action.set()
        await finish_completion.wait()

    monkeypatch.setattr(test_services.bot, "edit_message_media", pause_media)
    module = "sophie_bot.modules.welcomesecurity.utils_.on_user_passed"
    monkeypatch.setattr(f"{module}.is_user_admin", AsyncMock(return_value=False))
    monkeypatch.setattr(f"{module}.is_user_group_whitelisted", AsyncMock(return_value=False))
    monkeypatch.setattr(
        f"{module}.execute_restriction",
        AsyncMock(return_value=RestrictionResult(action=RestrictionAction.UNMUTE, applied=True)),
    )
    kick = AsyncMock()
    monkeypatch.setattr("sophie_bot.modules.welcomesecurity.schedules.kick_unpassed_users.execute_restriction", kick)
    completion = asyncio.create_task(
        complete_captcha(
            user,
            group,
            SimpleNamespace(welcome_mute=None),
            SimpleNamespace(chat=SimpleNamespace(id=user.tid), message_id=42),
            bot=test_services.bot,
            redis=test_services.redis,
        )
    )
    try:
        await asyncio.wait_for(visible_action.wait(), timeout=2)
        await KickUnpassedUsers(test_services).process_user(pending)
        kick.assert_not_awaited()
    finally:
        finish_completion.set()
        await completion
    assert await WSUserModel.get(pending.id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("join_request", [False, True])
async def test_failed_expiry_retries_the_same_durable_decision(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
    join_request: bool,
) -> None:
    user, group = await _create_user_and_group(881013, -881014)
    pending = await WSUserModel.ensure_user(user, group, join_request)
    pending.added_at = datetime.now(UTC) - timedelta(hours=100)
    await pending.save()
    module = "sophie_bot.modules.welcomesecurity.schedules.kick_unpassed_users"
    monkeypatch.setattr(f"{module}.is_user_group_whitelisted", AsyncMock(return_value=False))
    monkeypatch.setattr(f"{module}.is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        f"{module}.GreetingsModel.get_by_chat_iid", AsyncMock(return_value=SimpleNamespace(welcome_security=None))
    )
    action = AsyncMock(
        side_effect=[
            RestrictionResult(action=RestrictionAction.KICK, applied=False),
            RestrictionResult(action=RestrictionAction.KICK, applied=True),
        ]
    )
    decline = AsyncMock(side_effect=[TelegramAPIError(method=SimpleNamespace(), message="temporary failure"), True])
    monkeypatch.setattr(f"{module}.execute_restriction", action)
    monkeypatch.setattr(test_services.bot, "decline_chat_join_request", decline)
    scheduler = KickUnpassedUsers(test_services)
    await scheduler.process_user(pending)
    stored = await WSUserModel.get(pending.id)
    assert stored is not None and stored.transition == "expiring"
    assert await stored.claim_transition("completing") is None
    monkeypatch.setattr(f"{module}.is_user_group_whitelisted", AsyncMock(return_value=True))
    await scheduler.process_user(stored)
    assert await WSUserModel.get(pending.id) is None
    assert (decline if join_request else action).await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("expiry_operation", ["kick", "unmute", "decline"])
async def test_rejoin_during_expiry_preparation_invalidates_old_action(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
    expiry_operation: str,
) -> None:
    user, group = await _create_user_and_group(881015, -881016)
    await _join_user(user, group, message_id=1)
    pending = await WSUserModel.ensure_user(user, group, expiry_operation == "decline")
    pending.added_at = datetime.now(UTC) - timedelta(hours=100)
    await pending.save()
    loaded = asyncio.Event()
    resume = asyncio.Event()

    async def pause_greetings(chat_iid: PydanticObjectId) -> SimpleNamespace:
        loaded.set()
        await resume.wait()
        return SimpleNamespace(welcome_security=None)

    async def pause_exemption(group_tid: int, user_tid: int, reason: str) -> None:
        loaded.set()
        await resume.wait()

    module = "sophie_bot.modules.welcomesecurity.schedules.kick_unpassed_users"
    monkeypatch.setattr(f"{module}.GreetingsModel.get_by_chat_iid", pause_greetings)
    monkeypatch.setattr(f"{module}.is_user_group_whitelisted", AsyncMock(return_value=expiry_operation == "unmute"))
    monkeypatch.setattr(f"{module}.is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(f"{module}.log_group_whitelist_exemption", pause_exemption)
    decline = AsyncMock()
    monkeypatch.setattr(test_services.bot, "decline_chat_join_request", decline)
    kick = AsyncMock()
    monkeypatch.setattr(f"{module}.execute_restriction", kick)
    expiry = asyncio.create_task(KickUnpassedUsers(test_services).process_user(pending))
    try:
        await asyncio.wait_for(loaded.wait(), timeout=2)
        await _join_user(user, group, message_id=3)
        rejoined = await WSUserModel.ensure_user(user, group, False)
    finally:
        resume.set()
        await expiry
    kick.assert_not_awaited()
    decline.assert_not_awaited()
    stored = await WSUserModel.get(pending.id)
    assert stored is not None and stored.membership_join_message_id == 3
    assert stored.added_at == rejoined.added_at


@pytest.mark.asyncio
async def test_completion_retries_after_captcha_image_was_already_updated(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    user, group = await _create_user_and_group(881017, -881018)
    pending = await WSUserModel.ensure_user(user, group, True)
    module = "sophie_bot.modules.welcomesecurity.utils_.on_user_passed"
    monkeypatch.setattr(f"{module}.is_user_admin", AsyncMock(return_value=False))
    monkeypatch.setattr(f"{module}.is_user_group_whitelisted", AsyncMock(return_value=False))
    unmute = AsyncMock(
        side_effect=[
            RestrictionResult(action=RestrictionAction.UNMUTE, applied=False),
            RestrictionResult(action=RestrictionAction.UNMUTE, applied=True),
        ]
    )
    monkeypatch.setattr(f"{module}.execute_restriction", unmute)
    monkeypatch.setattr(
        test_services.bot,
        "edit_message_media",
        AsyncMock(
            side_effect=[
                True,
                TelegramBadRequest(method=SimpleNamespace(), message="message is not modified"),
            ]
        ),
    )
    approval = AsyncMock()
    monkeypatch.setattr(test_services.bot, "approve_chat_join_request", approval)
    for attempt in range(2):
        await complete_captcha(
            user,
            group,
            SimpleNamespace(welcome_mute=None),
            SimpleNamespace(chat=SimpleNamespace(id=user.tid), message_id=42),
            bot=test_services.bot,
            redis=test_services.redis,
        )
        if attempt == 0:
            stored = await WSUserModel.get(pending.id)
            assert stored is not None and stored.transition == "completing"
    assert unmute.await_count == 2
    assert approval.await_count == 2
    assert await WSUserModel.get(pending.id) is None
