from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Chat, Message, Update, User

from sophie_bot.middlewares import spam_detection
from sophie_bot.middlewares.request_context import RequestContext


@pytest.mark.parametrize("edited", [False, True])
@pytest.mark.parametrize("caption", [False, True])
async def test_group_predictions_are_persisted(monkeypatch: pytest.MonkeyPatch, edited: bool, caption: bool) -> None:
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=-100123, type="supergroup"),
        from_user=User(id=123, is_bot=False, first_name="Member"),
        text=None if caption else "buy now",
        caption="buy now" if caption else None,
    )
    event = Update(update_id=1, message=None if edited else message, edited_message=message if edited else None)
    predictor = MagicMock(return_value={"spam_probability": 0.9, "nsfw_probability": 0.0})
    match = MagicMock(insert=AsyncMock())
    model = MagicMock(return_value=match)
    monkeypatch.setattr(spam_detection, "predict_message", predictor)
    monkeypatch.setattr(spam_detection, "SpamMatchModel", model)
    monkeypatch.setattr(spam_detection, "is_user_admin", AsyncMock(return_value=False))
    monkeypatch.setattr(spam_detection, "is_user_group_whitelisted", AsyncMock(return_value=False))
    handler = AsyncMock(return_value="delivered")
    data = {
        "context": RequestContext(event_chat=MagicMock()),
        "services": SimpleNamespace(redis=MagicMock()),
    }

    result = await spam_detection.SpamDetectionMiddleware()(handler, event, data)

    assert result == "delivered"
    predictor.assert_called_once_with("buy now")
    model.assert_called_once_with(text="buy now", spam_probability=0.9, nsfw_probability=0.0, chat_tid=-100123)
    match.insert.assert_awaited_once()
    handler.assert_awaited_once_with(event, data)
