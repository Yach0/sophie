from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import timedelta
from typing import Final

from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)

from sophie_bot.services.redis import aredis
from sophie_bot.utils.feature_flags import get_value, is_enabled

ToolExchange = ModelRequest | ModelResponse

# Matches the message cache TTL: a replayed tool result is only useful while the surrounding
# conversation is still reconstructable.
TOOL_HISTORY_TTL: Final = timedelta(hours=48)

# Keeps the replayed context bounded — only the newest few answers carry their tool exchanges.
TOOL_HISTORY_RUN_LIMIT: Final = 5

# Memory writes are side effects whose outcome is re-injected into the system prompt on every run,
# so replaying the calls themselves only adds stale noise.
NON_REPLAYABLE_TOOLS: Final = frozenset({"write_memory", "forget_memory"})

_TRUNCATION_MARKER: Final = "... [truncated]"


def tool_history_key(chat_tid: int) -> str:
    """Builds the Redis key holding the per-answer tool exchanges of a chat."""
    return f"ai:tool_history:{chat_tid}"


def _decode_field(raw_field: bytes | str) -> str:
    return raw_field.decode() if isinstance(raw_field, bytes) else raw_field


def _truncate_tool_content(content: object, max_chars: int) -> object:
    """Cap textual tool output so a single research result cannot dominate the next prompt."""
    if not isinstance(content, str) or max_chars <= 0 or len(content) <= max_chars:
        return content
    return content[:max_chars] + _TRUNCATION_MARKER


def collect_tool_call_ids(messages: Iterable[ToolExchange]) -> set[str]:
    """Tool call IDs already present in a history, used to skip re-storing replayed exchanges."""
    return {
        part.tool_call_id
        for message in messages
        for part in message.parts
        if isinstance(part, (ToolCallPart, ToolReturnPart))
    }


def extract_tool_exchanges(
    messages: Sequence[ToolExchange],
    *,
    max_content_chars: int,
    skip_tool_call_ids: Iterable[str] = (),
) -> list[ToolExchange]:
    """Extract complete call/return tool pairs from a finished run.

    Providers reject a tool call without its result, so a call is only kept when its return part is
    present in the same run. Parts are rebuilt rather than reused so the stored exchange carries no
    token usage of its own and cannot be counted twice when it is replayed.
    """
    skipped = set(skip_tool_call_ids)
    returns: dict[str, ToolReturnPart] = {
        part.tool_call_id: part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
        and part.tool_call_id not in skipped
        and part.tool_name not in NON_REPLAYABLE_TOOLS
    }
    if not returns:
        return []

    exchanges: list[ToolExchange] = []
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        calls = [part for part in message.parts if isinstance(part, ToolCallPart) and part.tool_call_id in returns]
        if not calls:
            continue
        exchanges.append(
            ModelResponse(
                parts=[
                    ToolCallPart(tool_name=call.tool_name, args=call.args, tool_call_id=call.tool_call_id)
                    for call in calls
                ]
            )
        )
        exchanges.append(
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name=returns[call.tool_call_id].tool_name,
                        content=_truncate_tool_content(returns[call.tool_call_id].content, max_content_chars),
                        tool_call_id=call.tool_call_id,
                    )
                    for call in calls
                ]
            )
        )
    return exchanges


async def _trim_tool_history(chat_tid: int) -> None:
    key = tool_history_key(chat_tid)
    raw_fields = await aredis.hkeys(key)  # type: ignore[misc]
    message_ids = sorted(int(field) for raw_field in raw_fields if (field := _decode_field(raw_field)).isdigit())
    if len(message_ids) <= TOOL_HISTORY_RUN_LIMIT:
        return
    await aredis.hdel(key, *(str(message_id) for message_id in message_ids[:-TOOL_HISTORY_RUN_LIMIT]))  # type: ignore[misc]


async def store_tool_exchanges(chat_tid: int, message_id: int, exchanges: Sequence[ToolExchange]) -> None:
    """Persist the tool exchanges that produced the answer sent as ``message_id``."""
    if not exchanges:
        return
    key = tool_history_key(chat_tid)
    payload = ModelMessagesTypeAdapter.dump_json(list(exchanges))
    async with aredis.pipeline(transaction=True) as pipe:
        await pipe.hset(key, str(message_id), payload)  # type: ignore[misc]
        await pipe.expire(key, int(TOOL_HISTORY_TTL.total_seconds()))
        await pipe.execute()
    await _trim_tool_history(chat_tid)


async def get_tool_exchanges(chat_tid: int) -> dict[int, list[ToolExchange]]:
    """Stored tool exchanges of a chat, keyed by the bot message the answer was sent as."""
    raw_exchanges = await aredis.hgetall(tool_history_key(chat_tid))  # type: ignore[misc]
    return {
        int(field): list(ModelMessagesTypeAdapter.validate_json(raw_payload))
        for raw_field, raw_payload in raw_exchanges.items()
        if (field := _decode_field(raw_field)).isdigit()
    }


async def reset_tool_exchanges(chat_tid: int) -> None:
    """Drops every stored tool exchange of a chat."""
    await aredis.delete(tool_history_key(chat_tid))


async def load_chatbot_tool_history(chat_tid: int) -> dict[int, list[ToolExchange]]:
    if not await is_enabled("ai_chatbot_tool_history", chat_tid=chat_tid):
        return {}
    return await get_tool_exchanges(chat_tid)


async def remember_chatbot_tool_history(
    chat_tid: int,
    message_id: int,
    message_history: Sequence[ToolExchange],
    previous_history: Sequence[ToolExchange],
) -> None:
    """Store the tool exchanges a finished chatbot run performed, excluding replayed ones."""
    if not await is_enabled("ai_chatbot_tool_history", chat_tid=chat_tid):
        return
    max_content_chars = int(await get_value("ai_chatbot_tool_history_max_chars", chat_tid=chat_tid))
    exchanges = extract_tool_exchanges(
        message_history,
        max_content_chars=max_content_chars,
        skip_tool_call_ids=collect_tool_call_ids(previous_history),
    )
    await store_tool_exchanges(chat_tid, message_id, exchanges)
