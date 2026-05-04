from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from sophie_bot.db.models.ai.ai_provider import AIProviderModel
from sophie_bot.modules.ai.utils.ai_get_provider import get_chat_summary_model


@pytest.mark.asyncio
async def test_get_chat_summary_model_uses_stored_summary_model(monkeypatch: pytest.MonkeyPatch) -> None:
    chat_iid = PydanticObjectId()
    summary_model = object()
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


def test_ai_provider_summary_model_defaults_to_gpt_5_4() -> None:
    assert AIProviderModel.model_fields["summary_model"].default == "openai/gpt-5.4"
