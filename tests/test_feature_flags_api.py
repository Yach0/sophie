from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from sophie_bot.modules.rest.api import feature_flags as api
from sophie_bot.modules.rest.api.feature_flags import FeatureFlagUpdate, RolloutBump, RolloutSet
from sophie_bot.utils import feature_flags as flags

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("db_init")]

@pytest.fixture
def services(test_redis: object) -> SimpleNamespace:
    return SimpleNamespace(redis=test_redis)


async def test_list_reports_defaults_and_marks_overrides(
    services: SimpleNamespace,
) -> None:
    await flags.delete_override("ai_chatbot", redis=services.redis)
    listed = {flag.name: flag for flag in await api.list_feature_flags(services)}

    chatbot = listed["ai_chatbot"]
    assert chatbot.default is True
    assert chatbot.overridden is False

    await flags.set_value("ai_chatbot", False, redis=services.redis)
    listed = {flag.name: flag for flag in await api.list_feature_flags(services)}
    assert listed["ai_chatbot"].value is False
    assert listed["ai_chatbot"].overridden is True


async def test_set_then_reset_a_boolean_flag(
    services: SimpleNamespace,
) -> None:
    await api.set_feature_flag("ai_chatbot", FeatureFlagUpdate(value=False), services)
    assert await flags.get_value("ai_chatbot", redis=services.redis) is False

    reset = await api.reset_feature_flag("ai_chatbot", services)
    assert reset.overridden is False
    assert await flags.get_value("ai_chatbot", redis=services.redis) is True  # back to its default


async def test_an_unknown_flag_is_a_404(services: SimpleNamespace) -> None:
    with pytest.raises(HTTPException) as error:
        await api.set_feature_flag("not_a_flag", FeatureFlagUpdate(value=True), services)
    assert error.value.status_code == 404


async def test_a_boolean_flag_rejects_a_non_boolean(
    services: SimpleNamespace,
) -> None:
    with pytest.raises(HTTPException) as error:
        await api.set_feature_flag("ai_chatbot", FeatureFlagUpdate(value="yes"), services)
    assert error.value.status_code == 422


async def test_an_integer_flag_rejects_a_boolean(
    services: SimpleNamespace,
) -> None:
    # A bool is an int in Python, so an int flag must not silently accept true/false.
    with pytest.raises(HTTPException) as error:
        await api.set_feature_flag("ai_filter_daily_chat_limit", FeatureFlagUpdate(value=True), services)
    assert error.value.status_code == 422


async def test_a_constrained_string_flag_rejects_a_value_outside_its_set(
    services: SimpleNamespace,
) -> None:
    with pytest.raises(HTTPException) as error:
        await api.set_feature_flag("ai_chatbot_service_tier", FeatureFlagUpdate(value="turbo"), services)
    assert error.value.status_code == 422

    # A value from the allowed set is accepted, and the flag reports the set for a dropdown.
    updated = await api.set_feature_flag("ai_chatbot_service_tier", FeatureFlagUpdate(value="flex"), services)
    assert updated.value == "flex"
    assert updated.allowed_values is not None and "flex" in updated.allowed_values


async def test_a_float_flag_accepts_a_whole_number(
    services: SimpleNamespace,
) -> None:
    updated = await api.set_feature_flag("ai_chatbot_streaming_backoff_seconds", FeatureFlagUpdate(value=2), services)
    assert updated.value == 2.0


async def test_instant_rollout_sets_a_target_percentage(
    services: SimpleNamespace,
) -> None:
    info = await api.set_feature_rollout("ai_research", RolloutSet(value=True, percentage=25), services)
    assert info.current_percentage == 25
    assert info.value is True

    listed = await api.list_feature_rollouts(services)
    assert any(rollout.feature == "ai_research" for rollout in listed)

    await api.delete_feature_rollout("ai_research", services)
    assert all(rollout.feature != "ai_research" for rollout in await api.list_feature_rollouts(services))


async def test_timed_rollout_ramps_to_full(
    services: SimpleNamespace,
) -> None:
    info = await api.set_feature_rollout("ai_research", RolloutSet(value=True, days=7), services)
    assert info.target_percentage == 100
    assert info.duration_days == 7
    await api.delete_feature_rollout("ai_research", services)


async def test_bumping_without_a_rollout_is_a_conflict(
    services: SimpleNamespace,
) -> None:
    await api.delete_feature_rollout("ai_research", services)
    with pytest.raises(HTTPException) as error:
        await api.bump_feature_rollout("ai_research", RolloutBump(percentage=10), services)
    assert error.value.status_code == 409


async def test_a_rollout_needs_a_percentage_or_days(
    services: SimpleNamespace,
) -> None:
    with pytest.raises(HTTPException) as error:
        await api.set_feature_rollout("ai_research", RolloutSet(value=True), services)
    assert error.value.status_code == 422


async def test_per_chat_override_set_list_and_delete(
    services: SimpleNamespace,
) -> None:
    override = await api.set_feature_chat_override("ai_chatbot", -100123, FeatureFlagUpdate(value=False), services)
    assert override.chat_tid == -100123 and override.value is False and override.source == "manual"

    # The override wins for that chat only.
    assert await flags.get_value("ai_chatbot", chat_tid=-100123, redis=services.redis) is False
    assert await flags.get_value("ai_chatbot", redis=services.redis) is True

    listed = await api.list_feature_chat_overrides(chat_tid=-100123)
    assert [item.feature for item in listed] == ["ai_chatbot"]

    await api.delete_feature_chat_override("ai_chatbot", -100123, services)
    assert await api.list_feature_chat_overrides(chat_tid=-100123) == []


async def test_a_per_chat_override_validates_the_value(
    services: SimpleNamespace,
) -> None:
    with pytest.raises(HTTPException) as error:
        await api.set_feature_chat_override("ai_chatbot", -100123, FeatureFlagUpdate(value="yes"), services)
    assert error.value.status_code == 422
