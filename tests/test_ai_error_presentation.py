from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.types import Message

from sophie_bot.modules.ai.utils import support_replies
from sophie_bot.modules.ai.utils.ai_chatbot_reply import _send_chatbot_ai_failure_reply
from sophie_bot.modules.ai.utils.ai_errors import (
    AIErrorContext,
    AIRequestFailed,
    ai_error_boundary,
    ai_error_scope,
    ai_request_failed_from_error,
    ai_request_failed_message,
    get_ai_error_context,
    is_silent_ai_error,
)
from sophie_bot.modules.ai.utils.chatbot_streaming import ChatbotMessageStreamer
from sophie_bot.modules.error.utils.error_message import generic_error_message
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.exception import SophieException


@pytest.mark.parametrize("operation", ["translation", "research", "suggestions", "response", "transcription"])
@pytest.mark.parametrize("unexpected", [False, True])
async def test_explicit_failures_name_action_and_hide_exception(operation: str, unexpected: bool) -> None:
    cause = ValueError('private provider JSON {"code":2314}')

    @ai_error_boundary(AIErrorContext(operation=operation))
    async def request() -> None:
        if unexpected:
            raise cause
        raise ai_request_failed_from_error(cause, AIErrorContext(operation="structured")) from cause

    with pytest.raises(Exception) as raised:
        await request()
    text = generic_error_message(raised.value, "reference", hide_contact=True)["text"]
    assert operation in text.lower()
    assert "try again" in text.lower()
    assert "reference" in text
    assert "private" not in text
    assert "2314" not in text
    assert "provider" not in text
    if unexpected:
        assert raised.value is cause


async def test_nested_failure_keeps_failed_action_model_and_automatic_origin() -> None:
    cause = RuntimeError("bug")
    with (
        pytest.raises(RuntimeError),
        ai_error_scope(AIErrorContext(operation="response", origin="automatic")),
        ai_error_scope(AIErrorContext(operation="transcription", model_name="voxtral")),
    ):
        raise cause
    assert get_ai_error_context(cause) == AIErrorContext(
        operation="transcription", model_name="voxtral", origin="automatic"
    )
    later = ValueError("unrelated")
    assert get_ai_error_context(later) is None
    assert "unrelated" in generic_error_message(later, None)["text"]


async def test_cancellation_is_not_converted_or_attributed_and_cleanup_runs() -> None:
    cleanup = AsyncMock()

    @ai_error_boundary(AIErrorContext(operation="response", origin="automatic"))
    async def request() -> None:
        try:
            raise asyncio.CancelledError
        finally:
            await cleanup()

    with pytest.raises(asyncio.CancelledError) as raised:
        await request()
    assert get_ai_error_context(raised.value) is None
    cleanup.assert_awaited_once()


def test_meaningful_policy_notice_is_preserved() -> None:
    notice = SophieException("This message violates the chat policy.")
    with pytest.raises(SophieException), ai_error_scope(AIErrorContext(operation="response", origin="automatic")):
        raise notice
    assert not is_silent_ai_error(notice)
    assert "violates the chat policy" in generic_error_message(notice, "reference")["text"]


def test_handled_failure_reuses_reference() -> None:
    with ai_error_scope(AIErrorContext(operation="translation")):
        failure = ai_request_failed_from_error(ValueError("bug"), AIErrorContext(operation="structured"))
    failure.sentry_event_id = "already-reported"
    assert "already-reported" in ai_request_failed_message(error=failure)["text"]


async def test_automatic_handled_failure_cleans_progress_without_public_error() -> None:
    failure = AIRequestFailed("reference")
    message = Mock(spec=Message)
    streamer = Mock(spec=ChatbotMessageStreamer)
    with ai_error_scope(AIErrorContext(operation="response", origin="automatic")):
        result = await _send_chatbot_ai_failure_reply(message, streamer, failure)
    assert result is None
    streamer.stop.assert_awaited_once()
    streamer.delete_progress.assert_awaited_once()
    message.reply.assert_not_called()


def test_mixed_exception_group_is_not_converted_to_handled_provider_failure() -> None:
    failure = ExceptionGroup("mixed failures", [TimeoutError(), ValueError("programming bug")])
    with pytest.raises(ExceptionGroup) as raised:
        ai_request_failed_from_error(failure, AIErrorContext(operation="response"))
    assert raised.value is failure


@pytest.mark.parametrize("cancelled", [False, True])
async def test_support_background_failure_reporting_preserves_cancellation(
    cancelled: bool,
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
    runtime_sentry_events: list[dict[str, Any]],
) -> None:
    failure = asyncio.CancelledError() if cancelled else ValueError("support preparation bug")
    monkeypatch.setattr(support_replies, "_support_reply", AsyncMock(side_effect=failure))
    chat = Mock()
    chat.tid = -1234
    task = asyncio.create_task(support_replies._run_support_reply(Mock(), chat, services=test_services))
    with pytest.raises(type(failure)) as raised:
        await task
    assert raised.value is failure
    support_replies._report_support_reply_failure(task)
    if cancelled:
        assert not runtime_sentry_events
    else:
        (event,) = runtime_sentry_events
        assert event["tags"]["ai.origin"] == "automatic"
        assert event["exception"]["values"][-1]["type"] == "ValueError"
        support_replies._report_support_reply_failure(task)
        assert len(runtime_sentry_events) == 1
