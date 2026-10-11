from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from beanie import PydanticObjectId

from sophie_bot.db.models.chat import UserInGroupModel
from sophie_bot.db.models.greetings import WelcomeMute
from sophie_bot.db.models.ws_user import WSUserModel
from sophie_bot.modules.restrictions.utils import restrictions as restriction_module
from sophie_bot.modules.restrictions.utils.restrictions import execute_restriction
from sophie_bot.modules.welcomesecurity.schedules.kick_unpassed_users import KickUnpassedUsers
from sophie_bot.modules.welcomesecurity.utils_.complete_captcha import complete_captcha
from sophie_bot.modules.welcomesecurity.utils_.on_new_user import ws_on_new_user, ws_on_new_user_mute
from sophie_bot.modules.welcomesecurity.utils_.on_user_passed import ws_on_user_passed
from sophie_bot.services.application import ApplicationServices
from sophie_bot.shared.actions import RestrictionAction, RestrictionResult
from tests.test_restrictions_service import make_member
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


@pytest.mark.asyncio
@pytest.mark.parametrize("transition", ["completing", "expiring", "exempting"])
async def test_join_transition_is_not_an_exemption(
    monkeypatch: pytest.MonkeyPatch, test_services: ApplicationServices, transition: str
) -> None:
    user, group = await _create_user_and_group(882001, -882002)
    pending = await WSUserModel.ensure_user(user, group, True)
    await pending.claim_transition(transition)
    module = "sophie_bot.modules.welcomesecurity.utils_.on_new_user"
    monkeypatch.setattr(f"{module}.is_user_admin", AsyncMock(return_value=False))
    monkeypatch.setattr(f"{module}.is_user_group_whitelisted", AsyncMock(return_value=False))
    assert await ws_on_new_user(user, group, True, redis=test_services.redis) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("action", [RestrictionAction.MUTE, RestrictionAction.UNMUTE])
async def test_session_fence_runs_after_telegram_lookups(
    action: RestrictionAction, test_services: ApplicationServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, group = await _create_user_and_group(882003, -882004)
    await _join_user(user, group, message_id=1)
    pending = await WSUserModel.ensure_user(user, group, False)
    bot = AsyncMock()
    bot.get_chat_member.return_value = make_member(True)
    bot.restrict_chat_member.return_value = True
    await execute_restriction(bot, RestrictionAction.MUTE, group.tid, user.tid)
    if action == RestrictionAction.UNMUTE:
        pending = await pending.claim_transition("completing")
        assert pending is not None
        bot.get_chat_member.return_value = make_member(False)

    async def invalidate(*args: object, **kwargs: object) -> object:
        await _join_user(user, group, message_id=3)
        await WSUserModel.ensure_user(user, group, False)
        return bot.get_chat_member.return_value

    bot.get_chat_member.side_effect = invalidate
    bot.restrict_chat_member.reset_mock()
    result = await execute_restriction(bot, action, group.tid, user.tid, is_current=pending.transition_is_current)
    assert not result.applied
    bot.restrict_chat_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_pending_insert_recovers_duplicate_key(monkeypatch: pytest.MonkeyPatch) -> None:
    user, group = await _create_user_and_group(882005, -882006)
    collection = WSUserModel.get_pymongo_collection()
    await collection.create_index([("user", 1), ("group", 1)], unique=True, name="ws_user_group_unique")
    original_insert = WSUserModel.insert
    both_inserting = asyncio.Event()
    attempts = 0

    async def concurrent_insert(document: WSUserModel, **kwargs: object) -> WSUserModel:
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            both_inserting.set()
        await asyncio.wait_for(both_inserting.wait(), timeout=2)
        return await original_insert(document, **kwargs)

    monkeypatch.setattr(WSUserModel, "insert", concurrent_insert)
    first, second = await asyncio.gather(
        WSUserModel.ensure_user(user, group, False), WSUserModel.ensure_user(user, group, False)
    )
    assert first.id == second.id
    assert await WSUserModel.count() == 1


@pytest.mark.asyncio
async def test_delayed_initial_mute_after_completion_is_fenced(
    monkeypatch: pytest.MonkeyPatch, test_services: ApplicationServices
) -> None:
    user, group = await _create_user_and_group(882007, -882008)
    module = "sophie_bot.modules.welcomesecurity.utils_.on_new_user"
    monkeypatch.setattr(f"{module}.is_user_admin", AsyncMock(return_value=False))
    monkeypatch.setattr(f"{module}.is_user_group_whitelisted", AsyncMock(return_value=False))

    async def delayed_mute(*args: object, **kwargs: object) -> RestrictionResult:
        pending = await WSUserModel.is_user(user.iid, group.iid)
        assert pending is not None
        completing = await pending.claim_transition("completing")
        assert completing is not None
        await completing.finish_transition()
        fence = kwargs.get("is_current")
        assert fence is not None
        assert not await fence()
        return RestrictionResult(action=RestrictionAction.MUTE, applied=False)

    monkeypatch.setattr(f"{module}.execute_restriction", delayed_mute)
    assert not await ws_on_new_user_mute(user, group, bot=test_services.bot, redis=test_services.redis)


@pytest.mark.asyncio
async def test_completion_passes_session_fence_after_rejoin(
    monkeypatch: pytest.MonkeyPatch, test_services: ApplicationServices
) -> None:
    user, group = await _create_user_and_group(882009, -882010)
    await _join_user(user, group, message_id=1)
    pending = await WSUserModel.ensure_user(user, group, False)
    claimed = await pending.claim_transition("completing")
    module = "sophie_bot.modules.welcomesecurity.utils_.on_user_passed"

    async def rejoin_during_admin_lookup(**kwargs: object) -> bool:
        await _join_user(user, group, message_id=3)
        return False

    monkeypatch.setattr(f"{module}.is_user_admin", rejoin_during_admin_lookup)
    monkeypatch.setattr(f"{module}.is_user_group_whitelisted", AsyncMock(return_value=False))

    async def fenced_unmute(*args: object, **kwargs: object) -> RestrictionResult:
        fence = kwargs.get("is_current")
        assert fence is not None
        assert not await fence()
        return RestrictionResult(action=RestrictionAction.UNMUTE, applied=False)

    monkeypatch.setattr(f"{module}.execute_restriction", fenced_unmute)
    assert not await ws_on_user_passed(
        user, group, WelcomeMute(), pending=claimed, bot=test_services.bot, redis=test_services.redis
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("exempt", [False, True])
async def test_scheduler_fences_rejoin_inside_restriction_service(
    monkeypatch: pytest.MonkeyPatch, test_services: ApplicationServices, exempt: bool
) -> None:
    user, group = await _create_user_and_group(883001, -883002)
    await _join_user(user, group, message_id=1)
    pending = await WSUserModel.ensure_user(user, group, False)
    pending.added_at = datetime.now(UTC) - timedelta(hours=100)
    await pending.save()
    module = "sophie_bot.modules.welcomesecurity.schedules.kick_unpassed_users"
    monkeypatch.setattr(f"{module}.is_user_group_whitelisted", AsyncMock(return_value=exempt))
    monkeypatch.setattr(f"{module}.is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        f"{module}.GreetingsModel.get_by_chat_iid", AsyncMock(return_value=SimpleNamespace(welcome_security=None))
    )

    bot = AsyncMock()
    bot.get_chat_member.return_value = make_member(True)
    bot.restrict_chat_member.return_value = True
    if exempt:
        result = await execute_restriction(bot, RestrictionAction.MUTE, group.tid, user.tid)
        assert result.applied
        bot.get_chat_member.return_value = make_member(False)
        bot.restrict_chat_member.reset_mock()
    original_lock = restriction_module._mute_lock

    @asynccontextmanager
    async def delayed_lock(chat_tid: int, user_tid: int) -> AsyncIterator[None]:
        # Membership can change while waiting for the shared Mongo operation lease.
        await _join_user(user, group, message_id=3)
        await WSUserModel.ensure_user(user, group, False)
        async with original_lock(chat_tid, user_tid):
            yield

    monkeypatch.setattr(restriction_module, "_mute_lock", delayed_lock)
    services = SimpleNamespace(bot=bot, redis=test_services.redis)
    await KickUnpassedUsers(services).process_user(pending)
    bot.unban_chat_member.assert_not_awaited()
    bot.restrict_chat_member.assert_not_awaited()
    stored = await WSUserModel.get(pending.id)
    assert stored is not None and stored.transition is None


@pytest.mark.asyncio
@pytest.mark.parametrize("invalidate_at", ["media", "marker"])
async def test_completion_rechecks_before_join_bypass_and_approval(
    monkeypatch: pytest.MonkeyPatch, test_services: ApplicationServices, invalidate_at: str
) -> None:
    user, group = await _create_user_and_group(883003, -883004)
    pending = await WSUserModel.ensure_user(user, group, True)
    marker_key = f"chat_ws_join_request:{group.iid}:{user.iid}"
    original_set = test_services.redis.set

    async def replace_session() -> None:
        await pending.delete()
        await _join_user(user, group, message_id=3)
        await WSUserModel.ensure_user(user, group, True)

    async def edit_media(**kwargs: object) -> None:
        if invalidate_at == "media":
            await replace_session()

    async def set_marker(*args: object, **kwargs: object) -> object:
        result = await original_set(*args, **kwargs)
        if args[0] == marker_key and invalidate_at == "marker":
            await replace_session()
        return result

    monkeypatch.setattr(test_services.bot, "edit_message_media", edit_media)
    monkeypatch.setattr(test_services.redis, "set", set_marker)
    approval = AsyncMock()
    monkeypatch.setattr(test_services.bot, "approve_chat_join_request", approval)
    await complete_captcha(
        user,
        group,
        SimpleNamespace(welcome_mute=None),
        SimpleNamespace(chat=SimpleNamespace(id=user.tid), message_id=42),
        bot=test_services.bot,
        redis=test_services.redis,
    )
    approval.assert_not_awaited()
    if invalidate_at == "media":
        assert await test_services.redis.get(marker_key) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("retry", [False, True])
@pytest.mark.parametrize("previous_join", [False, True])
async def test_approval_join_event_preserves_completion_and_retry(
    monkeypatch: pytest.MonkeyPatch, test_services: ApplicationServices, retry: bool, previous_join: bool
) -> None:
    user, group = await _create_user_and_group(883005, -883006)
    if previous_join:
        await _join_user(user, group, message_id=1)
    pending = await WSUserModel.ensure_user(user, group, True)
    module = "sophie_bot.modules.welcomesecurity.utils_.on_user_passed"
    monkeypatch.setattr(f"{module}.is_user_admin", AsyncMock(return_value=False))
    monkeypatch.setattr(f"{module}.is_user_group_whitelisted", AsyncMock(return_value=False))
    unmute = AsyncMock(
        side_effect=[
            RestrictionResult(action=RestrictionAction.UNMUTE, applied=not retry),
            RestrictionResult(action=RestrictionAction.UNMUTE, applied=True),
        ]
    )
    monkeypatch.setattr(f"{module}.execute_restriction", unmute)
    monkeypatch.setattr(test_services.bot, "edit_message_media", AsyncMock())

    async def approve(**kwargs: object) -> bool:
        await _join_user(user, group, message_id=3)
        return True

    monkeypatch.setattr(test_services.bot, "approve_chat_join_request", approve)
    for attempt in range(2 if retry else 1):
        await complete_captcha(
            user,
            group,
            SimpleNamespace(welcome_mute=None),
            SimpleNamespace(chat=SimpleNamespace(id=user.tid), message_id=42),
            bot=test_services.bot,
            redis=test_services.redis,
        )
        if retry and attempt == 0:
            stored = await WSUserModel.get(pending.id)
            assert stored is not None and stored.transition == "completing"
            assert await stored.transition_is_current()
    assert unmute.await_count == (2 if retry else 1)
    assert await WSUserModel.get(pending.id) is None


@pytest.mark.asyncio
async def test_admission_handoff_does_not_follow_a_second_join() -> None:
    user, group = await _create_user_and_group(883007, -883008)
    pending = await WSUserModel.ensure_user(user, group, True)
    claimed = await pending.claim_transition("completing")
    assert claimed is not None
    await _join_user(user, group, message_id=1)
    assert await claimed.transition_is_current()
    admitted = await WSUserModel.ensure_user(user, group, False)
    assert admitted.transition == "completing"
    assert admitted.added_at == claimed.added_at
    await _join_user(user, group, message_id=3)
    assert not await claimed.transition_is_current()
    rejoined = await WSUserModel.ensure_user(user, group, False)
    assert rejoined.transition is None
    assert not await claimed.finish_transition()


@pytest.mark.asyncio
async def test_admission_handoff_recovers_interrupted_membership_write(monkeypatch: pytest.MonkeyPatch) -> None:
    user, group = await _create_user_and_group(883009, -883010)
    pending = await WSUserModel.ensure_user(user, group, True)
    claimed = await pending.claim_transition("completing")
    assert claimed is not None
    original = UserInGroupModel.ensure_user_in_group

    async def fail_join(*args: object, **kwargs: object) -> UserInGroupModel:
        if kwargs.get("is_join"):
            raise TimeoutError("interrupted before membership update")
        return await original(*args, **kwargs)

    monkeypatch.setattr(UserInGroupModel, "ensure_user_in_group", fail_join)
    with pytest.raises(TimeoutError):
        await _join_user(user, group, message_id=1)
    assert not await claimed.transition_is_current()
    monkeypatch.setattr(UserInGroupModel, "ensure_user_in_group", original)
    await _join_user(user, group, message_id=1)
    assert await claimed.transition_is_current()
    assert await claimed.finish_transition()


@pytest.mark.asyncio
async def test_old_request_fence_cannot_follow_new_completion_with_same_timestamp() -> None:
    user, group = await _create_user_and_group(883011, -883012)
    pending = await WSUserModel.ensure_user(user, group, True)
    claimed = await pending.claim_transition("completing")
    assert claimed is not None
    await _join_user(user, group, message_id=1)
    await _join_user(user, group, message_id=3)
    rejoined = await WSUserModel.ensure_user(user, group, False)
    # Mongo stores milliseconds: a new claim may share the old wall-clock value.
    rejoined.added_at = claimed.added_at
    await rejoined.save()
    assert await rejoined.claim_transition("completing") is not None
    assert not await claimed.transition_is_current()
