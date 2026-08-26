from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.utils.ai_mode import get_capabilities
from sophie_bot.modules.ai.utils.ai_restriction_reasons import should_generate_ai_reason


@pytest.mark.parametrize("mode", list(AIMode))
async def test_ai_reason_availability_follows_whether_ai_is_enabled(
    monkeypatch: pytest.MonkeyPatch, mode: AIMode
) -> None:
    feature_enabled = AsyncMock(return_value=True)
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_restriction_reasons.is_enabled", feature_enabled)
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.ai_restriction_reasons.resolve_chat_capabilities",
        AsyncMock(return_value=get_capabilities(mode)),
    )
    chat = SimpleNamespace(tid=-100123)

    assert await should_generate_ai_reason(chat) is (mode is not AIMode.disabled)
    feature_enabled.assert_awaited_once_with("ai_moderation_reasons", chat_tid=chat.tid)


async def test_ai_reason_feature_flag_disables_generation_in_an_enabled_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolve_capabilities = AsyncMock(return_value=get_capabilities(AIMode.support))
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.ai_restriction_reasons.is_enabled",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "sophie_bot.modules.ai.utils.ai_restriction_reasons.resolve_chat_capabilities",
        resolve_capabilities,
    )

    assert not await should_generate_ai_reason(SimpleNamespace(tid=-100123))
    resolve_capabilities.assert_not_awaited()
