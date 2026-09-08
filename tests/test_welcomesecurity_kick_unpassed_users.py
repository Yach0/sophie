from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from sophie_bot.constants import WELCOMESECURITY_KICK_TIMEOUT_HOURS
from sophie_bot.modules.welcomesecurity.schedules.kick_unpassed_users import KickUnpassedUsers
from sophie_bot.shared.actions import RestrictionAction, RestrictionResult

_MODULE = "sophie_bot.modules.welcomesecurity.schedules.kick_unpassed_users"


def _make_ws_user(*, is_join_request: bool, age_hours: float) -> SimpleNamespace:
    return SimpleNamespace(
        id=PydanticObjectId(),
        passed=False,
        is_join_request=is_join_request,
        added_at=datetime.now(UTC) - timedelta(hours=age_hours),
        user=SimpleNamespace(ref=SimpleNamespace(id=PydanticObjectId())),
        group=SimpleNamespace(ref=SimpleNamespace(id=PydanticObjectId())),
        delete=AsyncMock(),
    )


def _patch_module(
    monkeypatch: pytest.MonkeyPatch,
    execute_restriction: AsyncMock,
    test_services: object,
) -> None:
    monkeypatch.setattr(
        f"{_MODULE}.ChatModel.get_by_iid",
        AsyncMock(
            side_effect=[
                SimpleNamespace(id=PydanticObjectId(), tid=123),
                SimpleNamespace(id=PydanticObjectId(), tid=-100123),
            ]
        ),
    )
    monkeypatch.setattr(f"{_MODULE}.is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        f"{_MODULE}.execute_restriction",
        execute_restriction,
    )


@pytest.mark.asyncio
async def test_process_user_kicks_timed_out_non_join_request_user(
    monkeypatch: pytest.MonkeyPatch,
    test_services: object,
) -> None:
    ws_user = _make_ws_user(is_join_request=False, age_hours=WELCOMESECURITY_KICK_TIMEOUT_HOURS + 1)
    execute_restriction = AsyncMock(
        return_value=RestrictionResult(
            action=RestrictionAction.KICK,
            applied=True,
        )
    )
    _patch_module(monkeypatch, execute_restriction, test_services)

    await KickUnpassedUsers(test_services).process_user(ws_user)

    execute_restriction.assert_awaited_once_with(
        test_services.bot,
        RestrictionAction.KICK,
        -100123,
        123,
    )
    test_services.bot.decline_chat_join_request.assert_not_awaited()
    ws_user.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_user_declines_timed_out_join_request(
    monkeypatch: pytest.MonkeyPatch,
    test_services: object,
) -> None:
    ws_user = _make_ws_user(is_join_request=True, age_hours=WELCOMESECURITY_KICK_TIMEOUT_HOURS + 1)
    execute_restriction = AsyncMock()
    decline_join_request = AsyncMock()
    monkeypatch.setattr(
        test_services.bot,
        "decline_chat_join_request",
        decline_join_request,
    )
    _patch_module(monkeypatch, execute_restriction, test_services)

    await KickUnpassedUsers(test_services).process_user(ws_user)

    decline_join_request.assert_awaited_once_with(
        chat_id=-100123,
        user_id=123,
    )
    execute_restriction.assert_not_awaited()
    ws_user.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_user_leaves_user_inside_timeout_window(
    monkeypatch: pytest.MonkeyPatch,
    test_services: object,
) -> None:
    ws_user = _make_ws_user(is_join_request=False, age_hours=1)
    execute_restriction = AsyncMock()
    _patch_module(monkeypatch, execute_restriction, test_services)

    await KickUnpassedUsers(test_services).process_user(ws_user)

    execute_restriction.assert_not_awaited()
    ws_user.delete.assert_not_awaited()
