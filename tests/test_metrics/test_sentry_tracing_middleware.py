from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Chat, Message, Update, User

from sophie_bot.middlewares.sentry_tracing import SentryTracingMiddleware


@pytest.fixture
def mock_update() -> Update:
    user = User(id=123, is_bot=False, first_name="Test")
    chat = Chat(id=456, type="private")
    message = Message(message_id=1, date=datetime.now(UTC), chat=chat, from_user=user, text="/start")
    return Update(update_id=1, message=message)


@pytest.mark.asyncio
async def test_starts_span_for_update(mock_update: Update) -> None:
    middleware = SentryTracingMiddleware()
    handler = AsyncMock(return_value="ok")
    span = MagicMock()
    span.__enter__.return_value = span
    span.__exit__.return_value = False
    data = {"services": SimpleNamespace(redis=object())}

    with (
        patch("sophie_bot.middlewares.sentry_tracing.sentry_sdk.start_span", return_value=span) as start_span,
    ):
        result = await middleware(handler, mock_update, data)

    assert result == "ok"
    handler.assert_awaited_once_with(mock_update, data)
    start_span.assert_called_once()
    assert start_span.call_args.kwargs["name"] == "command:start"
    assert start_span.call_args.kwargs["parent_span"] is None
    attributes = start_span.call_args.kwargs["attributes"]
    assert attributes["sentry.op"] == "bot.update"
    assert attributes["update_type"] == "message"
    assert attributes["command"] == "start"


@pytest.mark.asyncio
async def test_span_wraps_handler_exception(mock_update: Update) -> None:
    """The span context must exit (via __exit__) even when the handler raises."""
    middleware = SentryTracingMiddleware()
    handler = AsyncMock(side_effect=ValueError("boom"))
    span = MagicMock()
    span.__enter__.return_value = span
    span.__exit__.return_value = False
    data = {"services": SimpleNamespace(redis=object())}

    with (
        patch("sophie_bot.middlewares.sentry_tracing.sentry_sdk.start_span", return_value=span),
        pytest.raises(ValueError, match="boom"),
    ):
        await middleware(handler, mock_update, data)

    span.__exit__.assert_called_once()
