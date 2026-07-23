from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.utils.ai_chat_models import (
    get_chat_default_model,
    get_chat_filters_model,
    get_chat_summary_model,
    get_chat_translations_model,
)
from sophie_bot.modules.ai.utils.ai_mode import get_capabilities
from sophie_bot.modules.ai.utils.ai_model_registry import MODE_MODELS, get_model_name


def _patch_model_builder(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Make get_ai_model return a distinct sentinel per model name, without building a real client."""
    built: dict[str, object] = {}

    def fake_get_ai_model(model_name: str) -> object:
        return built.setdefault(model_name, object())

    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_chat_models.get_ai_model", fake_get_ai_model)
    return built


async def test_override_flag_wins_over_the_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    built = _patch_model_builder(monkeypatch)
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.ai_chat_models.get_value", AsyncMock(return_value="custom/model")
    )

    model = await get_chat_default_model(PydanticObjectId(), chat_tid=-100123)

    assert model is built["custom/model"]


async def test_chatbot_model_follows_the_chat_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    built = _patch_model_builder(monkeypatch)
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_chat_models.get_value", AsyncMock(return_value=""))
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.ai_chat_models.get_chat_mode", AsyncMock(return_value=AIMode.entertainment)
    )

    model = await get_chat_default_model(PydanticObjectId())

    assert model is built[MODE_MODELS[AIMode.entertainment]["chatbot"]]


async def test_translations_and_filters_follow_the_chat_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    built = _patch_model_builder(monkeypatch)
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_chat_models.get_value", AsyncMock(return_value=""))
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.ai_chat_models.get_chat_mode", AsyncMock(return_value=AIMode.moderation)
    )
    chat_iid = PydanticObjectId()

    translations = await get_chat_translations_model(chat_iid)
    filters = await get_chat_filters_model(chat_iid)

    assert translations is built[MODE_MODELS[AIMode.moderation]["translation"]]
    assert filters is built[MODE_MODELS[AIMode.moderation]["filters"]]


async def test_filters_model_without_a_chat_uses_the_support_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    built = _patch_model_builder(monkeypatch)
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_chat_models.get_value", AsyncMock(return_value=""))

    model = await get_chat_filters_model(None)

    assert model is built[MODE_MODELS[AIMode.support]["filters"]]


async def test_summary_model_falls_back_to_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    built = _patch_model_builder(monkeypatch)
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_chat_models.get_value", AsyncMock(return_value=""))

    model = await get_chat_summary_model(PydanticObjectId())

    assert model is built["openai/gpt-5.5"]


def test_unavailable_model_falls_back_to_the_support_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Entertainment's translation model lives on a custom provider that may not be configured."""
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_model_registry.CUSTOM_PROVIDER_NAMES", frozenset())

    assert get_model_name(AIMode.entertainment, "translation") == MODE_MODELS[AIMode.support]["translation"]


def test_disabled_mode_grants_nothing() -> None:
    capabilities = get_capabilities(AIMode.disabled)

    assert not capabilities.ai_enabled
    assert not any(
        (
            capabilities.chatbot_for_users,
            capabilities.trigger_on_reply,
            capabilities.proactive_replies,
            capabilities.notes_read,
            capabilities.memory,
            capabilities.moderator,
            capabilities.message_cache,
        )
    )


def test_moderation_mode_keeps_no_message_history() -> None:
    capabilities = get_capabilities(AIMode.moderation)

    assert capabilities.moderator
    assert not capabilities.message_cache
    assert not capabilities.chatbot_for_users
    assert not capabilities.trigger_on_reply


def test_no_mode_lets_the_agent_write_notes() -> None:
    """Notes are read-only in every mode; the save/delete tools no longer exist."""
    from sophie_bot.modules.ai.utils.chatbot_agent import CHATBOT_TOOLS

    assert {tool.name for tool in CHATBOT_TOOLS}.isdisjoint({"save_note", "delete_note"})
