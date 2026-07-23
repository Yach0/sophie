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
from sophie_bot.db.models.ai.ai_catalog import AIModelPurpose
from sophie_bot.modules.ai.utils.ai_catalog import AICatalog, CatalogModel, CatalogProvider
from sophie_bot.modules.ai.utils.ai_mode import get_capabilities


ENTERTAINMENT_CHATBOT = "free/chatbot"
MODERATION_TRANSLATION = "standard/translate"
MODERATION_FILTERS = "standard/filter"
SUPPORT_FILTERS = "quality/filter"
SUMMARY = "quality/summary"


def _catalog() -> AICatalog:
    provider = CatalogProvider(name="openrouter", kind="openrouter", base_url=None, api_key="k")
    names = [ENTERTAINMENT_CHATBOT, MODERATION_TRANSLATION, MODERATION_FILTERS, SUPPORT_FILTERS, SUMMARY]
    return AICatalog(
        version="1",
        providers={provider.name: provider},
        models={
            name: CatalogModel(
                name=name, provider=provider, api_name=name, supports_reasoning=True, extra_params=None
            )
            for name in names
        },
        roles={
            (AIMode.entertainment, AIModelPurpose.chatbot): ENTERTAINMENT_CHATBOT,
            (AIMode.moderation, AIModelPurpose.translation): MODERATION_TRANSLATION,
            (AIMode.moderation, AIModelPurpose.filters): MODERATION_FILTERS,
            (AIMode.support, AIModelPurpose.filters): SUPPORT_FILTERS,
            (None, AIModelPurpose.summary): SUMMARY,
        },
    )


def _patch_model_builder(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Make get_ai_model return a distinct sentinel per model name, without building a real client."""
    built: dict[str, object] = {}

    def fake_get_ai_model(model_name: str) -> object:
        return built.setdefault(model_name, object())

    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_chat_models.get_ai_model", fake_get_ai_model)
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_catalog.get_catalog", AsyncMock(return_value=_catalog()))
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

    assert model is built[ENTERTAINMENT_CHATBOT]


async def test_translations_and_filters_follow_the_chat_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    built = _patch_model_builder(monkeypatch)
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_chat_models.get_value", AsyncMock(return_value=""))
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.ai_chat_models.get_chat_mode", AsyncMock(return_value=AIMode.moderation)
    )
    chat_iid = PydanticObjectId()

    translations = await get_chat_translations_model(chat_iid)
    filters = await get_chat_filters_model(chat_iid)

    assert translations is built[MODERATION_TRANSLATION]
    assert filters is built[MODERATION_FILTERS]


async def test_filters_model_without_a_chat_uses_the_support_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    built = _patch_model_builder(monkeypatch)
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_chat_models.get_value", AsyncMock(return_value=""))

    model = await get_chat_filters_model(None)

    assert model is built[SUPPORT_FILTERS]


async def test_summary_model_falls_back_to_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    built = _patch_model_builder(monkeypatch)
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_chat_models.get_value", AsyncMock(return_value=""))

    model = await get_chat_summary_model(PydanticObjectId())

    assert model is built[SUMMARY]


async def test_purpose_missing_for_a_mode_falls_back_to_the_support_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Entertainment has no filters model in this catalog, so the support tier answers for it."""
    built = _patch_model_builder(monkeypatch)
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_chat_models.get_value", AsyncMock(return_value=""))

    model = await get_chat_filters_model(PydanticObjectId(), mode=AIMode.entertainment)

    assert model is built[SUPPORT_FILTERS]


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
