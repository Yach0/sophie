from __future__ import annotations

from asyncio import CancelledError
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


@pytest.mark.parametrize("original_error_type", [ValueError, CancelledError])
@pytest.mark.parametrize("delete_fails", [False, True])
async def test_stop_failure_preserves_original_exception(
    original_error_type: type[ValueError | CancelledError], delete_fails: bool, test_redis: object
) -> None:
    original_error = original_error_type("Original reply failure")
    stop_error = RuntimeError("Stop failed")
    delete_error = RuntimeError("Delete failed") if delete_fails else None
    bot = SimpleNamespace(delete_message=AsyncMock(side_effect=delete_error))
    source = SimpleNamespace(chat=SimpleNamespace(id=1), message_id=2, bot=bot)
    streamer = ChatbotMessageStreamer(source, "header", 0, redis=test_redis)
    streamer.response_message = SimpleNamespace(chat=source.chat, message_id=3, bot=bot)
    streamer.stop = AsyncMock(side_effect=stop_error)

    with (
        patch("sophie_bot.utils.logger.log.warning") as warning,
        pytest.raises(original_error_type) as raised,
    ):
        async with chatbot_streaming_session(streamer):
            raise original_error

    assert raised.value is original_error
    streamer.stop.assert_awaited_once_with()
    bot.delete_message.assert_awaited_once_with(chat_id=1, message_id=3)
    warning.assert_any_call("chatbot_streaming_session: Failed to stop streamer", error=str(stop_error))
    assert warning.call_count == (2 if delete_fails else 1)
    if delete_fails:
        warning.assert_any_call(
            "chatbot_streaming_session: Failed to delete unfinished progress", error=str(delete_error)
        )


@pytest.mark.parametrize(
    "cleanup_error",
    [
        RuntimeError("Cleanup failed"),
        CancelledError("Cleanup cancelled"),
        TelegramNetworkError(method=DeleteMessage(chat_id=1, message_id=3), message="Cleanup failed"),
    ],
)
async def test_cleanup_failure_without_original_exception_propagates(
    cleanup_error: BaseException, test_redis: object
) -> None:
    streamer = ChatbotMessageStreamer(SimpleNamespace(), "header", 0, redis=test_redis)
    streamer.stop = AsyncMock(side_effect=cleanup_error)

    with pytest.raises(type(cleanup_error)) as raised:
        async with chatbot_streaming_session(streamer):
            pass

    assert raised.value is cleanup_error
