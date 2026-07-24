from __future__ import annotations

import pytest
from fastapi import HTTPException

from sophie_bot.modules.rest.api import feature_flags as api
from sophie_bot.modules.rest.api.feature_flags import FeatureFlagUpdate, RolloutBump, RolloutSet
from sophie_bot.utils import feature_flags as flags

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("db_init")]


async def test_list_reports_defaults_and_marks_overrides() -> None:
    await flags.delete_override("ai_chatbot")
    listed = {flag.name: flag for flag in await api.list_feature_flags()}

    chatbot = listed["ai_chatbot"]
    assert chatbot.default is True
    assert chatbot.overridden is False

    await flags.set_value("ai_chatbot", False)
    listed = {flag.name: flag for flag in await api.list_feature_flags()}
    assert listed["ai_chatbot"].value is False
    assert listed["ai_chatbot"].overridden is True


async def test_set_then_reset_a_boolean_flag() -> None:
    await api.set_feature_flag("ai_chatbot", FeatureFlagUpdate(value=False))
    assert await flags.get_value("ai_chatbot") is False

    reset = await api.reset_feature_flag("ai_chatbot")
    assert reset.overridden is False
    assert await flags.get_value("ai_chatbot") is True  # back to its default


async def test_an_unknown_flag_is_a_404() -> None:
    with pytest.raises(HTTPException) as error:
        await api.set_feature_flag("not_a_flag", FeatureFlagUpdate(value=True))
    assert error.value.status_code == 404


async def test_a_boolean_flag_rejects_a_non_boolean() -> None:
    with pytest.raises(HTTPException) as error:
        await api.set_feature_flag("ai_chatbot", FeatureFlagUpdate(value="yes"))
    assert error.value.status_code == 422


async def test_an_integer_flag_rejects_a_boolean() -> None:
    # A bool is an int in Python, so an int flag must not silently accept true/false.
    with pytest.raises(HTTPException) as error:
        await api.set_feature_flag("ai_filter_daily_chat_limit", FeatureFlagUpdate(value=True))
    assert error.value.status_code == 422


async def test_a_constrained_string_flag_rejects_a_value_outside_its_set() -> None:
    with pytest.raises(HTTPException) as error:
        await api.set_feature_flag("ai_chatbot_service_tier", FeatureFlagUpdate(value="turbo"))
    assert error.value.status_code == 422

    # A value from the allowed set is accepted, and the flag reports the set for a dropdown.
    updated = await api.set_feature_flag("ai_chatbot_service_tier", FeatureFlagUpdate(value="flex"))
    assert updated.value == "flex"
    assert updated.allowed_values is not None and "flex" in updated.allowed_values


async def test_a_float_flag_accepts_a_whole_number() -> None:
    updated = await api.set_feature_flag("ai_chatbot_streaming_backoff_seconds", FeatureFlagUpdate(value=2))
    assert updated.value == 2.0


async def test_instant_rollout_sets_a_target_percentage() -> None:
    info = await api.set_feature_rollout("ai_research", RolloutSet(value=True, percentage=25))
    assert info.current_percentage == 25
    assert info.value is True

    listed = await api.list_feature_rollouts()
    assert any(rollout.feature == "ai_research" for rollout in listed)

    await api.delete_feature_rollout("ai_research")
    assert all(rollout.feature != "ai_research" for rollout in await api.list_feature_rollouts())


async def test_timed_rollout_ramps_to_full() -> None:
    info = await api.set_feature_rollout("ai_research", RolloutSet(value=True, days=7))
    assert info.target_percentage == 100
    assert info.duration_days == 7
    await api.delete_feature_rollout("ai_research")


async def test_bumping_without_a_rollout_is_a_conflict() -> None:
    await api.delete_feature_rollout("ai_research")
    with pytest.raises(HTTPException) as error:
        await api.bump_feature_rollout("ai_research", RolloutBump(percentage=10))
    assert error.value.status_code == 409


async def test_a_rollout_needs_a_percentage_or_days() -> None:
    with pytest.raises(HTTPException) as error:
        await api.set_feature_rollout("ai_research", RolloutSet(value=True))
    assert error.value.status_code == 422


async def test_per_chat_override_set_list_and_delete() -> None:
    override = await api.set_feature_chat_override("ai_chatbot", -100123, FeatureFlagUpdate(value=False))
    assert override.chat_tid == -100123 and override.value is False and override.source == "manual"

    # The override wins for that chat only.
    assert await flags.get_value("ai_chatbot", chat_tid=-100123) is False
    assert await flags.get_value("ai_chatbot") is True

    listed = await api.list_feature_chat_overrides(chat_tid=-100123)
    assert [item.feature for item in listed] == ["ai_chatbot"]

    await api.delete_feature_chat_override("ai_chatbot", -100123)
    assert await api.list_feature_chat_overrides(chat_tid=-100123) == []


async def test_a_per_chat_override_validates_the_value() -> None:
    with pytest.raises(HTTPException) as error:
        await api.set_feature_chat_override("ai_chatbot", -100123, FeatureFlagUpdate(value="yes"))
    assert error.value.status_code == 422
