from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import DeleteMessage
from stfu_tg import Doc

from sophie_bot.modules.ai.utils.chatbot_streaming import ChatbotMessageStreamer, chatbot_streaming_session


@pytest.mark.parametrize("failure_stage", ["generation", "delivery"])
@pytest.mark.parametrize("cleanup_error_type", [TelegramBadRequest, TelegramNetworkError])
async def test_delete_failure_preserves_original_exception(
    failure_stage: str, cleanup_error_type: type[TelegramBadRequest | TelegramNetworkError], test_redis: object
) -> None:
    original_error = ValueError("Original reply failure")
    cleanup_error = cleanup_error_type(method=DeleteMessage(chat_id=1, message_id=3), message="Delete failed")
    bot = SimpleNamespace(
        edit_message_text=AsyncMock(side_effect=original_error),
        delete_message=AsyncMock(side_effect=cleanup_error),
    )
    source = SimpleNamespace(chat=SimpleNamespace(id=1), message_id=2, bot=bot)
    streamer = ChatbotMessageStreamer(source, "header", 0, redis=test_redis)
    streamer.response_message = SimpleNamespace(chat=source.chat, message_id=3, bot=bot)

    with (
        patch("sophie_bot.utils.logger.log.warning") as warning,
        pytest.raises(ValueError) as raised,
    ):
        async with chatbot_streaming_session(streamer):
            if failure_stage == "generation":
                raise original_error
            await streamer.send_final(Doc("answer"))

    assert raised.value is original_error
    bot.delete_message.assert_awaited_once_with(chat_id=1, message_id=3)
    warning.assert_called_once_with(
        "chatbot_streaming_session: Failed to delete unfinished progress", error=str(cleanup_error)
    )


async def test_cleanup_failure_without_original_exception_propagates(test_redis: object) -> None:
    cleanup_error = TelegramNetworkError(method=DeleteMessage(chat_id=1, message_id=3), message="Cleanup failed")
    streamer = ChatbotMessageStreamer(SimpleNamespace(), "header", 0, redis=test_redis)
    streamer.stop = AsyncMock(side_effect=cleanup_error)

    with pytest.raises(TelegramNetworkError) as raised:
        async with chatbot_streaming_session(streamer):
            pass

    assert raised.value is cleanup_error
