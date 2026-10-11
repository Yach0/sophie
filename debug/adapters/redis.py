from __future__ import annotations

import asyncio
import inspect
import re
import time
from collections.abc import Callable, Sequence
from typing import Any

from redis.asyncio.client import Pipeline, Redis

from debug.adapters import EventRecorder
from debug.capture import child_span_id
from debug.i18n import gettext_debug as _
from debug.protocol import Category, Level, Outcome, Phase

_MESSAGE_KEY = re.compile(r"^messages:(-?\d+)$")
_TOOL_KEY = re.compile(r"^ai:tool_history:(-?\d+)$")
_PRICING_KEY = "sophie:ai:openrouter_pricing"
_READ_COMMANDS = {
    "EXISTS",
    "GET",
    "GETRANGE",
    "HGET",
    "HGETALL",
    "HKEYS",
    "HLEN",
    "HSCAN",
    "LRANGE",
    "MGET",
    "PTTL",
    "SCARD",
    "SCAN",
    "SMEMBERS",
    "SSCAN",
    "STRLEN",
    "TTL",
    "TYPE",
    "ZRANGE",
    "ZRANGEBYSCORE",
    "ZCARD",
    "ZSCAN",
}
_DELETE_COMMANDS = {"DEL", "UNLINK"}
_PRUNE_COMMANDS = {"HDEL", "ZREMRANGEBYRANK", "ZREMRANGEBYSCORE"}


def _command_name(args: Sequence[Any]) -> str:
    if not args:
        return "UNKNOWN"
    value = args[0]
    if isinstance(value, bytes):
        return value.decode("ascii", errors="replace").upper()
    if isinstance(value, str):
        return value.upper()
    return type(value).__name__.upper()


def _decode_key(value: Any) -> str | None:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return value if isinstance(value, str) else None


def _redis_keys(command: str, args: Sequence[Any]) -> tuple[str, ...]:
    if len(args) < 2 or command == "SCAN":
        return ()
    values = args[1:] if command in {"DEL", "EXISTS", "MGET", "UNLINK"} else args[1:2]
    return tuple(key for value in values if (key := _decode_key(value)) is not None)


def _client_identity(client: Redis | Pipeline) -> dict[str, Any]:
    settings = getattr(client.connection_pool, "connection_kwargs", {})
    return {
        "type": type(client).__name__,
        "host": settings.get("host"),
        "port": settings.get("port"),
        "db": settings.get("db", 0),
    }


def _result_facts(result: Any) -> dict[str, Any]:
    if isinstance(result, BaseException):
        return {"error": type(result).__name__}
    if result is None:
        return {"empty": True, "count": 0}
    if isinstance(result, (bytes, str)):
        return {"empty": len(result) == 0, "size": len(result)}
    if isinstance(result, (list, tuple, set, frozenset, dict)):
        return {"empty": len(result) == 0, "count": len(result)}
    if isinstance(result, bool):
        return {"empty": not result, "count": int(result)}
    if isinstance(result, int):
        return {"empty": result == 0, "count": result}
    return {"type": type(result).__name__}


def _ai_result_facts(command: str, result: Any) -> dict[str, Any]:
    if command in {"HSCAN", "SSCAN", "ZSCAN"} and isinstance(result, (list, tuple)) and len(result) == 2:
        cursor, entries = result
        if isinstance(entries, (list, tuple, set, frozenset, dict)):
            return {"cursor": cursor, "empty": len(entries) == 0, "count": len(entries)}
    if command in {"TTL", "PTTL"} and isinstance(result, int) and not isinstance(result, bool):
        return {"ttl": result}
    return _result_facts(result)


def _ai_key(key: str | None) -> tuple[str, int | None] | None:
    if key is None:
        return None
    if match := _MESSAGE_KEY.fullmatch(key):
        return "messages", int(match.group(1))
    if match := _TOOL_KEY.fullmatch(key):
        return "tools", int(match.group(1))
    if key == _PRICING_KEY:
        return "pricing", None
    return None


def _activity(command: str) -> str:
    if command in _DELETE_COMMANDS:
        return "delete"
    if command in _PRUNE_COMMANDS:
        return "prune"
    if command in _READ_COMMANDS:
        return "read"
    return "write"


def _emit_ai_activity(
    recorder: EventRecorder,
    args: Sequence[Any],
    result: Any,
    *,
    redis_span_id: str,
    command_index: int | None = None,
    error: BaseException | None = None,
    cancelled: bool = False,
    unknown: bool = False,
) -> None:
    command = _command_name(args)
    matching_keys = [(key, matched) for key in _redis_keys(command, args) if (matched := _ai_key(key)) is not None]
    if not matching_keys:
        return
    activity = _activity(command)
    if unknown:
        outcome = None
    elif cancelled:
        outcome = Outcome.CANCELLED
    elif error is not None or isinstance(result, BaseException):
        outcome = Outcome.ERROR
    else:
        outcome = Outcome.OK
    observed_error = error if error is not None else result if isinstance(result, BaseException) else None
    for key, (cache_kind, chat_tid) in matching_keys:
        recorder.emit(
            category=Category.AI_CACHE,
            name=f"{cache_kind}.{activity}",
            phase=Phase.INSTANT,
            level=(
                Level.WARNING
                if unknown or outcome is Outcome.CANCELLED
                else Level.ERROR
                if outcome is Outcome.ERROR
                else Level.INFO
            ),
            outcome=outcome,
            error=observed_error,
            span_id=child_span_id(),
            parent_span_id=redis_span_id,
            summary=_("AI {cache_kind} cache {activity} via Redis {command}").format(
                cache_kind=cache_kind,
                activity=activity,
                command=command,
            ),
            payload={
                "cache_kind": cache_kind,
                "chat_tid": chat_tid,
                "key": key,
                "redis_command": command,
                "redis_span_id": redis_span_id,
                "command_index": command_index,
                "aggregate_result": len(matching_keys) > 1,
                "result": (
                    {"unknown": True} if unknown else _ai_result_facts(command, error if error is not None else result)
                ),
                "provider_prompt_cache": _("not reported"),
            },
        )


def _emit_failure(
    recorder: EventRecorder,
    *,
    name: str,
    span_id: str,
    started: float,
    payload: dict[str, Any],
    error: BaseException,
    cancelled: bool,
) -> None:
    recorder.emit(
        category=Category.REDIS,
        name=name,
        phase=Phase.FINISH,
        level=Level.WARNING if cancelled else Level.ERROR,
        outcome=Outcome.CANCELLED if cancelled else Outcome.ERROR,
        duration_ms=(time.perf_counter() - started) * 1_000,
        error=error,
        span_id=span_id,
        summary=_("Redis {command} {outcome}").format(
            command=name,
            outcome=_("cancelled") if cancelled else _("failed"),
        ),
        payload=payload,
    )


def install_redis_observer(recorder: EventRecorder) -> Callable[[], None]:
    original_execute_command = Redis.execute_command
    original_pipeline_execute = Pipeline.execute
    original_immediate_execute = Pipeline.immediate_execute_command
    expected_signatures = (
        (original_execute_command, ("self", "args", "options")),
        (original_pipeline_execute, ("self", "raise_on_error")),
        (original_immediate_execute, ("self", "args", "options")),
    )
    for method, expected in expected_signatures:
        if tuple(inspect.signature(method).parameters) != expected:
            raise RuntimeError(_("Installed redis-py execution signature is unsupported"))

    async def execute_command(client: Redis, *args: Any, **options: Any) -> Any:
        name = _command_name(args)
        span_id = child_span_id()
        started = time.perf_counter()
        recorder.emit(
            category=Category.REDIS,
            name=name,
            phase=Phase.START,
            span_id=span_id,
            summary=_("Redis {command} started").format(command=name),
            payload={"args": args, "options": options, "client": _client_identity(client)},
        )
        try:
            result = await original_execute_command(client, *args, **options)
        except asyncio.CancelledError as error:
            _emit_failure(
                recorder,
                name=name,
                span_id=span_id,
                started=started,
                payload={"args": args, "options": options, "client": _client_identity(client)},
                error=error,
                cancelled=True,
            )
            _emit_ai_activity(recorder, args, None, redis_span_id=span_id, error=error, cancelled=True)
            raise
        except Exception as error:
            _emit_failure(
                recorder,
                name=name,
                span_id=span_id,
                started=started,
                payload={"args": args, "options": options, "client": _client_identity(client)},
                error=error,
                cancelled=False,
            )
            _emit_ai_activity(recorder, args, None, redis_span_id=span_id, error=error)
            raise
        recorder.emit(
            category=Category.REDIS,
            name=name,
            phase=Phase.FINISH,
            outcome=Outcome.OK,
            duration_ms=(time.perf_counter() - started) * 1_000,
            span_id=span_id,
            summary=_("Redis {command} succeeded").format(command=name),
            payload={"args": args, "result": result, "client": _client_identity(client)},
        )
        _emit_ai_activity(recorder, args, result, redis_span_id=span_id)
        return result

    async def immediate_execute_command(client: Pipeline, *args: Any, **options: Any) -> Any:
        name = _command_name(args)
        span_id = child_span_id()
        started = time.perf_counter()
        recorder.emit(
            category=Category.REDIS,
            name=f"watch.{name}",
            phase=Phase.START,
            span_id=span_id,
            summary=_("Redis WATCH-path {command} started").format(command=name),
            payload={"args": args, "options": options, "client": _client_identity(client)},
        )
        try:
            result = await original_immediate_execute(client, *args, **options)
        except asyncio.CancelledError as error:
            _emit_failure(
                recorder,
                name=f"watch.{name}",
                span_id=span_id,
                started=started,
                payload={"args": args, "options": options, "client": _client_identity(client)},
                error=error,
                cancelled=True,
            )
            _emit_ai_activity(recorder, args, None, redis_span_id=span_id, error=error, cancelled=True)
            raise
        except Exception as error:
            _emit_failure(
                recorder,
                name=f"watch.{name}",
                span_id=span_id,
                started=started,
                payload={"args": args, "options": options, "client": _client_identity(client)},
                error=error,
                cancelled=False,
            )
            _emit_ai_activity(recorder, args, None, redis_span_id=span_id, error=error)
            raise
        recorder.emit(
            category=Category.REDIS,
            name=f"watch.{name}",
            phase=Phase.FINISH,
            outcome=Outcome.OK,
            duration_ms=(time.perf_counter() - started) * 1_000,
            span_id=span_id,
            summary=_("Redis WATCH-path {command} succeeded").format(command=name),
            payload={"args": args, "result": result, "client": _client_identity(client)},
        )
        _emit_ai_activity(recorder, args, result, redis_span_id=span_id)
        return result

    async def pipeline_execute(client: Pipeline, raise_on_error: bool = True) -> list[Any]:
        stack = list(client.command_stack)
        if not stack and not client.watching:
            return await original_pipeline_execute(client, raise_on_error=raise_on_error)
        span_id = child_span_id()
        started = time.perf_counter()
        commands = [
            {"index": index, "args": command_args, "options": command_options}
            for index, (command_args, command_options) in enumerate(stack)
        ]
        recorder.emit(
            category=Category.REDIS,
            name="pipeline",
            phase=Phase.START,
            span_id=span_id,
            summary=_("Redis pipeline started"),
            payload={
                "transaction": bool(client.is_transaction or client.explicit_transaction),
                "raise_on_error": raise_on_error,
                "commands": commands,
                "client": _client_identity(client),
            },
        )
        try:
            results = await original_pipeline_execute(client, raise_on_error=raise_on_error)
        except asyncio.CancelledError as error:
            _emit_failure(
                recorder,
                name="pipeline",
                span_id=span_id,
                started=started,
                payload={"commands": commands, "results_known": False, "client": _client_identity(client)},
                error=error,
                cancelled=True,
            )
            for index, (command_args, _command_options) in enumerate(stack):
                _emit_ai_activity(
                    recorder,
                    command_args,
                    None,
                    redis_span_id=span_id,
                    command_index=index,
                    unknown=True,
                )
            raise
        except Exception as error:
            _emit_failure(
                recorder,
                name="pipeline",
                span_id=span_id,
                started=started,
                payload={"commands": commands, "results_known": False, "client": _client_identity(client)},
                error=error,
                cancelled=False,
            )
            for index, (command_args, _command_options) in enumerate(stack):
                _emit_ai_activity(
                    recorder,
                    command_args,
                    None,
                    redis_span_id=span_id,
                    command_index=index,
                    unknown=True,
                )
            raise
        command_results = []
        has_error_result = False
        for index, result in enumerate(results):
            has_error_result = has_error_result or isinstance(result, BaseException)
            command_results.append(
                {
                    "index": index,
                    "result": result,
                    "outcome": "error" if isinstance(result, BaseException) else "ok",
                }
            )
            if index < len(stack):
                _emit_ai_activity(
                    recorder,
                    stack[index][0],
                    result,
                    redis_span_id=span_id,
                    command_index=index,
                )
        recorder.emit(
            category=Category.REDIS,
            name="pipeline",
            phase=Phase.FINISH,
            level=Level.WARNING if has_error_result else Level.INFO,
            outcome=Outcome.ERROR if has_error_result else Outcome.OK,
            duration_ms=(time.perf_counter() - started) * 1_000,
            span_id=span_id,
            summary=(
                _("Redis pipeline completed with error results") if has_error_result else _("Redis pipeline succeeded")
            ),
            payload={
                "transaction": bool(client.is_transaction or client.explicit_transaction),
                "raise_on_error": raise_on_error,
                "commands": commands,
                "results": command_results,
                "aggregate_timing": True,
                "client": _client_identity(client),
            },
        )
        return results

    setattr(Redis, "execute_command", execute_command)  # noqa: B010
    setattr(Pipeline, "execute", pipeline_execute)  # noqa: B010
    setattr(Pipeline, "immediate_execute_command", immediate_execute_command)  # noqa: B010

    def restore() -> None:
        setattr(Redis, "execute_command", original_execute_command)  # noqa: B010
        setattr(Pipeline, "execute", original_pipeline_execute)  # noqa: B010
        setattr(Pipeline, "immediate_execute_command", original_immediate_execute)  # noqa: B010

    return restore
