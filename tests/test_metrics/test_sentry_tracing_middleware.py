from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Chat, Message, Update, User

from sophie_bot.middlewares.sentry_tracing import SentryTracingMiddleware


@pytest.fixture
def mock_update() -> Update:
    user = User(id=123, is_bot=False, first_name="Test")
    chat = Chat(id=456, type="private")
    message = Message(message_id=1, date=datetime.now(), chat=chat, from_user=user, text="/start")
    return Update(update_id=1, message=message)


@pytest.mark.asyncio
async def test_starts_transaction_when_flag_enabled(mock_update: Update):
    middleware = SentryTracingMiddleware()
    handler = AsyncMock(return_value="ok")
    transaction = MagicMock()
    transaction.__enter__.return_value = transaction
    transaction.__exit__.return_value = False

    with (
        patch("sophie_bot.middlewares.sentry_tracing.is_enabled", AsyncMock(return_value=True)),
        patch(
            "sophie_bot.middlewares.sentry_tracing.sentry_sdk.start_transaction", return_value=transaction
        ) as start_transaction,
    ):
        result = await middleware(handler, mock_update, {})

    assert result == "ok"
    handler.assert_awaited_once_with(mock_update, {})
    start_transaction.assert_called_once()
    assert start_transaction.call_args.kwargs["op"] == "bot.update"
    assert start_transaction.call_args.kwargs["name"] == "command:start"
    transaction.set_tag.assert_any_call("update_type", "message")
    transaction.set_tag.assert_any_call("command", "start")


@pytest.mark.asyncio
async def test_no_transaction_when_flag_disabled(mock_update: Update):
    middleware = SentryTracingMiddleware()
    handler = AsyncMock(return_value="ok")

    with (
        patch("sophie_bot.middlewares.sentry_tracing.is_enabled", AsyncMock(return_value=False)),
        patch("sophie_bot.middlewares.sentry_tracing.sentry_sdk.start_transaction") as start_transaction,
    ):
        result = await middleware(handler, mock_update, {})

    assert result == "ok"
    handler.assert_awaited_once_with(mock_update, {})
    start_transaction.assert_not_called()


@pytest.mark.asyncio
async def test_transaction_wraps_handler_exception(mock_update: Update):
    """The transaction context must exit (via __exit__) even when the handler raises."""
    middleware = SentryTracingMiddleware()
    handler = AsyncMock(side_effect=ValueError("boom"))
    transaction = MagicMock()
    transaction.__enter__.return_value = transaction
    transaction.__exit__.return_value = False

    with (
        patch("sophie_bot.middlewares.sentry_tracing.is_enabled", AsyncMock(return_value=True)),
        patch("sophie_bot.middlewares.sentry_tracing.sentry_sdk.start_transaction", return_value=transaction),
        pytest.raises(ValueError, match="boom"),
    ):
        await middleware(handler, mock_update, {})

    transaction.__exit__.assert_called_once()
