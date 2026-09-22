from __future__ import annotations

import asyncio
import base64
import binascii
import json
import math
import time
from datetime import UTC, datetime
from decimal import InvalidOperation
from typing import Any, cast

from bson import json_util
from pydantic import JsonValue, ValidationError
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_core import PydanticSerializationError
from pymongo.errors import ConnectionFailure, PyMongoError, WTimeoutError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from debug.capture import REDACTED, TRUNCATED, TelemetrySink, known_secret_values, normalize_payload
from debug.i18n import gettext_debug as _
from debug.protocol import REPLY_DATA_LIMIT, SAFE_INTEGER_MAX, strict_json_loads
from sophie_bot.modules.ai.utils.ai_model_pricing import clear_model_pricing_cache
from sophie_bot.modules.ai.utils.cache_messages import (
    MESSAGE_CACHE_TTL,
    MessageType,
    get_message_cache_key,
    reset_messages,
)
from sophie_bot.modules.ai.utils.chatbot_tool_history import (
    TOOL_HISTORY_TTL,
    reset_tool_exchanges,
    tool_history_key,
)
from sophie_bot.runtime import BotModeRuntime

READ_TIMEOUT_SECONDS = 5
MONGO_MAX_TIME_MS = 2_000
MAX_PAGE_ITEMS = 200
MAX_REDIS_ARGUMENTS = 100
PRICING_CACHE_KEY = "sophie:ai:openrouter_pricing"
_PLACEHOLDERS = frozenset({REDACTED, TRUNCATED})
_SERVER_CODE_OPERATORS = frozenset({"$where", "$function", "$accumulator"})
_MUTATIONS = frozenset({"mongo.insert_one", "mongo.update_one", "mongo.delete_one", "redis.command", "ai_cache.clear"})


class WorkerActionError(RuntimeError):
    def __init__(self, code: str, message: str, *, ambiguous: bool = False) -> None:
        super().__init__(_(message))
        self.code = code
        self.ambiguous = ambiguous


def _flatten_string_values(value: Any, *, prefix: str = "") -> dict[str, str]:
    flattened: dict[str, str] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            child_prefix = f"{prefix}_{key}" if prefix else str(key)
            flattened.update(_flatten_string_values(item, prefix=child_prefix))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            flattened.update(_flatten_string_values(item, prefix=f"{prefix}_{index}"))
    elif isinstance(value, str) and value and value != "None":
        flattened[prefix] = value
    return flattened


def _contains_unsafe_integer(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return abs(value) > SAFE_INTEGER_MAX
    if isinstance(value, dict):
        return any(_contains_unsafe_integer(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_unsafe_integer(item) for item in value)
    return False


def _contains_forbidden_operator(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            key.casefold() in _SERVER_CODE_OPERATORS or _contains_forbidden_operator(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_operator(item) for item in value)
    return False


def _contains_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return value in _PLACEHOLDERS
    if isinstance(value, dict):
        return "$truncated" in value or any(_contains_placeholder(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_placeholder(item) for item in value)
    return False


def _decode_extended_json(value: Any) -> Any:
    if _contains_unsafe_integer(value):
        raise WorkerActionError(
            "unsafe_integer",
            "Integers outside JavaScript's safe range require canonical Extended JSON",
        )
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        return json_util.loads(encoded, json_options=json_util.CANONICAL_JSON_OPTIONS)
    except (TypeError, ValueError, KeyError, OverflowError, InvalidOperation, binascii.Error) as error:
        raise WorkerActionError("invalid_extended_json", "Invalid canonical Extended JSON") from error


def _collection_name(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 255 or "\x00" in value or value.startswith("system."):
        raise WorkerActionError(
            "invalid_collection", "Collection must be a non-system collection in the configured database"
        )
    return value


def _redis_bytes(value: Any) -> bytes:
    if isinstance(value, str):
        return value.encode("utf-8")
    if isinstance(value, dict) and set(value) == {"base64"} and isinstance(value["base64"], str):
        try:
            return base64.b64decode(value["base64"], validate=True)
        except (ValueError, binascii.Error) as error:
            raise WorkerActionError("invalid_redis_bytes", "Invalid base64 Redis value") from error
    raise WorkerActionError("invalid_redis_bytes", "Redis bytes must be a string or a base64 object")


def _redis_value(value: Any) -> JsonValue:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if not isinstance(value, bytes):
        raise WorkerActionError("invalid_redis_response", "Redis returned an unexpected value type")
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return {"base64": base64.b64encode(value).decode("ascii")}


def _strict_nonnegative_int(value: Any, name: str, *, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or (maximum is not None and value > maximum):
        raise WorkerActionError("invalid_input", f"{name} is outside the allowed range")
    return value


def _strict_positive_int(value: Any, name: str, *, maximum: int | None = None) -> int:
    result = _strict_nonnegative_int(value, name, maximum=maximum)
    if result == 0:
        raise WorkerActionError("invalid_input", f"{name} must be greater than zero")
    return result


def _valid_expiry_argument(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return 0 < value <= SAFE_INTEGER_MAX
    return isinstance(value, str) and value.isascii() and value.isdecimal() and 0 < int(value) <= SAFE_INTEGER_MAX


class WorkerActions:
    def __init__(self, runtime: BotModeRuntime, sink: TelemetrySink) -> None:
        self.runtime = runtime
        self.sink = sink
        self._known_secrets = known_secret_values(_flatten_string_values(runtime.config.model_dump(mode="json")))

    async def execute(self, operation: str, payload: dict[str, JsonValue]) -> JsonValue:
        try:
            if operation == "mongo.collections":
                return await self._read(self._mongo_collections())
            if operation == "mongo.query":
                return await self._read(self._mongo_query(payload))
            if operation == "redis.query":
                return await self._read(self._redis_query(payload))
            if operation == "ai_cache.messages":
                return await self._read(self._ai_messages(payload))
            if operation == "ai_cache.tools":
                return await self._read(self._ai_tools(payload))
            if operation == "ai_cache.pricing":
                return await self._read(self._ai_pricing())
            if operation == "mongo.insert_one":
                return await self._mongo_insert(payload)
            if operation == "mongo.update_one":
                return await self._mongo_update(payload)
            if operation == "mongo.delete_one":
                return await self._mongo_delete(payload)
            if operation == "redis.command":
                return await self._redis_command(payload)
            if operation == "ai_cache.clear":
                return await self._ai_clear(payload)
            raise WorkerActionError("unknown_operation", "Unsupported debugger operation")
        except WorkerActionError:
            raise
        except (PyMongoError, RedisError, OSError, ValueError, TypeError) as error:
            normalized, _truncated, _redacted = normalize_payload(str(error), self._known_secrets)
            message = normalized if isinstance(normalized, str) else type(error).__name__
            ambiguous = operation in _MUTATIONS and isinstance(
                error,
                (ConnectionFailure, WTimeoutError, RedisConnectionError, RedisTimeoutError, OSError),
            )
            raise WorkerActionError(
                "operation_failed",
                message,
                ambiguous=ambiguous,
            ) from error

    async def _read(self, awaitable: Any) -> JsonValue:
        try:
            async with asyncio.timeout(READ_TIMEOUT_SECONDS):
                return await awaitable
        except TimeoutError as error:
            raise WorkerActionError("read_timeout", "Inspector operation exceeded the 5 second limit") from error

    def _output(self, value: Any) -> JsonValue:
        normalized, truncated, _redacted = normalize_payload(value, self._known_secrets)
        if truncated and isinstance(normalized, dict):
            normalized.setdefault("truncated", True)
        try:
            size = len(json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode())
        except (TypeError, ValueError) as error:
            raise WorkerActionError("serialization_failed", "Result is not strict JSON") from error
        if size <= REPLY_DATA_LIMIT:
            return normalized
        if isinstance(normalized, dict):
            for field_name in ("collections", "items", "entries", "invalid_entries", "data"):
                values = normalized.get(field_name)
                if not isinstance(values, list):
                    continue
                while values and size > REPLY_DATA_LIMIT:
                    values.pop()
                    normalized["truncated"] = True
                    if "has_more" in normalized:
                        normalized["has_more"] = True
                    size = len(
                        json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
                    )
                if size <= REPLY_DATA_LIMIT:
                    return normalized
            for field_name in ("value", "data"):
                if field_name not in normalized or normalized[field_name] == TRUNCATED:
                    continue
                normalized[field_name] = TRUNCATED
                normalized["truncated"] = True
                if "has_more" in normalized:
                    normalized["has_more"] = True
                size = len(json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode())
                if size <= REPLY_DATA_LIMIT:
                    return normalized
        raise WorkerActionError("result_too_large", "Inspector result exceeded the 1 MiB response limit")

    async def _mongo_collections(self) -> JsonValue:
        names = await self.runtime.services.db.database.list_collection_names()
        return self._output({"collections": sorted(name for name in names if not name.startswith("system."))})

    async def _mongo_query(self, payload: dict[str, JsonValue]) -> JsonValue:
        started = time.monotonic()
        collection_name = _collection_name(payload.get("collection"))
        raw_filter = payload.get("filter", {})
        raw_projection = payload.get("projection")
        raw_sort = payload.get("sort", [])
        skip = _strict_nonnegative_int(payload.get("skip", 0), "skip", maximum=1_000_000)
        limit = _strict_positive_int(payload.get("limit", 50), "limit", maximum=MAX_PAGE_ITEMS)
        if not isinstance(raw_filter, dict) or _contains_forbidden_operator(raw_filter):
            raise WorkerActionError("invalid_query", "Mongo filter is invalid or contains a server-side code operator")
        if raw_projection is not None and not isinstance(raw_projection, dict):
            raise WorkerActionError("invalid_projection", "Mongo projection must be an object or null")
        if isinstance(raw_projection, dict) and any(
            not isinstance(key, str) or isinstance(value, bool) or value not in (0, 1)
            for key, value in raw_projection.items()
        ):
            raise WorkerActionError("invalid_projection", "Projection values must be 0 or 1")
        if not isinstance(raw_sort, list) or any(
            not isinstance(item, list)
            or len(item) != 2
            or not isinstance(item[0], str)
            or isinstance(item[1], bool)
            or item[1] not in (-1, 1)
            for item in raw_sort
        ):
            raise WorkerActionError("invalid_sort", "Sort must contain ordered field and direction pairs")
        raw_sort = cast(list[list[str | int]], raw_sort)
        mongo_filter = _decode_extended_json(raw_filter)
        projection = _decode_extended_json(raw_projection) if raw_projection is not None else None
        collection = self.runtime.services.db.database[collection_name]
        cursor = collection.find(mongo_filter, projection).skip(skip).limit(limit + 1).max_time_ms(MONGO_MAX_TIME_MS)
        if raw_sort:
            cursor = cursor.sort([(str(item[0]), int(item[1])) for item in raw_sort])
        try:
            documents = await cursor.to_list(length=limit + 1)
        finally:
            await cursor.close()
        has_more = len(documents) > limit
        result = {
            "items": documents[:limit],
            "has_more": has_more,
            "duration_ms": (time.monotonic() - started) * 1000,
            "truncated": has_more,
        }
        return self._output(result)

    async def _redis_query(self, payload: dict[str, JsonValue]) -> JsonValue:
        started = time.monotonic()
        operation = payload.get("op")
        limit = _strict_positive_int(payload.get("limit", 50), "limit", maximum=MAX_PAGE_ITEMS)
        redis = self.runtime.services.redis
        result: dict[str, Any]
        if operation == "scan":
            cursor = _strict_nonnegative_int(payload.get("cursor", 0), "cursor")
            pattern = _redis_bytes(payload.get("pattern", "*"))
            next_cursor, keys = await redis.scan(cursor=cursor, match=pattern, count=limit)
            data: JsonValue = [_redis_value(key) for key in list(keys)[:limit]]
            result = {
                "type": "scan",
                "ttl": None,
                "data": data,
                "next_cursor": int(next_cursor),
                "has_more": bool(next_cursor),
                "truncated": len(keys) > limit,
            }
        else:
            key = _redis_bytes(payload.get("key"))
            raw_type = await redis.type(key)
            redis_type = raw_type.decode("ascii", errors="replace") if isinstance(raw_type, bytes) else str(raw_type)
            ttl = int(await redis.ttl(key))
            if redis_type == "none":
                result = {
                    "type": "missing",
                    "ttl": ttl,
                    "data": None,
                    "next_cursor": 0,
                    "has_more": False,
                    "truncated": False,
                }
            elif operation == "string":
                self._require_redis_type(redis_type, "string")
                offset = _strict_nonnegative_int(payload.get("offset", 0), "offset")
                length = int(await redis.strlen(key))
                value = await redis.getrange(key, offset, offset + limit - 1)
                result = {
                    "type": "string",
                    "ttl": ttl,
                    "data": _redis_value(value),
                    "next_cursor": 0,
                    "has_more": offset + limit < length,
                    "truncated": offset + limit < length,
                    "length": length,
                }
            elif operation == "hash":
                self._require_redis_type(redis_type, "hash")
                cursor = _strict_nonnegative_int(payload.get("cursor", 0), "cursor")
                next_cursor, values = await redis.hscan(key, cursor=cursor, count=limit)
                if not isinstance(values, dict):
                    raise WorkerActionError("invalid_redis_response", "Redis HSCAN returned an unexpected value")
                pairs = [[_redis_value(field), _redis_value(value)] for field, value in list(values.items())[:limit]]
                result = {
                    "type": "hash",
                    "ttl": ttl,
                    "data": pairs,
                    "next_cursor": int(next_cursor),
                    "has_more": bool(next_cursor),
                    "truncated": len(values) > limit,
                }
            elif operation == "set":
                self._require_redis_type(redis_type, "set")
                cursor = _strict_nonnegative_int(payload.get("cursor", 0), "cursor")
                next_cursor, values = await redis.sscan(key, cursor=cursor, count=limit)
                result = {
                    "type": "set",
                    "ttl": ttl,
                    "data": [_redis_value(value) for value in list(values)[:limit]],
                    "next_cursor": int(next_cursor),
                    "has_more": bool(next_cursor),
                    "truncated": len(values) > limit,
                }
            elif operation == "list":
                self._require_redis_type(redis_type, "list")
                offset = _strict_nonnegative_int(payload.get("offset", 0), "offset")
                length = int(await redis.llen(key))
                values = await redis.lrange(key, offset, offset + limit - 1)
                result = {
                    "type": "list",
                    "ttl": ttl,
                    "data": [_redis_value(value) for value in values],
                    "next_cursor": 0,
                    "has_more": offset + limit < length,
                    "truncated": offset + limit < length,
                    "length": length,
                }
            elif operation == "zset":
                self._require_redis_type(redis_type, "zset")
                offset = _strict_nonnegative_int(payload.get("offset", 0), "offset")
                length = int(await redis.zcard(key))
                values = await redis.zrange(key, offset, offset + limit - 1, withscores=True)
                result = {
                    "type": "zset",
                    "ttl": ttl,
                    "data": [[_redis_value(value), score] for value, score in values],
                    "next_cursor": 0,
                    "has_more": offset + limit < length,
                    "truncated": offset + limit < length,
                    "length": length,
                }
            else:
                raise WorkerActionError("invalid_redis_operation", "Unsupported Redis inspector operation")
        result["duration_ms"] = (time.monotonic() - started) * 1000
        return self._output(result)

    @staticmethod
    def _require_redis_type(actual: str, expected: str) -> None:
        if actual != expected:
            raise WorkerActionError("redis_type_mismatch", f"Key contains {actual}, not {expected}")

    async def _ai_messages(self, payload: dict[str, JsonValue]) -> JsonValue:
        chat_tid = self._chat_tid(payload.get("chat_tid"))
        offset = _strict_nonnegative_int(payload.get("offset", 0), "offset")
        limit = _strict_positive_int(payload.get("limit", 50), "limit", maximum=MAX_PAGE_ITEMS)
        key = get_message_cache_key(chat_tid)
        redis = self.runtime.services.redis
        now = datetime.now(UTC)
        cutoff = now - MESSAGE_CACHE_TTL
        rows = await redis.zrangebyscore(
            key, cutoff.timestamp(), now.timestamp(), start=offset, num=limit + 1, withscores=True
        )
        ttl = int(await redis.ttl(key))
        count = int(await redis.zcount(key, cutoff.timestamp(), now.timestamp()))
        entries: list[Any] = []
        invalid_entries: list[dict[str, JsonValue]] = []
        for index, (raw, score) in enumerate(rows[:limit], start=offset):
            if not isinstance(raw, (str, bytes)):
                invalid_entries.append({"index": index, "score": score, "error": "invalid_value_type"})
                continue
            try:
                message = MessageType.model_validate_json(raw)
                entries.append(message.model_dump(mode="json"))
            except (ValidationError, ValueError, TypeError) as error:
                invalid_entries.append(
                    {
                        "index": index,
                        "score": score,
                        "error": type(error).__name__,
                        "value": _redis_value(raw),
                    }
                )
        return self._output(
            {
                "kind": "messages",
                "key": key,
                "ttl": ttl,
                "configured_ttl_seconds": int(MESSAGE_CACHE_TTL.total_seconds()),
                "count": count,
                "entries": entries,
                "invalid_entries": invalid_entries,
                "offset": offset,
                "has_more": len(rows) > limit,
                "truncated": len(rows) > limit,
            }
        )

    async def _ai_tools(self, payload: dict[str, JsonValue]) -> JsonValue:
        chat_tid = self._chat_tid(payload.get("chat_tid"))
        cursor = _strict_nonnegative_int(payload.get("cursor", 0), "cursor")
        limit = _strict_positive_int(payload.get("limit", 50), "limit", maximum=MAX_PAGE_ITEMS)
        key = tool_history_key(chat_tid)
        redis = self.runtime.services.redis
        next_cursor, rows = await redis.hscan(key, cursor=cursor, count=limit)
        if not isinstance(rows, dict):
            raise WorkerActionError("invalid_redis_response", "Redis HSCAN returned an unexpected value")
        ttl = int(await redis.ttl(key))
        count = int(await redis.hlen(key))
        entries: list[dict[str, Any]] = []
        invalid_entries: list[dict[str, JsonValue]] = []
        for raw_field, raw_payload in list(rows.items())[:limit]:
            field_value = _redis_value(raw_field)
            if not isinstance(raw_payload, (str, bytes)):
                invalid_entries.append({"field": field_value, "error": "invalid_value_type"})
                continue
            stored_value = _redis_value(raw_payload)
            field = field_value if isinstance(field_value, str) else None
            if field is None or not field.isdigit():
                invalid_entries.append({"field": field_value, "error": "invalid_message_id", "value": stored_value})
                continue
            try:
                exchanges = list(ModelMessagesTypeAdapter.validate_json(raw_payload))
                serialized = ModelMessagesTypeAdapter.dump_python(exchanges, mode="json")
                entries.append(
                    {
                        "message_id": int(field),
                        "exchanges": serialized,
                        "replay_status": "not_evaluated",
                        "replay_note": _("ai_chatbot_tool_history controls use, not storage visibility"),
                    }
                )
            except (ValidationError, PydanticSerializationError, ValueError, TypeError) as error:
                invalid_entries.append({"field": field, "error": type(error).__name__, "value": stored_value})
        entries.sort(key=lambda item: int(item["message_id"]))
        return self._output(
            {
                "kind": "tools",
                "key": key,
                "ttl": ttl,
                "configured_ttl_seconds": int(TOOL_HISTORY_TTL.total_seconds()),
                "count": count,
                "entries": entries,
                "invalid_entries": invalid_entries,
                "next_cursor": int(next_cursor),
                "has_more": bool(next_cursor),
                "truncated": len(rows) > limit,
                "replay_status": "not_evaluated",
            }
        )

    async def _ai_pricing(self) -> JsonValue:
        redis = self.runtime.services.redis
        raw = await redis.get(PRICING_CACHE_KEY)
        ttl = int(await redis.ttl(PRICING_CACHE_KEY))
        value: JsonValue = None
        invalid_entry: dict[str, JsonValue] | None = None
        if raw is not None:
            if not isinstance(raw, (str, bytes)):
                invalid_entry = {"error": "invalid_value_type"}
            else:
                try:
                    value = strict_json_loads(raw)
                except (ValueError, TypeError):
                    invalid_entry = {"error": "invalid_stored_json", "value": _redis_value(raw)}
        return self._output(
            {
                "kind": "pricing",
                "key": PRICING_CACHE_KEY,
                "ttl": ttl,
                "value": value,
                "missing": raw is None,
                "invalid_entry": invalid_entry,
            }
        )

    async def _mongo_insert(self, payload: dict[str, JsonValue]) -> JsonValue:
        collection_name = _collection_name(payload.get("collection"))
        raw_document = payload.get("document")
        if not isinstance(raw_document, dict) or not raw_document or _contains_placeholder(raw_document):
            raise WorkerActionError(
                "invalid_document", "Insert document must be a non-empty object without placeholders"
            )
        document = _decode_extended_json(raw_document)
        result = await self.runtime.services.db.database[collection_name].insert_one(document)
        return self._output({"acknowledged": result.acknowledged, "inserted_id": result.inserted_id})

    async def _mongo_update(self, payload: dict[str, JsonValue]) -> JsonValue:
        collection_name = _collection_name(payload.get("collection"))
        raw_id = payload.get("_id")
        raw_update = payload.get("update")
        if (
            raw_id is None
            or _contains_placeholder(raw_id)
            or not isinstance(raw_update, dict)
            or not raw_update
            or _contains_placeholder(raw_update)
        ):
            raise WorkerActionError("invalid_update", "Update requires an exact _id and non-empty update")
        if set(raw_update) - {"$set", "$unset", "$inc"}:
            raise WorkerActionError("invalid_update", "Only $set, $unset and $inc are allowed")
        if any(not isinstance(value, dict) or not value for value in raw_update.values()):
            raise WorkerActionError("invalid_update", "Every update operator must contain fields")
        raw_update = cast(dict[str, dict[str, JsonValue]], raw_update)
        if any(field == "_id" or field.startswith("_id.") for fields in raw_update.values() for field in fields):
            raise WorkerActionError("invalid_update", "The _id field cannot be edited")
        update = _decode_extended_json(raw_update)
        document_id = _decode_extended_json(raw_id)
        result = await self.runtime.services.db.database[collection_name].update_one(
            {"_id": document_id}, update, upsert=False
        )
        return self._output(
            {
                "acknowledged": result.acknowledged,
                "matched_count": result.matched_count,
                "modified_count": result.modified_count,
            }
        )

    async def _mongo_delete(self, payload: dict[str, JsonValue]) -> JsonValue:
        collection_name = _collection_name(payload.get("collection"))
        raw_id = payload.get("_id")
        if raw_id is None or _contains_placeholder(raw_id):
            raise WorkerActionError("invalid_delete", "Delete requires an exact _id without placeholders")
        document_id = _decode_extended_json(raw_id)
        result = await self.runtime.services.db.database[collection_name].delete_one({"_id": document_id})
        return self._output({"acknowledged": result.acknowledged, "deleted_count": result.deleted_count})

    async def _redis_command(self, payload: dict[str, JsonValue]) -> JsonValue:
        command = payload.get("command")
        raw_args = payload.get("args")
        if not isinstance(command, str) or not isinstance(raw_args, list) or _contains_placeholder(raw_args):
            raise WorkerActionError("invalid_redis_command", "Redis command and arguments are invalid")
        command = command.upper()
        self._validate_redis_command(command, raw_args)
        args = [self._redis_argument(value) for value in raw_args]
        result = await self.runtime.services.redis.execute_command(command, *args)
        return self._output({"command": command, "result": result})

    @staticmethod
    def _redis_argument(value: JsonValue) -> bytes | str | int | float:
        if isinstance(value, bool):
            raise WorkerActionError("invalid_redis_argument", "Boolean Redis arguments are not allowed")
        if isinstance(value, int):
            if abs(value) > SAFE_INTEGER_MAX:
                raise WorkerActionError("unsafe_integer", "Large Redis integers must be supplied as strings")
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                raise WorkerActionError("invalid_redis_argument", "Non-finite Redis arguments are not allowed")
            return value
        if isinstance(value, (str, dict)):
            return _redis_bytes(value)
        raise WorkerActionError("invalid_redis_argument", "Redis arguments must be strings, numbers, or base64 objects")

    @staticmethod
    def _validate_redis_command(command: str, args: list[JsonValue]) -> None:
        if not args or not isinstance(args[0], (str, dict)):
            raise WorkerActionError("invalid_redis_command", "The first Redis argument must be one RedisBytes key")
        _redis_bytes(args[0])
        if len(args) > MAX_REDIS_ARGUMENTS:
            raise WorkerActionError("invalid_redis_command", "Too many Redis arguments")
        lengths: dict[str, tuple[int, int | None]] = {
            "DEL": (1, 1),
            "UNLINK": (1, 1),
            "HDEL": (2, None),
            "ZREM": (2, None),
            "EXPIRE": (2, 2),
            "PERSIST": (1, 1),
        }
        if command in lengths:
            minimum, maximum = lengths[command]
            if len(args) < minimum or (maximum is not None and len(args) > maximum):
                raise WorkerActionError("invalid_redis_command", f"Invalid {command} argument count")
            if command == "EXPIRE" and not _valid_expiry_argument(args[1]):
                raise WorkerActionError("invalid_redis_command", "EXPIRE requires a positive integer TTL")
            return
        if command == "HSET":
            if len(args) < 3 or len(args) % 2 == 0:
                raise WorkerActionError("invalid_redis_command", "HSET requires one key and field/value pairs")
            return
        if command == "ZADD":
            if len(args) < 3 or len(args) % 2 == 0:
                raise WorkerActionError("invalid_redis_command", "ZADD requires one key and score/member pairs")
            for score in args[1::2]:
                if isinstance(score, bool) or not isinstance(score, (int, float, str)):
                    raise WorkerActionError("invalid_redis_command", "ZADD scores must be finite numbers")
                try:
                    numeric = float(score)
                except ValueError as error:
                    raise WorkerActionError("invalid_redis_command", "ZADD scores must be finite numbers") from error
                if not math.isfinite(numeric):
                    raise WorkerActionError("invalid_redis_command", "ZADD scores must be finite numbers")
            return
        if command == "SET":
            if len(args) < 2:
                raise WorkerActionError("invalid_redis_command", "SET requires one key and one value")
            index = 2
            seen_expiry = False
            seen_condition = False
            while index < len(args):
                option_value = args[index]
                if not isinstance(option_value, str):
                    raise WorkerActionError("invalid_redis_command", "SET options must be text")
                option = option_value.upper()
                if option in {"EX", "PX"} and not seen_expiry and index + 1 < len(args):
                    expiry = args[index + 1]
                    valid_expiry = _valid_expiry_argument(expiry)
                    if not valid_expiry:
                        raise WorkerActionError("invalid_redis_command", "SET expiry must be a positive integer")
                    seen_expiry = True
                    index += 2
                    continue
                if option in {"NX", "XX"} and not seen_condition:
                    seen_condition = True
                    index += 1
                    continue
                raise WorkerActionError("invalid_redis_command", "SET supports only EX/PX and NX/XX")
            return
        raise WorkerActionError("invalid_redis_command", "Redis command is not allowlisted")

    async def _ai_clear(self, payload: dict[str, JsonValue]) -> JsonValue:
        cache_kind = payload.get("cache_kind")
        chat_tid_value = payload.get("chat_tid")
        redis = self.runtime.services.redis
        if cache_kind == "pricing":
            if chat_tid_value is not None:
                raise WorkerActionError("invalid_ai_cache_clear", "Pricing cache is global and forbids chat_tid")
            await clear_model_pricing_cache(redis=redis)
            return self._output({"cache_kind": "pricing", "cleared": True})
        chat_tid = self._chat_tid(chat_tid_value)
        if cache_kind == "messages":
            await reset_messages(chat_tid, redis=redis)
        elif cache_kind == "tools":
            await reset_tool_exchanges(chat_tid, redis=redis)
        elif cache_kind == "context":
            await reset_messages(chat_tid, redis=redis)
            await reset_tool_exchanges(chat_tid, redis=redis)
        else:
            raise WorkerActionError("invalid_ai_cache_clear", "Unsupported AI cache clear scope")
        return self._output({"cache_kind": cache_kind, "chat_tid": chat_tid, "cleared": True})

    @staticmethod
    def _chat_tid(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or abs(value) > SAFE_INTEGER_MAX:
            raise WorkerActionError("invalid_chat_tid", "chat_tid must be a safe Telegram integer")
        return value
