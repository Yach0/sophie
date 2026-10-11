from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramAPIError
from beanie import PydanticObjectId

from sophie_bot.db.models.greetings import WELCOMESECURITY_EXPIRE_DEFAULT_TIME
from sophie_bot.modules.welcomesecurity.schedules.kick_unpassed_users import KickUnpassedUsers
from sophie_bot.services.application import ApplicationServices
from sophie_bot.shared.actions import RestrictionAction, RestrictionResult

_MODULE = "sophie_bot.modules.welcomesecurity.schedules.kick_unpassed_users"


@pytest.fixture(autouse=True)
def _untracked_membership(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f"{_MODULE}.UserInGroupModel.get_user_in_group", AsyncMock(return_value=None))


def _make_ws_user(*, is_join_request: bool) -> SimpleNamespace:
    return SimpleNamespace(
        id=PydanticObjectId(),
        passed=False,
        transition=None,
        transition_is_current=AsyncMock(return_value=True),
        finish_transition=AsyncMock(),
        is_join_request=is_join_request,
        membership_id=None,
        membership_join_message_id=None,
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
    if current_record is not None:
        current_record.claim_transition = AsyncMock(return_value=current_record)
        current_record.transition = "expiring"
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

    execute_restriction.assert_awaited_once_with(
        test_services.bot, RestrictionAction.KICK, -100123, 123, is_current=ws_user.transition_is_current
    )
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
