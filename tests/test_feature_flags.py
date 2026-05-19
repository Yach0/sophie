from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest

from sophie_bot.db.models.feature_flag import FeatureFlagOverride
from sophie_bot.services.redis import aredis
from sophie_bot.utils.feature_flags import FEATURE_FLAGS, get_service_tier, get_value, is_enabled, list_all, set_enabled


@pytest.fixture(autouse=True)
async def _reset_feature_flag_overrides(db_init: object) -> AsyncGenerator[None, None]:
    await FeatureFlagOverride.get_pymongo_collection().delete_many({})
    yield
    await FeatureFlagOverride.get_pymongo_collection().delete_many({})


@pytest.mark.asyncio
async def test_is_enabled_returns_default_when_no_override_exists() -> None:
    assert await is_enabled("filters_rest_api") is False
    assert await is_enabled("notes_rest_api") is True


@pytest.mark.asyncio
async def test_proactive_replies_feature_flags_have_safe_defaults() -> None:
    assert await is_enabled("ai_proactive_replies") is False
    assert await get_value("ai_proactive_replies_model") == "openai/gpt-5-nano"
    assert "telegram bot named Sophie" in str(await get_value("ai_chatbot_system_prompt"))
    assert "professional AI translator" in str(await get_value("ai_translate_system_prompt"))
    assert "Summarize the chat day" in str(await get_value("ai_chat_summaries_prompt"))
    assert "moderation reason" in str(await get_value("ai_moderation_reason_prompt"))
    assert "filter handler suggestions" in str(await get_value("ai_filter_suggestions_prompt"))
    assert "Be very conservative" in str(await get_value("ai_proactive_replies_prompt"))
    assert await get_service_tier("ai_proactive_replies_service_tier") == "flex"
    assert await get_value("ai_proactive_replies_batch_size") == 30
    assert await get_value("ai_proactive_replies_window_seconds") == 180
    assert await get_value("ai_proactive_replies_max_answers") == 1
    assert await get_value("ai_proactive_replies_max_reactions") == 1
    assert await get_value("ai_proactive_replies_min_messages") == 30


@pytest.mark.asyncio
async def test_set_enabled_persists_override_for_later_reads() -> None:
    await set_enabled("filters_rest_api", True)

    assert await is_enabled("filters_rest_api") is True
    assert await aredis.hget("sophie:kill_switch", "filters_rest_api") == b"1"


@pytest.mark.asyncio
async def test_list_all_returns_all_features_in_declared_order_with_effective_values() -> None:
    await set_enabled("filters_rest_api", True)
    await set_enabled("notes_rest_api", False)

    states = await list_all()

    assert list(states) == list(FEATURE_FLAGS)
    assert states["filters_rest_api"] is True
    assert states["notes_rest_api"] is False
    assert states["welcomecaptcha"] is True


@pytest.mark.asyncio
async def test_invalid_redis_value_falls_back_to_default() -> None:
    await aredis.hset("sophie:kill_switch", "filters_rest_api", b"invalid")

    assert await is_enabled("filters_rest_api") is False

    states = await list_all()
    assert states["filters_rest_api"] is False


@pytest.mark.asyncio
async def test_kill_switch_public_api_flow_lists_confirmed_override() -> None:
    await set_enabled("welcomecaptcha", False)

    current = await is_enabled("welcomecaptcha")
    states = await list_all()

    assert current is False
    assert states["welcomecaptcha"] is False
    assert await aredis.hget("sophie:kill_switch", "welcomecaptcha") == b"0"
