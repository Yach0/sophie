from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from sophie_bot.db.models.ai.ai_provider import AIProviderModel
from sophie_bot.modules.ai.utils.ai_get_provider import get_chat_default_model, get_chat_summary_model
from sophie_bot.modules.ai.utils.ai_header import ai_chatbot_header
from sophie_bot.modules.ai.utils.ai_model_registry import AI_MODEL_TO_SHORT_NAME


class _ModelMap(dict):
    def __getitem__(self, model_name: str) -> object:
        if model_name not in self:
            self[model_name] = object()
        return super().__getitem__(model_name)


class _NamedModel:
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name


@pytest.mark.asyncio
async def test_get_chat_summary_model_uses_stored_summary_model(monkeypatch: pytest.MonkeyPatch) -> None:
    chat_iid = PydanticObjectId()
    summary_model = object()
    # Feature flag returns the default value, so the function falls through
    # to the stored summary model in AIProviderModel.
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.ai_get_provider.get_value",
        AsyncMock(return_value="openai/gpt-5.5"),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.ai_get_provider.AIProviderModel.get_summary_model_name",
        AsyncMock(return_value="openai/gpt-5.4"),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.ai_get_provider.AI_MODELS",
        {"openai/gpt-5.4": summary_model},
    )

    model = await get_chat_summary_model(chat_iid)

    assert model is summary_model


@pytest.mark.asyncio
async def test_get_chat_summary_model_uses_feature_flag_override(monkeypatch: pytest.MonkeyPatch) -> None:
    chat_iid = PydanticObjectId()
    override_model = object()
    # Feature flag returns a non-default value — should use it directly
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.ai_get_provider.get_value",
        AsyncMock(return_value="anthropic/claude-haiku-4.5"),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.ai_get_provider.AI_MODELS",
        {"anthropic/claude-haiku-4.5": override_model},
    )

    model = await get_chat_summary_model(chat_iid)

    assert model is override_model


@pytest.mark.asyncio
async def test_get_chat_default_model_uses_custom_feature_flag_override(monkeypatch: pytest.MonkeyPatch) -> None:
    chat_iid = PydanticObjectId()
    model_map = _ModelMap()
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.ai_get_provider.get_value",
        AsyncMock(return_value="custom/provider-model"),
    )
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_get_provider.AI_MODELS", model_map)

    model = await get_chat_default_model(chat_iid, chat_tid=-100123)

    assert model is model_map["custom/provider-model"]


def test_ai_chatbot_header_renders_unknown_model_name() -> None:
    model_name = "custom/provider-model"
    model = _NamedModel(model_name)

    header = ai_chatbot_header(model)  # type: ignore[arg-type]

    assert model_name in header.to_html()


def test_xiaomi_mimo_model_has_display_name() -> None:
    assert AI_MODEL_TO_SHORT_NAME["xiaomi/mimo-v2.5-pro"] == "Xiaomi Mimo V2.5 Pro"


def test_ai_provider_summary_model_defaults_to_gpt_5_5() -> None:
    assert AIProviderModel.model_fields["summary_model"].default == "openai/gpt-5.5"
