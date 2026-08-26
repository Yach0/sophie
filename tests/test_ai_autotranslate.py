from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sophie_bot.db.models.ai.ai_autotranslate import AIAutotranslateModel


@pytest.mark.asyncio
async def test_disabling_preserves_language_settings(monkeypatch):
    model = SimpleNamespace(
        enabled=True,
        excluded_languages={"de"},
        recent_languages=["fr", "es"],
        save=AsyncMock(),
    )
    monkeypatch.setattr(AIAutotranslateModel, "find_one", AsyncMock(return_value=model))
    chat = SimpleNamespace(iid="chat-id")

    await AIAutotranslateModel.set_state(chat, False)

    assert model.enabled is False
    assert model.excluded_languages == {"de"}
    assert model.recent_languages == ["fr", "es"]
    model.save.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_legacy_document_without_enabled_field_is_enabled(monkeypatch):
    model = SimpleNamespace()
    monkeypatch.setattr(AIAutotranslateModel, "find_one", AsyncMock(return_value=model))

    assert await AIAutotranslateModel.get_state("chat-id") is True
