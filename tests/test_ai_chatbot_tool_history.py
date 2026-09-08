from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from redis.exceptions import RedisError

from sophie_bot.modules.ai.utils.chatbot_tool_history import (
    collect_tool_call_ids,
    extract_tool_exchanges,
    get_tool_exchanges,
    remember_chatbot_tool_history,
    tool_history_key,
)

CHAT_TID = -100123


def _run_with_tool_call(
    tool_name: str = "web_search", tool_call_id: str = "call-1"
) -> list[ModelRequest | ModelResponse]:
    return [
        ModelRequest(parts=[UserPromptPart(content="who won?")]),
        ModelResponse(parts=[ToolCallPart(tool_name=tool_name, args={"query": "who won"}, tool_call_id=tool_call_id)]),
        ModelRequest(parts=[ToolReturnPart(tool_name=tool_name, content="they did", tool_call_id=tool_call_id)]),
        ModelResponse(parts=[TextPart(content="They did.")]),
    ]


def test_extract_keeps_complete_call_and_return_pairs() -> None:
    exchanges = extract_tool_exchanges(_run_with_tool_call(), max_content_chars=100)

    assert [type(message) for message in exchanges] == [ModelResponse, ModelRequest]
    assert isinstance(exchanges[0].parts[0], ToolCallPart)
    assert isinstance(exchanges[1].parts[0], ToolReturnPart)
    assert exchanges[1].parts[0].content == "they did"


def test_extract_drops_call_without_return() -> None:
    messages = _run_with_tool_call()
    del messages[2]

    assert extract_tool_exchanges(messages, max_content_chars=100) == []


def test_extract_skips_memory_tools() -> None:
    messages = _run_with_tool_call(tool_name="write_memory")

    assert extract_tool_exchanges(messages, max_content_chars=100) == []


def test_extract_skips_already_replayed_call_ids() -> None:
    messages = _run_with_tool_call()
    skipped = collect_tool_call_ids(messages[1:3])

    assert extract_tool_exchanges(messages, max_content_chars=100, skip_tool_call_ids=skipped) == []


def test_extract_truncates_long_tool_output() -> None:
    messages = _run_with_tool_call()
    messages[2] = ModelRequest(parts=[ToolReturnPart(tool_name="web_search", content="x" * 50, tool_call_id="call-1")])

    exchanges = extract_tool_exchanges(messages, max_content_chars=10)

    assert exchanges[1].parts[0].content == "x" * 10 + "... [truncated]"


async def test_get_tool_exchanges_drops_unreadable_payloads(
    test_redis: object,
) -> None:
    key = tool_history_key(CHAT_TID)
    await test_redis.hset(
        key,
        "10",
        ModelMessagesTypeAdapter.dump_json(_run_with_tool_call()[1:3]),
    )
    await test_redis.hset(key, "11", b'{"not": "a message list"}')
    await test_redis.hset(key, "not-a-message-id", b"[]")

    exchanges = await get_tool_exchanges(CHAT_TID, redis=test_redis)

    assert list(exchanges) == [10]
    # The poisoned entries are pruned, so the next reply does not re-parse them.
    assert sorted(await test_redis.hkeys(key)) == [b"10"]


async def test_remember_swallows_redis_failures(
    monkeypatch: pytest.MonkeyPatch,
    test_redis: object,
) -> None:
    def _fail(*args: object, **kwargs: object) -> None:
        raise RedisError("redis is down")

    monkeypatch.setattr(test_redis, "pipeline", _fail)

    with (
        patch("sophie_bot.modules.ai.utils.chatbot_tool_history.is_enabled", AsyncMock(return_value=True)),
        patch("sophie_bot.modules.ai.utils.chatbot_tool_history.get_value", AsyncMock(return_value=100)),
    ):
        await remember_chatbot_tool_history(
            CHAT_TID,
            42,
            _run_with_tool_call(),
            [],
            redis=test_redis,
        )
