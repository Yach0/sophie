from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException

from sophie_bot.db.models.antiflood import AntifloodModel
from sophie_bot.db.models.filters import FilterActionType
from sophie_bot.modules.antiflood.api.antiflood import ActionRequest
from sophie_bot.modules.antiflood.domain import DEFAULT_MUTE_DURATION, get_action_duration
from sophie_bot.modules.antiflood.middlewares.enforcer import AntifloodEnforcerMiddleware
from sophie_bot.modules.restrictions.actions.ban import BanActionDataModel, BanModernAction
from sophie_bot.modules.restrictions.actions.kick import KickModernAction
from sophie_bot.modules.restrictions.actions.mute import MuteActionDataModel, MuteModernAction

CHAT_TID = -1001483164428
USER_TID = 7860164386

FLOOD_ACTIONS = {
    "mute_user": MuteModernAction(),
    "ban_user": BanModernAction(),
    "kick_user": KickModernAction(),
}


@pytest.fixture
def registered_actions() -> Iterator[None]:
    with (
        patch("sophie_bot.modules.filters.utils_.action_duration.ALL_MODERN_ACTIONS", FLOOD_ACTIONS),
        patch("sophie_bot.modules.antiflood.api.antiflood.ALL_MODERN_ACTIONS", FLOOD_ACTIONS),
    ):
        yield


def _settings(name: str, data: dict[str, Any]) -> AntifloodModel:
    return AntifloodModel(chat=PydanticObjectId(), actions=[FilterActionType(name=name, data=data)])


def _flooding_message() -> SimpleNamespace:
    return SimpleNamespace(chat=SimpleNamespace(id=CHAT_TID), from_user=SimpleNamespace(id=USER_TID))


def test_wizard_persists_durations_as_iso_strings() -> None:
    assert MuteActionDataModel(mute_duration=timedelta(hours=2)).model_dump(mode="json") == {"mute_duration": "PT2H"}
    assert BanActionDataModel(ban_duration=timedelta(hours=2)).model_dump(mode="json") == {"ban_duration": "PT2H"}


@pytest.mark.asyncio
async def test_get_action_duration_reads_configured_mute_duration(db_init: Any, registered_actions: None) -> None:
    settings = _settings("mute_user", {"mute_duration": "PT2H"})

    assert get_action_duration(settings) == timedelta(hours=2)


@pytest.mark.asyncio
async def test_get_action_duration_reads_configured_ban_duration(db_init: Any, registered_actions: None) -> None:
    settings = _settings("ban_user", {"ban_duration": "PT2H"})

    assert get_action_duration(settings) == timedelta(hours=2)


@pytest.mark.asyncio
async def test_get_action_duration_defaults_when_no_action_is_configured(
    db_init: Any, registered_actions: None
) -> None:
    settings = AntifloodModel(chat=PydanticObjectId())

    assert get_action_duration(settings) == DEFAULT_MUTE_DURATION


@pytest.mark.asyncio
async def test_get_action_duration_is_none_for_indefinite_and_durationless_actions(
    db_init: Any, registered_actions: None
) -> None:
    assert get_action_duration(_settings("mute_user", {"mute_duration": None})) is None
    assert get_action_duration(_settings("kick_user", {})) is None


@pytest.mark.asyncio
async def test_enforcer_bans_for_the_configured_duration(db_init: Any, registered_actions: None) -> None:
    settings = _settings("ban_user", {"ban_duration": "PT2H"})
    ban_user = AsyncMock(return_value=True)

    with patch("sophie_bot.modules.antiflood.middlewares.enforcer.ban_user", ban_user):
        assert await AntifloodEnforcerMiddleware()._execute_action(_flooding_message(), settings)

    ban_user.assert_awaited_once_with(CHAT_TID, USER_TID, until_date=timedelta(hours=2))


@pytest.mark.asyncio
async def test_enforcer_mutes_for_the_configured_duration(db_init: Any, registered_actions: None) -> None:
    settings = _settings("mute_user", {"mute_duration": "PT2H"})
    mute_user = AsyncMock(return_value=True)

    with patch("sophie_bot.modules.antiflood.middlewares.enforcer.mute_user", mute_user):
        assert await AntifloodEnforcerMiddleware()._execute_action(_flooding_message(), settings)

    mute_user.assert_awaited_once_with(CHAT_TID, USER_TID, until_date=timedelta(hours=2))


@pytest.mark.asyncio
async def test_enforcer_mutes_for_the_default_duration_when_no_action_is_configured(
    db_init: Any, registered_actions: None
) -> None:
    settings = AntifloodModel(chat=PydanticObjectId())
    mute_user = AsyncMock(return_value=True)

    with patch("sophie_bot.modules.antiflood.middlewares.enforcer.mute_user", mute_user):
        assert await AntifloodEnforcerMiddleware()._execute_action(_flooding_message(), settings)

    mute_user.assert_awaited_once_with(CHAT_TID, USER_TID, until_date=DEFAULT_MUTE_DURATION)


def test_action_request_rejects_invalid_duration(registered_actions: None) -> None:
    with pytest.raises(HTTPException) as exc_info:
        ActionRequest(name="mute_user", data={"mute_duration": "garbage"})

    assert exc_info.value.status_code == 422
    assert "Invalid action data for 'mute_user'" in str(exc_info.value.detail)


def test_action_request_canonicalizes_duration_for_storage(registered_actions: None) -> None:
    assert ActionRequest(name="mute_user", data={"mute_duration": 7200}).data == {"mute_duration": "PT2H"}


def test_action_request_falls_back_to_default_data_when_data_is_empty(registered_actions: None) -> None:
    assert ActionRequest(name="mute_user", data={}).data == {"mute_duration": None}


def test_action_request_accepts_actions_without_a_data_model(registered_actions: None) -> None:
    assert ActionRequest(name="kick_user", data={}).data == {}
