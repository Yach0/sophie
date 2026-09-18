from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from aiogram.client.session.middlewares.base import NextRequestMiddlewareType
from aiogram.methods import GetMe

from debug.adapters import EventRecorder
from debug.adapters.middleware import _TimedMiddleware
from debug.adapters.telegram import TelegramRequestObserver
from debug.capture import TRACE_CONTEXT, TelemetrySink, new_trace_context
from debug.protocol import CapturedEvent, Outcome, Phase


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[CapturedEvent] = []
        self.recorder_errors = 0

    def emit(self, event: CapturedEvent) -> None:
        self.events.append(event)


def recorder_for(sink: RecordingSink) -> EventRecorder:
    return EventRecorder(cast(TelemetrySink, sink))


def test_middleware_observer_preserves_results_and_exceptions() -> None:
    async def scenario() -> None:
        sink = RecordingSink()
        recorder = recorder_for(sink)
        result = object()

        async def downstream(event: object, data: dict[str, Any]) -> object:
            return result

        async def middleware(handler: Any, event: object, data: dict[str, Any]) -> object:
            return await handler(event, data)

        context = new_trace_context(update_id=123)
        token = TRACE_CONTEXT.set(context)
        try:
            observed = _TimedMiddleware(middleware, recorder)
            assert await observed(downstream, object(), {}) is result
        finally:
            TRACE_CONTEXT.reset(token)

        assert [event.phase for event in sink.events] == [Phase.START, Phase.FINISH]
        assert sink.events[-1].outcome is Outcome.OK
        finish_payload = sink.events[-1].payload
        assert isinstance(finish_payload, dict)
        assert finish_payload["downstream_calls"] == 1
        assert sink.events[-1].parent_span_id == context.span_id

        failure = ValueError("application failure")

        async def failing_middleware(handler: Any, event: object, data: dict[str, Any]) -> object:
            raise failure

        with pytest.raises(ValueError) as raised:
            await _TimedMiddleware(failing_middleware, recorder)(downstream, object(), {})
        assert raised.value is failure
        assert sink.events[-1].error_type == "ValueError"
        assert sink.events[-1].outcome is Outcome.ERROR

    asyncio.run(scenario())


def test_telegram_request_observer_preserves_result_and_failure_identity() -> None:
    async def scenario() -> None:
        sink = RecordingSink()
        observer = TelegramRequestObserver(recorder_for(sink))
        result = object()

        async def successful_request(bot: Any, method: Any) -> object:
            return result

        request = cast(NextRequestMiddlewareType[Any], successful_request)
        assert await observer(request, cast(Any, object()), GetMe()) is result
        assert sink.events[-1].outcome is Outcome.OK

        failure = RuntimeError("telegram failure")

        async def failing_request(bot: Any, method: Any) -> object:
            raise failure

        with pytest.raises(RuntimeError) as raised:
            request = cast(NextRequestMiddlewareType[Any], failing_request)
            await observer(request, cast(Any, object()), GetMe())
        assert raised.value is failure
        assert sink.events[-1].error_type == "RuntimeError"
        assert sink.events[-1].outcome is Outcome.ERROR

    asyncio.run(scenario())
