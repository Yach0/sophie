from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from functools import partial
from types import SimpleNamespace
from typing import Literal
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, User
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage
from redis.asyncio.client import Pipeline
from redis.exceptions import ConnectionError, ResponseError, TimeoutError, WatchError
from stfu_tg import Doc
from tenacity import AsyncRetrying

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel
from sophie_bot.db.models.chat import ChatType
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.ai.utils import ai_chatbot_reply as chatbot_reply
from sophie_bot.modules.ai.utils import cache_messages, chatbot_tool_history, proactive_replies
from sophie_bot.modules.ai.utils.ai_run import AIAgentResult
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext
from sophie_bot.modules.ai.utils.cache_messages import MessageType, get_cached_messages
from sophie_bot.modules.ai.utils.chatbot_tool_history import get_tool_exchanges
from sophie_bot.modules.ai.utils.modern_context import ModernContext
from sophie_bot.modules.ai.utils.old_context import OldContext
from sophie_bot.services.application import ApplicationServices
from tests.utils.ai_tokenizers import bundled_test_tokenizers  # noqa: F401


@pytest.mark.parametrize("tool_history", [False, True])
@pytest.mark.parametrize("failure", ["before", "lost_ack", "watch_conflict"])
@pytest.mark.parametrize("initial_epoch", [0, 7])
async def test_legacy_persistence_does_not_restore_context_reset_between_attempts(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
    tool_history: bool,
    failure: Literal["before", "lost_ack", "watch_conflict"],
    initial_epoch: int,
) -> None:
    redis = test_services.redis
    chat_tid = -100123
    epoch_key = cache_messages.get_context_epoch_key(chat_tid)
    if initial_epoch:
        await redis.set(epoch_key, initial_epoch)
    message = MessageType(user_id=123, message_id=8, text="answer", created_at=datetime.now(UTC))
    exchanges = [
        ModelResponse(parts=[ToolCallPart(tool_name="web_search", args={}, tool_call_id="call-1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="web_search", content="result", tool_call_id="call-1")]),
    ]
    module = chatbot_tool_history if tool_history else cache_messages
    original_execute = Pipeline.execute
    attempts = 0
    sleeps = 0

    async def persist() -> None:
        if tool_history:
            await chatbot_tool_history.store_tool_exchanges(chat_tid, message.message_id, exchanges, redis=redis)
        else:
            await cache_messages.store_delivered_message(chat_tid, message, redis=redis)

    async def execute(pipe: Pipeline, raise_on_error: bool = True) -> list[object]:
        nonlocal attempts
        target_command = "HSET" if tool_history else "ZADD"
        if not any(args[0] == target_command for args, _options in pipe.command_stack):
            return await original_execute(pipe, raise_on_error)
        attempts += 1
        if attempts == 1:
            if failure == "before":
                raise ConnectionError("unavailable before EXEC")
            if failure == "watch_conflict":
                # Change a watched key after the check, before Redis processes EXEC.
                await redis.incr(epoch_key)
            await original_execute(pipe, raise_on_error)
            assert failure == "lost_ack"
            assert await redis.exists(
                chatbot_tool_history.tool_history_key(chat_tid)
                if tool_history
                else cache_messages.get_message_cache_key(chat_tid)
            )
            raise TimeoutError("EXEC succeeded but acknowledgment was lost")
        return await original_execute(pipe, raise_on_error)

    async def reset_during_retry_delay(seconds: float) -> None:
        nonlocal sleeps
        sleeps += 1
        assert seconds == 0.1
        await ModernContext.reset(chat_tid, redis=redis)
        await chatbot_tool_history.reset_tool_exchanges(chat_tid, redis=redis)

    monkeypatch.setattr(Pipeline, "execute", execute)
    monkeypatch.setattr(module, "AsyncRetrying", partial(AsyncRetrying, sleep=reset_during_retry_delay))

    await persist()

    assert attempts == 1
    assert sleeps == 1
    assert await get_cached_messages(chat_tid, redis=redis) == ()
    assert await get_tool_exchanges(chat_tid, redis=redis) == {}

    # A fresh operation may persist in the new epoch.
    await persist()
    assert attempts == 2
    if tool_history:
        assert list(await get_tool_exchanges(chat_tid, redis=redis)) == [message.message_id]
    else:
        assert await get_cached_messages(chat_tid, redis=redis) == (message,)


@pytest.mark.parametrize(
    ("modern", "proactive", "tool_failure", "failure"),
    [
        (modern, proactive, tool_failure, failure)
        for modern, proactive, tool_failure in [
            (True, False, False),
            (True, True, False),
            (False, False, False),
            (False, True, False),
            (False, False, True),
        ]
        for failure in ["before", "lost_ack", "exhausted", "watch_exhausted", "response_error", "cancelled"]
    ]
    + [
        pytest.param(False, False, False, "reset_during_delay", id="legacy-cache-lost-ack-reset-before-tool-history"),
        pytest.param(False, False, False, "reset_during_config", id="legacy-cache-success-reset-during-tool-config"),
    ]
    + [
        (False, proactive, False, failure)
        for proactive in [False, True]
        for failure in [
            "epoch_connection",
            "epoch_timeout",
            "epoch_exhausted",
            "reset_before_watch",
            "reset_before_get",
        ]
    ],
)
async def test_delivered_reply_retries_only_prepared_persistence(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
    modern: bool,
    proactive: bool,
    tool_failure: bool,
    failure: Literal[
        "before",
        "lost_ack",
        "exhausted",
        "watch_exhausted",
        "response_error",
        "cancelled",
        "reset_during_delay",
        "reset_during_config",
        "epoch_connection",
        "epoch_timeout",
        "epoch_exhausted",
        "reset_before_watch",
        "reset_before_get",
    ],
) -> None:
    chat = ChatModel(
        tid=-100123,
        type=ChatType.supergroup,
        first_name_or_title="Group",
        username=None,
        is_bot=False,
        last_saw=datetime.now(UTC),
    )
    connection = ChatConnection(type=chat.type, is_connected=False, tid=chat.tid, title="Group", db_model=chat)
    source = Message(
        message_id=7,
        date=datetime.now(UTC),
        chat=Chat(id=chat.tid, type="supergroup"),
        from_user=User(id=123, is_bot=False, first_name="Alice"),
        text="question",
    )
    sent = source.model_copy(
        update={
            "message_id": 8,
            "text": "answer",
            "from_user": User(id=CONFIG.bot_id, is_bot=True, first_name="Sophie"),
        }
    )
    context = SophieAIToolContext(
        connection=connection, chat_tid=chat.tid, chat_iid=chat.iid, user_tid=123, services=test_services
    )
    history = (
        await ModernContext.build(source, context, token_budget=100000, instructions="Be helpful.", runtime_context="")
        if modern
        else OldContext(services=test_services)
    )
    native = [
        ModelRequest(parts=[UserPromptPart(content=history.prompt)]),
        ModelResponse(parts=[ToolCallPart(tool_name="web_search", args={}, tool_call_id="call-1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="web_search", content="result", tool_call_id="call-1")]),
        ModelResponse(parts=[TextPart(content="answer")]),
    ]
    run = AsyncMock(
        return_value=AIAgentResult(output="answer", message_history=native, new_messages=native, usage=RunUsage())
    )
    send = AsyncMock(return_value=sent)
    prepare = AsyncMock(return_value=history)
    module = proactive_replies if proactive else chatbot_reply
    model = TestModel()
    plan = AsyncMock(return_value=SimpleNamespace(primary=model))
    monkeypatch.setattr(module, "get_chat_default_model_plan" if proactive else "_resolve_model_plan", plan)
    monkeypatch.setattr(module, "_build_answer_history" if proactive else "prepare_chatbot_history", prepare)
    monkeypatch.setattr(module, "send_ai_rich_message_to_chat" if proactive else "send_ai_rich_message", send)
    monkeypatch.setattr(module, "run_chatbot", run)
    monkeypatch.setattr(module, "is_enabled", AsyncMock(side_effect=lambda feature, **kwargs: feature == "ai_chatbot"))
    monkeypatch.setattr(module, "resolve_chat_service_tier", AsyncMock(return_value=None))
    monkeypatch.setattr(module, "build_chatbot_header", AsyncMock(return_value="header"))
    monkeypatch.setattr(
        module, "build_reply_doc" if proactive else "_build_fitting_reply_doc", AsyncMock(return_value=Doc("answer"))
    )
    if not proactive:
        monkeypatch.setattr(module, "build_message_streamer", AsyncMock(return_value=None))
        monkeypatch.setattr(module, "should_offer_help_mode", AsyncMock(return_value=False))
    tool_config = AsyncMock(return_value=100)
    if failure == "reset_during_config":

        async def reset_during_tool_config(*args: object, **kwargs: object) -> int:
            assert [
                (row.message_id, row.text) for row in await get_cached_messages(chat.tid, redis=test_services.redis)
            ] == [(8, "answer")]
            await ModernContext.reset(chat.tid, redis=test_services.redis)
            await chatbot_tool_history.reset_tool_exchanges(chat.tid, redis=test_services.redis)
            return 100

        tool_config.side_effect = reset_during_tool_config
    monkeypatch.setattr(chatbot_tool_history, "get_value", tool_config)

    epoch_key = cache_messages.get_context_epoch_key(chat.tid)
    if not modern:
        await test_services.redis.set(epoch_key, 7)
    original_get = test_services.redis.get
    epoch_reads = 0

    async def read_epoch(key: str) -> bytes | None:
        nonlocal epoch_reads
        if key == epoch_key:
            epoch_reads += 1
            if failure.startswith("epoch_"):
                run.assert_not_awaited()
                send.assert_not_awaited()
                if failure == "epoch_exhausted" or epoch_reads < 3:
                    raise (
                        TimeoutError("epoch unavailable")
                        if failure == "epoch_timeout"
                        else ConnectionError("epoch unavailable")
                    )
        return await original_get(key)

    if failure.startswith("epoch_"):
        monkeypatch.setattr(test_services.redis, "get", read_epoch)

    initial_read_failures = 0
    original_watch = Pipeline.watch
    original_pipeline_get = Pipeline.get

    async def watch(pipe: Pipeline, *keys: str) -> None:
        nonlocal initial_read_failures
        if failure == "reset_before_watch" and epoch_key in keys and initial_read_failures == 0:
            send.assert_awaited_once()
            initial_read_failures += 1
            raise ConnectionError("unavailable before WATCH")
        await original_watch(pipe, *keys)

    async def pipeline_get(pipe: Pipeline, key: str) -> bytes | None:
        nonlocal initial_read_failures
        if failure == "reset_before_get" and key == epoch_key and initial_read_failures == 0:
            send.assert_awaited_once()
            initial_read_failures += 1
            raise TimeoutError("unavailable before GET")
        return await original_pipeline_get(pipe, key)

    if failure in {"reset_before_watch", "reset_before_get"}:
        monkeypatch.setattr(Pipeline, "watch", watch)
        monkeypatch.setattr(Pipeline, "get", pipeline_get)

    original_execute = Pipeline.execute
    attempts = 0
    snapshots: list[tuple[object, ...]] = []

    async def execute(pipe: Pipeline, raise_on_error: bool = True) -> list[object]:
        nonlocal attempts
        target_command = "HSET" if tool_failure else "SET" if modern else "ZADD"
        target = next((args for args, _options in pipe.command_stack if args[0] == target_command), None)
        if target is None:
            return await original_execute(pipe, raise_on_error)
        attempts += 1
        snapshots.append(tuple(target))
        if isinstance(history, ModernContext):
            assert not history._closed
            assert history._renewal is not None and not history._renewal.done()
            assert await test_services.redis.get(history._lock_key) == history._owner.encode()
        if failure == "response_error":
            raise ResponseError("invalid persistence command")
        if failure == "cancelled":
            raise asyncio.CancelledError
        if failure == "watch_exhausted":
            raise WatchError("connection lost while watching")
        if failure == "exhausted" or (failure == "before" and attempts < 3):
            raise ConnectionError("unavailable")
        result = await original_execute(pipe, raise_on_error)
        if failure in {"lost_ack", "reset_during_delay"} and attempts == 1:
            # Redis applied EXEC; the caller never received its successful result.
            if failure == "reset_during_delay":
                assert [
                    (row.message_id, row.text) for row in await get_cached_messages(chat.tid, redis=test_services.redis)
                ] == [(8, "answer")]
            raise WatchError("connection lost while watching") if modern else TimeoutError("lost acknowledgment")
        return result

    monkeypatch.setattr(Pipeline, "execute", execute)

    sleeps = 0

    async def reset_during_retry_delay(seconds: float) -> None:
        nonlocal sleeps
        sleeps += 1
        assert seconds == 0.1
        await ModernContext.reset(chat.tid, redis=test_services.redis)
        await chatbot_tool_history.reset_tool_exchanges(chat.tid, redis=test_services.redis)

    if failure in {"reset_during_delay", "reset_before_watch", "reset_before_get"}:
        monkeypatch.setattr(cache_messages, "AsyncRetrying", partial(AsyncRetrying, sleep=reset_during_retry_delay))

    async def reply() -> None:
        if proactive:
            await proactive_replies._answer_message(
                chat.tid,
                chat,
                MessageType(user_id=123, message_id=7, text="question", created_at=source.date),
                services=test_services,
            )
        else:
            assert await chatbot_reply.ai_chatbot_reply(source, connection, services=test_services) is sent

    if failure in {"exhausted", "watch_exhausted", "response_error", "cancelled", "epoch_exhausted"}:
        error_type = {
            "exhausted": ConnectionError,
            "watch_exhausted": WatchError,
            "response_error": ResponseError,
            "cancelled": asyncio.CancelledError,
            "epoch_exhausted": ConnectionError,
        }[failure]
        with pytest.raises(error_type):
            await reply()
    else:
        await reply()

    if failure.startswith("epoch_"):
        assert epoch_reads == 3
        if failure == "epoch_exhausted":
            run.assert_not_awaited()
            send.assert_not_awaited()
            assert attempts == 0
            assert await get_cached_messages(chat.tid, redis=test_services.redis) == ()
            return

    assert attempts == (
        0
        if failure in {"reset_before_watch", "reset_before_get"}
        else 1
        if failure
        in {
            "epoch_connection",
            "epoch_timeout",
            "response_error",
            "cancelled",
            "reset_during_delay",
            "reset_during_config",
        }
        else 2
        if failure == "lost_ack"
        else 3
    )
    assert all(snapshot == snapshots[0] for snapshot in snapshots)
    run.assert_awaited_once()
    send.assert_awaited_once()
    prepare.assert_awaited_once()
    cached = await get_cached_messages(chat.tid, redis=test_services.redis)
    success = failure in {"before", "lost_ack", "epoch_connection", "epoch_timeout"}
    assert [(row.message_id, row.text) for row in cached] == ([(8, "answer")] if success or tool_failure else [])
    if failure in {"reset_during_delay", "reset_before_watch", "reset_before_get"}:
        assert sleeps == 1
        if failure != "reset_during_delay":
            assert initial_read_failures == 1
        assert await original_get(epoch_key) == b"8"
        assert await get_tool_exchanges(chat.tid, redis=test_services.redis) == {}
        tool_config.assert_not_awaited()
    if failure == "reset_during_config":
        tool_config.assert_awaited_once()
        assert await get_tool_exchanges(chat.tid, redis=test_services.redis) == {}
    if isinstance(history, ModernContext):
        assert history._closed
        assert not await test_services.redis.exists(history._lock_key)
        stored = await test_services.redis.get(history._session_key)
        if success:
            assert stored == snapshots[0][2].encode()
            assert len([event for event in history._state.events if event.kind == "turn"]) == 1
            followup = await ModernContext.build(
                source.model_copy(update={"message_id": 9, "text": "follow-up"}),
                context,
                token_budget=100000,
                instructions="Be helpful.",
                runtime_context="",
            )
            try:
                assert [
                    part.content
                    for event in followup.message_history
                    for part in event.parts
                    if isinstance(part, TextPart)
                ] == ["answer"]
                assert len([event for event in followup._state.events if event.kind == "turn"]) == 1
            finally:
                await followup.abort()
        else:
            assert stored is None
    elif not proactive:
        if success:
            exchanges = await get_tool_exchanges(chat.tid, redis=test_services.redis)
            assert list(exchanges) == [8]
            assert len(exchanges[8]) == 2
        if success or tool_failure:
            tool_config.assert_awaited_once()
