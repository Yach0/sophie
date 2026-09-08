from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException

from sophie_bot.db.models.antiflood import AntifloodModel
from sophie_bot.modules.antiflood.api.antiflood import (
    ActionRequest,
    _validate_action_request,
)
from sophie_bot.modules.antiflood.domain import (
    DEFAULT_MUTE_DURATION,
    get_action_duration,
)
from sophie_bot.modules.antiflood.middlewares.enforcer import (
    AntifloodEnforcerMiddleware,
)
from sophie_bot.modules.restrictions.actions.ban import (
    BanActionDataModel,
    BanModernAction,
)
from sophie_bot.modules.restrictions.actions.kick import KickModernAction
from sophie_bot.modules.restrictions.actions.mute import (
    MuteActionDataModel,
    MuteModernAction,
)
from sophie_bot.shared.actions import (
    ActionDefinition,
    RestrictionAction,
    RestrictionResult,
    StoredAction,
)

CHAT_TID = -1001483164428
USER_TID = 7860164386

FLOOD_ACTIONS: dict[str, ActionDefinition[Any]] = {
    action.definition.name: action.definition
    for action in (MuteModernAction, BanModernAction, KickModernAction)
}


def _settings(name: str, data: dict[str, Any]) -> AntifloodModel:
    return AntifloodModel(
        chat=PydanticObjectId(),
        actions=[StoredAction(name=name, data=data)],
    )


def _flooding_message() -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=CHAT_TID),
        from_user=SimpleNamespace(id=USER_TID),
    )


def _enforcer() -> AntifloodEnforcerMiddleware:
    services = SimpleNamespace(
        bot=object(),
        modules=SimpleNamespace(actions=FLOOD_ACTIONS),
    )
    return AntifloodEnforcerMiddleware(services)


def test_wizard_persists_durations_as_iso_strings() -> None:
    assert MuteActionDataModel(
        mute_duration=timedelta(hours=2)
    ).model_dump(mode="json") == {"mute_duration": "PT2H"}
    assert BanActionDataModel(
        ban_duration=timedelta(hours=2)
    ).model_dump(mode="json") == {"ban_duration": "PT2H"}


def test_get_action_duration_reads_configured_mute_duration() -> None:
    settings = _settings("mute_user", {"mute_duration": "PT2H"})
    assert get_action_duration(settings, FLOOD_ACTIONS) == timedelta(hours=2)


def test_get_action_duration_reads_configured_ban_duration() -> None:
    settings = _settings("ban_user", {"ban_duration": "PT2H"})
    assert get_action_duration(settings, FLOOD_ACTIONS) == timedelta(hours=2)


def test_get_action_duration_defaults_when_no_action_is_configured() -> None:
    settings = AntifloodModel(chat=PydanticObjectId())
    assert get_action_duration(settings, FLOOD_ACTIONS) == DEFAULT_MUTE_DURATION


def test_get_action_duration_is_none_for_indefinite_and_durationless_actions() -> None:
    assert (
        get_action_duration(
            _settings("mute_user", {"mute_duration": None}),
            FLOOD_ACTIONS,
        )
        is None
    )
    assert get_action_duration(
        _settings("kick_user", {}),
        FLOOD_ACTIONS,
    ) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action_name", "duration_field", "restriction_action"),
    [
        ("ban_user", "ban_duration", RestrictionAction.BAN),
        ("mute_user", "mute_duration", RestrictionAction.MUTE),
    ],
)
async def test_enforcer_uses_configured_duration(
    action_name: str,
    duration_field: str,
    restriction_action: RestrictionAction,
) -> None:
    settings = _settings(action_name, {duration_field: "PT2H"})
    execute = AsyncMock(
        return_value=RestrictionResult(
            action=restriction_action,
            applied=True,
        )
    )
    enforcer = _enforcer()

    with patch(
        "sophie_bot.modules.antiflood.middlewares.enforcer.execute_restriction",
        execute,
    ):
        assert await enforcer._execute_action(_flooding_message(), settings)

    execute.assert_awaited_once_with(
        enforcer.services.bot,
        restriction_action,
        CHAT_TID,
        USER_TID,
        until_date=timedelta(hours=2),
    )


@pytest.mark.asyncio
async def test_enforcer_uses_default_duration_without_configured_action() -> None:
    settings = AntifloodModel(chat=PydanticObjectId())
    execute = AsyncMock(
        return_value=RestrictionResult(
            action=RestrictionAction.MUTE,
            applied=True,
        )
    )
    enforcer = _enforcer()

    with patch(
        "sophie_bot.modules.antiflood.middlewares.enforcer.execute_restriction",
        execute,
    ):
        assert await enforcer._execute_action(_flooding_message(), settings)

    execute.assert_awaited_once_with(
        enforcer.services.bot,
        RestrictionAction.MUTE,
        CHAT_TID,
        USER_TID,
        until_date=DEFAULT_MUTE_DURATION,
    )


def test_action_request_rejects_invalid_duration() -> None:
    request = ActionRequest(
        name="mute_user",
        data={"mute_duration": "garbage"},
    )
    with pytest.raises(HTTPException) as exc_info:
        _validate_action_request(request, FLOOD_ACTIONS)

    assert exc_info.value.status_code == 422
    assert "Invalid action data for 'mute_user'" in str(exc_info.value.detail)


def test_action_request_canonicalizes_duration_for_storage() -> None:
    stored = _validate_action_request(
        ActionRequest(name="mute_user", data={"mute_duration": 7200}),
        FLOOD_ACTIONS,
    )
    assert stored.data == {"mute_duration": "PT2H"}


def test_action_request_falls_back_to_default_data_when_data_is_empty() -> None:
    stored = _validate_action_request(
        ActionRequest(name="mute_user", data={}),
        FLOOD_ACTIONS,
    )
    assert stored.data == {"mute_duration": None}


def test_action_request_accepts_actions_without_a_data_model() -> None:
    stored = _validate_action_request(
        ActionRequest(name="kick_user", data={}),
        FLOOD_ACTIONS,
    )
    assert stored.data == {}
