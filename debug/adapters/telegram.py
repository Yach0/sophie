from __future__ import annotations

import asyncio
import time
from typing import Any

from aiogram.client.bot import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.dispatcher.event.bases import REJECTED, UNHANDLED
from aiogram.methods.base import TelegramMethod
from aiogram.types import TelegramObject, Update

from debug.adapters import EventRecorder
from debug.capture import TRACE_CONTEXT, child_span_id, new_trace_context
from debug.i18n import gettext_debug as _
from debug.protocol import Category, Level, Outcome, Phase
from sophie_bot.metrics.update_info import extract_command_name, extract_update_info


def update_chat_tid(update: Update) -> int | None:
    candidates = (
        update.message,
        update.edited_message,
        update.callback_query.message if update.callback_query else None,
        update.chat_member,
        update.my_chat_member,
        update.chat_join_request,
    )
    for candidate in candidates:
        chat = getattr(candidate, "chat", None)
        identifier = getattr(chat, "id", None)
        if isinstance(identifier, int):
            return identifier
    return None


def _update_metadata(update: Update, recorder: EventRecorder) -> tuple[dict[str, Any], str | None]:
    try:
        return dict(extract_update_info(update)), extract_command_name(update)
    except (AttributeError, TypeError, ValueError):
        recorder.sink.recorder_errors += 1
        return {
            "update_type": "unknown",
            "chat_type": "unknown",
            "transport": "polling",
            "message_kind": None,
        }, None


class TelegramRequestObserver(BaseRequestMiddleware):
    def __init__(self, recorder: EventRecorder) -> None:
        self._recorder = recorder

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[Any],
        bot: Bot,
        method: TelegramMethod[Any],
    ) -> Any:
        api_method = method.__api_method__
        span_id = child_span_id()
        started = time.perf_counter()
        params = method.model_dump(mode="python", exclude_none=True, warnings=False)
        chat_id = params.get("chat_id")
        self._recorder.emit(
            category=Category.TELEGRAM,
            name=api_method,
            phase=Phase.START,
            span_id=span_id,
            summary=_("Telegram {method} started").format(method=api_method),
            payload={
                "method": api_method,
                "params": params,
                "chat_tid": chat_id if isinstance(chat_id, int) else None,
                "long_poll": api_method == "getUpdates",
            },
        )
        try:
            result = await make_request(bot, method)
        except asyncio.CancelledError as error:
            self._recorder.emit(
                category=Category.TELEGRAM,
                name=api_method,
                phase=Phase.FINISH,
                level=Level.WARNING,
                outcome=Outcome.CANCELLED,
                duration_ms=(time.perf_counter() - started) * 1_000,
                error=error,
                span_id=span_id,
                summary=_("Telegram {method} cancelled").format(method=api_method),
                payload={"method": api_method, "params": params, "long_poll": api_method == "getUpdates"},
            )
            raise
        except Exception as error:
            self._recorder.emit(
                category=Category.TELEGRAM,
                name=api_method,
                phase=Phase.FINISH,
                level=Level.ERROR,
                outcome=Outcome.ERROR,
                duration_ms=(time.perf_counter() - started) * 1_000,
                error=error,
                span_id=span_id,
                summary=_("Telegram {method} failed").format(method=api_method),
                payload={"method": api_method, "params": params, "long_poll": api_method == "getUpdates"},
            )
            raise
        self._recorder.emit(
            category=Category.TELEGRAM,
            name=api_method,
            phase=Phase.FINISH,
            outcome=Outcome.OK,
            duration_ms=(time.perf_counter() - started) * 1_000,
            span_id=span_id,
            summary=_("Telegram {method} succeeded").format(method=api_method),
            payload={
                "method": api_method,
                "params": params,
                "result": result,
                "long_poll": api_method == "getUpdates",
            },
        )
        return result


class RootCorrelationMiddleware:
    _sophie_debug_root_correlation = True

    def __init__(self, recorder: EventRecorder) -> None:
        self._recorder = recorder

    async def __call__(self, handler: Any, event: TelegramObject, data: dict[str, Any]) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)
        context = new_trace_context(update_id=event.update_id, chat_tid=update_chat_tid(event))
        token = TRACE_CONTEXT.set(context)
        started = time.perf_counter()
        try:
            update_info, command = _update_metadata(event, self._recorder)
            self._recorder.emit(
                category=Category.TELEGRAM,
                name="incoming_update",
                phase=Phase.START,
                span_id=context.span_id,
                parent_span_id=None,
                summary=_("Incoming Telegram {update_type}").format(update_type=update_info["update_type"]),
                payload={
                    "update": event,
                    "update_type": update_info["update_type"],
                    "chat_type": update_info["chat_type"],
                    "message_kind": update_info["message_kind"],
                    "transport": update_info["transport"],
                    "command": command,
                },
            )
            try:
                result = await handler(event, data)
            except asyncio.CancelledError as error:
                self._recorder.emit(
                    category=Category.TELEGRAM,
                    name="incoming_update",
                    phase=Phase.FINISH,
                    level=Level.WARNING,
                    outcome=Outcome.CANCELLED,
                    duration_ms=(time.perf_counter() - started) * 1_000,
                    error=error,
                    span_id=context.span_id,
                    parent_span_id=None,
                    summary=_("Telegram update {update_id} cancelled").format(update_id=event.update_id),
                    payload={"disposition": "cancelled", "update_type": update_info["update_type"], "command": command},
                )
                raise
            except Exception as error:
                self._recorder.emit(
                    category=Category.TELEGRAM,
                    name="incoming_update",
                    phase=Phase.FINISH,
                    level=Level.ERROR,
                    outcome=Outcome.ERROR,
                    duration_ms=(time.perf_counter() - started) * 1_000,
                    error=error,
                    span_id=context.span_id,
                    parent_span_id=None,
                    summary=_("Telegram update {update_id} failed").format(update_id=event.update_id),
                    payload={"disposition": "error", "update_type": update_info["update_type"], "command": command},
                )
                raise
            disposition = "unhandled" if result is UNHANDLED else "rejected" if result is REJECTED else "consumed"
            self._recorder.emit(
                category=Category.TELEGRAM,
                name="incoming_update",
                phase=Phase.FINISH,
                outcome=Outcome.OK,
                duration_ms=(time.perf_counter() - started) * 1_000,
                span_id=context.span_id,
                parent_span_id=None,
                summary=_("Telegram update {update_id} {disposition}").format(
                    update_id=event.update_id,
                    disposition=disposition,
                ),
                payload={
                    "disposition": disposition,
                    "update_type": update_info["update_type"],
                    "command": command,
                    "result": result,
                },
            )
            return result
        finally:
            TRACE_CONTEXT.reset(token)
