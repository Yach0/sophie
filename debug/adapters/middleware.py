from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

from aiogram.dispatcher.middlewares.manager import MiddlewareManager

from debug.adapters import EventRecorder
from debug.capture import TRACE_CONTEXT, child_span_id
from debug.i18n import gettext_debug as _
from debug.protocol import Category, Level, Outcome, Phase


def _callable_name(value: Any) -> str:
    target = value
    if not inspect.isfunction(value) and not inspect.ismethod(value):
        target = type(value)
    module = getattr(target, "__module__", None)
    qualname = getattr(target, "__qualname__", None) or getattr(target, "__name__", None)
    if module and qualname:
        return f"{module}.{qualname}"
    return type(value).__name__


def _handler_metadata(data: dict[str, Any]) -> dict[str, Any]:
    handler = data.get("handler")
    callback = getattr(handler, "callback", None)
    event_update = data.get("event_update")
    return {
        "handler": _callable_name(callback) if callback is not None else None,
        "event_type": type(event_update).__name__ if event_update is not None else None,
    }


def _interval_union_ns(intervals: list[tuple[int, int]]) -> int:
    if not intervals:
        return 0
    total = 0
    ordered = sorted(intervals)
    start, end = ordered[0]
    for next_start, next_end in ordered[1:]:
        if next_start <= end:
            end = max(end, next_end)
        else:
            total += end - start
            start, end = next_start, next_end
    return total + end - start


class _TimedMiddleware:
    def __init__(self, middleware: Any, recorder: EventRecorder) -> None:
        self._middleware = middleware
        self._recorder = recorder
        self._name = _callable_name(middleware)

    async def __call__(self, handler: Any, event: Any, data: dict[str, Any]) -> Any:
        context = TRACE_CONTEXT.get()
        span_id = child_span_id()
        parent_span_id = context.span_id if context else None
        token = TRACE_CONTEXT.set(replace(context, span_id=span_id)) if context is not None else None
        intervals: list[tuple[int, int]] = []
        started_ns = time.perf_counter_ns()
        self._recorder.emit(
            category=Category.MIDDLEWARE,
            name=self._name,
            phase=Phase.START,
            span_id=span_id,
            parent_span_id=parent_span_id,
            summary=_("Middleware {middleware} started").format(middleware=self._name),
            payload=_handler_metadata(data),
        )

        async def timed_downstream(next_event: Any, next_data: dict[str, Any]) -> Any:
            downstream_started = time.perf_counter_ns()
            try:
                return await handler(next_event, next_data)
            finally:
                intervals.append((downstream_started, time.perf_counter_ns()))

        try:
            result = await self._middleware(timed_downstream, event, data)
        except asyncio.CancelledError as error:
            finished_ns = time.perf_counter_ns()
            self._emit_finish(
                data,
                span_id,
                parent_span_id,
                started_ns,
                finished_ns,
                intervals,
                Outcome.CANCELLED,
                error,
                None,
            )
            raise
        except Exception as error:
            finished_ns = time.perf_counter_ns()
            self._emit_finish(
                data,
                span_id,
                parent_span_id,
                started_ns,
                finished_ns,
                intervals,
                Outcome.ERROR,
                error,
                None,
            )
            raise
        else:
            finished_ns = time.perf_counter_ns()
            self._emit_finish(
                data,
                span_id,
                parent_span_id,
                started_ns,
                finished_ns,
                intervals,
                Outcome.OK,
                None,
                result,
            )
            return result
        finally:
            if token is not None:
                TRACE_CONTEXT.reset(token)

    def _emit_finish(
        self,
        data: dict[str, Any],
        span_id: str,
        parent_span_id: str | None,
        started_ns: int,
        finished_ns: int,
        intervals: list[tuple[int, int]],
        outcome: Outcome,
        error: BaseException | None,
        result: Any,
    ) -> None:
        inclusive_ns = finished_ns - started_ns
        downstream_ns = min(inclusive_ns, _interval_union_ns(intervals))
        self._recorder.emit(
            category=Category.MIDDLEWARE,
            name=self._name,
            phase=Phase.FINISH,
            level=(
                Level.ERROR
                if outcome is Outcome.ERROR
                else Level.WARNING
                if outcome is Outcome.CANCELLED
                else Level.INFO
            ),
            outcome=outcome,
            duration_ms=inclusive_ns / 1_000_000,
            error=error,
            span_id=span_id,
            parent_span_id=parent_span_id,
            summary=_("Middleware {middleware} {outcome}").format(
                middleware=self._name,
                outcome=outcome.value,
            ),
            payload={
                **_handler_metadata(data),
                "inclusive_ms": inclusive_ns / 1_000_000,
                "self_ms": (inclusive_ns - downstream_ns) / 1_000_000,
                "downstream_await_ms": downstream_ns / 1_000_000,
                "downstream_calls": len(intervals),
                "result": result,
            },
        )


def install_middleware_observer(recorder: EventRecorder) -> Callable[[], None]:
    original = MiddlewareManager.wrap_middlewares
    if tuple(inspect.signature(original).parameters) != ("middlewares", "handler"):
        raise RuntimeError(_("Installed aiogram MiddlewareManager.wrap_middlewares signature is unsupported"))

    def wrap_middlewares(middlewares: Sequence[Any], handler: Any) -> Any:
        timed = [
            middleware
            if getattr(middleware, "_sophie_debug_root_correlation", False)
            else _TimedMiddleware(middleware, recorder)
            for middleware in middlewares
        ]
        return original(timed, handler)

    setattr(MiddlewareManager, "wrap_middlewares", staticmethod(wrap_middlewares))  # noqa: B010

    def restore() -> None:
        setattr(MiddlewareManager, "wrap_middlewares", staticmethod(original))  # noqa: B010

    return restore
