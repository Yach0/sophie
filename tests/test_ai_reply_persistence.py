from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from functools import partial
from types import SimpleNamespace
from typing import Literal
from unittest.mock import AsyncMock, Mock

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
from redis.asyncio import Redis
from redis.asyncio.client import Pipeline
from redis.exceptions import ConnectionError, ResponseError, TimeoutError, WatchError
from stfu_tg import Doc
from tenacity import AsyncRetrying

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel
from sophie_bot.db.models.chat import ChatType
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.ai.magic_handlers import modern_action
from sophie_bot.modules.ai.utils import ai_chatbot_reply as chatbot_reply
from sophie_bot.modules.ai.utils import cache_messages, chatbot_tool_history, proactive_replies, redis_persistence
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
    monkeypatch.setattr(redis_persistence, "AsyncRetrying", partial(AsyncRetrying, sleep=reset_during_retry_delay))

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
    + [(modern, True, False, "reset_during_decision") for modern in [False, True]]
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
            "reset_during_preparation",
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
        "reset_during_preparation",
        "reset_during_decision",
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
        module,
        "build_reply_doc" if proactive else "_build_fitting_reply_doc",
        AsyncMock(return_value=Doc("answer") if proactive else (Doc("answer"), "answer")),
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
    if failure == "reset_during_preparation":

        async def reset_during_preparation(*args: object, **kwargs: object) -> OldContext | ModernContext:
            run.assert_not_awaited()
            send.assert_not_awaited()
            await ModernContext.reset(chat.tid, redis=test_services.redis)
            await chatbot_tool_history.reset_tool_exchanges(chat.tid, redis=test_services.redis)
            return history

        prepare.side_effect = reset_during_preparation
    if failure == "reset_during_decision":
        settings = proactive_replies.ProactiveReplySettings(min_messages=1)
        await cache_messages.cache_message(
            "question", chat.tid, 123, source.message_id, source.date, "Alice", redis=test_services.redis
        )

        async def reset_during_decision(
            decision_chat: ChatModel,
            chat_tid: int,
            candidates: tuple[MessageType, ...],
            settings: proactive_replies.ProactiveReplySettings,
            *,
            services: ApplicationServices,
        ) -> proactive_replies.ProactiveDecision:
            nonlocal history
            assert [(candidate.message_id, candidate.text) for candidate in candidates] == [(7, "question")]
            run.assert_not_awaited()
            send.assert_not_awaited()
            await ModernContext.reset(chat_tid, redis=services.redis)
            assert await get_cached_messages(chat_tid, redis=services.redis) == ()
            if isinstance(history, ModernContext):
                await history.abort()
                history = await ModernContext.build(
                    source, context, token_budget=100000, instructions="Be helpful.", runtime_context=""
                )
                prepare.return_value = history
            return proactive_replies.ProactiveDecision(
                actions=[proactive_replies.ProactiveAction(action="answer", message_id=7)]
            )

        decision = AsyncMock(side_effect=reset_during_decision)
        monkeypatch.setattr(proactive_replies, "check_quota", AsyncMock(return_value=SimpleNamespace(allowed=True)))
        monkeypatch.setattr(proactive_replies, "_get_settings", AsyncMock(return_value=settings))
        monkeypatch.setattr(proactive_replies, "_generate_decision", decision)
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
        if target is None or (
            failure == "reset_during_decision"
            and target[1] != (history._session_key if modern else cache_messages.get_message_cache_key(chat.tid))
        ):
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
        monkeypatch.setattr(redis_persistence, "AsyncRetrying", partial(AsyncRetrying, sleep=reset_during_retry_delay))

    async def reply() -> None:
        if failure == "reset_during_decision":
            await proactive_replies.maybe_run_proactive_reply(source, chat, services=test_services)
            decision.assert_awaited_once()
        elif proactive:
            await proactive_replies._answer_message(
                chat.tid,
                chat,
                MessageType(user_id=123, message_id=7, text="question", created_at=source.date),
                services=test_services,
            )
        else:
            assert await chatbot_reply.ai_chatbot_reply(source, connection, services=test_services) is sent

    if failure in {
        "exhausted",
        "watch_exhausted",
        "response_error",
        "cancelled",
        "epoch_connection",
        "epoch_timeout",
        "epoch_exhausted",
    }:
        error_type = {
            "exhausted": ConnectionError,
            "watch_exhausted": WatchError,
            "response_error": ResponseError,
            "cancelled": asyncio.CancelledError,
            "epoch_connection": ConnectionError,
            "epoch_timeout": TimeoutError,
            "epoch_exhausted": ConnectionError,
        }[failure]
        with pytest.raises(error_type):
            await reply()
    else:
        await reply()

    if failure.startswith("epoch_"):
        assert epoch_reads == 1
        run.assert_not_awaited()
        send.assert_not_awaited()
        assert attempts == 0
        assert await get_cached_messages(chat.tid, redis=test_services.redis) == ()
        return

    assert attempts == (
        0
        if failure in {"reset_before_watch", "reset_before_get", "reset_during_preparation", "reset_during_decision"}
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
    if failure in {"reset_during_preparation", "reset_during_decision"}:
        assert await original_get(epoch_key) == (b"1" if modern else b"8")
        assert await get_tool_exchanges(chat.tid, redis=test_services.redis) == {}
        tool_config.assert_not_awaited()
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


@pytest.mark.parametrize("reset_during_retry", [False, True])
async def test_modern_proactive_post_send_epoch_failure_retries_only_persistence(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
    reset_during_retry: bool,
) -> None:
    chat = ChatModel(
        tid=-100123,
        type=ChatType.supergroup,
        first_name_or_title="Group",
        username=None,
        is_bot=False,
        last_saw=datetime.now(UTC),
    )
    source = Message(
        message_id=7,
        date=datetime.now(UTC),
        chat=Chat(id=chat.tid, type="supergroup"),
        from_user=User(id=123, is_bot=False, first_name="Alice"),
        text="question",
    )
    sent = source.model_copy(update={"message_id": 8, "text": "answer"})
    connection = ChatConnection(type=chat.type, is_connected=False, tid=chat.tid, title="Group", db_model=chat)
    context = SophieAIToolContext(connection=connection, chat_tid=chat.tid, chat_iid=chat.iid, services=test_services)
    history = await ModernContext.build(
        source, context, token_budget=100000, instructions="Be helpful.", runtime_context=""
    )
    native = [
        ModelRequest(parts=[UserPromptPart(content=history.prompt)]),
        ModelResponse(parts=[TextPart(content="answer")]),
    ]
    run = AsyncMock(
        return_value=AIAgentResult(output="answer", message_history=native, new_messages=native, usage=RunUsage())
    )
    send = AsyncMock(return_value=sent)
    prepare = AsyncMock(return_value=history)
    monkeypatch.setattr(proactive_replies, "_build_answer_history", prepare)
    monkeypatch.setattr(proactive_replies, "run_chatbot", run)
    monkeypatch.setattr(proactive_replies, "send_ai_rich_message_to_chat", send)
    monkeypatch.setattr(proactive_replies, "is_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(
        proactive_replies, "get_chat_default_model_plan", AsyncMock(return_value=SimpleNamespace(primary=TestModel()))
    )
    monkeypatch.setattr(proactive_replies, "resolve_chat_service_tier", AsyncMock(return_value=None))
    monkeypatch.setattr(proactive_replies, "build_chatbot_header", AsyncMock(return_value="header"))
    monkeypatch.setattr(proactive_replies, "build_reply_doc", AsyncMock(return_value=Doc("answer")))

    epoch_key = cache_messages.get_context_epoch_key(chat.tid)
    original_get = test_services.redis.get
    original_pipeline_get = Pipeline.get
    failures = 0
    direct_post_send_reads = 0
    persistence_epoch_reads = 0
    sleeps = 0

    def fail_first_post_send_epoch_read() -> None:
        nonlocal failures
        run.assert_awaited_once()
        send.assert_awaited_once()
        if failures == 0:
            failures += 1
            raise ConnectionError("transient epoch read failure after Telegram send")

    async def get(key: str) -> bytes | None:
        nonlocal direct_post_send_reads
        if key == epoch_key and send.await_count:
            direct_post_send_reads += 1
            fail_first_post_send_epoch_read()
        return await original_get(key)

    async def pipeline_get(pipe: Pipeline, key: str) -> bytes | None:
        nonlocal persistence_epoch_reads
        if key == epoch_key:
            persistence_epoch_reads += 1
            fail_first_post_send_epoch_read()
        return await original_pipeline_get(pipe, key)

    async def retry_delay(seconds: float) -> None:
        nonlocal sleeps
        sleeps += 1
        assert seconds == 0.1
        if reset_during_retry:
            await ModernContext.reset(chat.tid, redis=test_services.redis)

    monkeypatch.setattr(test_services.redis, "get", get)
    monkeypatch.setattr(Pipeline, "get", pipeline_get)
    monkeypatch.setattr(redis_persistence, "AsyncRetrying", partial(AsyncRetrying, sleep=retry_delay))

    await proactive_replies._answer_message(
        chat.tid,
        chat,
        MessageType(user_id=123, message_id=7, text="question", created_at=source.date),
        services=test_services,
        expected_epoch=0,
    )

    run.assert_awaited_once()
    send.assert_awaited_once()
    prepare.assert_awaited_once()
    assert failures == sleeps == 1
    assert direct_post_send_reads == 0
    assert persistence_epoch_reads == 2
    assert history._closed
    assert not await test_services.redis.exists(history._lock_key)
    cached = await get_cached_messages(chat.tid, redis=test_services.redis)
    assert [(row.message_id, row.text) for row in cached] == ([] if reset_during_retry else [(8, "answer")])
    stored = await original_get(history._session_key)
    if reset_during_retry:
        assert stored is None
        assert not await test_services.redis.exists(ModernContext._index_key(chat.tid))
        assert await original_get(epoch_key) == b"1"
    else:
        assert stored == history._state.model_dump_json().encode()
        assert len([event for event in history._state.events if event.kind == "turn"]) == 1


@pytest.mark.parametrize(
    "preparation_step",
    ["add_from_cache", "add_from_message", "before", "lost_ack", "exhausted", "response_error", "cancelled"],
)
async def test_filter_reply_persistence_never_repeats_delivery_or_completion(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
    preparation_step: str,
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
    sent = source.model_copy(update={"message_id": 8, "text": "answer"})
    native = [
        ModelRequest(parts=[UserPromptPart(content="question")]),
        ModelResponse(parts=[ToolCallPart(tool_name="web_search", args={}, tool_call_id="call-1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="web_search", content="result", tool_call_id="call-1")]),
        ModelResponse(parts=[TextPart(content="answer")]),
    ]
    run = AsyncMock(
        return_value=AIAgentResult(output="answer", message_history=native, new_messages=native, usage=RunUsage())
    )
    send = AsyncMock(return_value=sent)
    epoch_key = cache_messages.get_context_epoch_key(chat.tid)
    await test_services.redis.set(epoch_key, 7)

    async def reset_during_preparation(*args: object, **kwargs: object) -> None:
        run.assert_not_awaited()
        send.assert_not_awaited()
        await ModernContext.reset(chat.tid, redis=test_services.redis)
        await chatbot_tool_history.reset_tool_exchanges(chat.tid, redis=test_services.redis)

    load_cache = AsyncMock()
    load_message = AsyncMock()
    monkeypatch.setattr(OldContext, "add_from_cache", load_cache)
    monkeypatch.setattr(OldContext, "add_from_message", load_message)
    reset = preparation_step in {"add_from_cache", "add_from_message"}
    if reset:
        (load_cache if preparation_step == "add_from_cache" else load_message).side_effect = reset_during_preparation
    monkeypatch.setattr(ChatModel, "get_by_tid", AsyncMock(return_value=chat))
    monkeypatch.setattr(modern_action.AIQuotaFilter, "__call__", AsyncMock(return_value=True))
    monkeypatch.setattr(
        modern_action, "get_chat_default_model_plan", AsyncMock(return_value=SimpleNamespace(primary=TestModel()))
    )
    monkeypatch.setattr(modern_action, "is_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(modern_action, "build_message_streamer", AsyncMock(return_value=None))
    monkeypatch.setattr(modern_action, "run_ai_text", run)
    monkeypatch.setattr(modern_action, "build_chatbot_header", AsyncMock(return_value="header"))
    monkeypatch.setattr(
        modern_action, "_build_fitting_reply_doc", AsyncMock(return_value=(Doc("answer"), "fitted answer"))
    )
    monkeypatch.setattr(modern_action, "send_ai_rich_message", send)
    tool_config = AsyncMock(return_value=100)
    monkeypatch.setattr(chatbot_tool_history, "get_value", tool_config)

    completed = Mock(wraps=modern_action.mark_action_completed)
    monkeypatch.setattr(modern_action, "mark_action_completed", completed)
    original_execute = Pipeline.execute
    attempts = 0

    async def execute(pipe: Pipeline, raise_on_error: bool = True) -> list[object]:
        nonlocal attempts
        if not any(args[0] == "ZADD" for args, _options in pipe.command_stack):
            return await original_execute(pipe, raise_on_error)
        attempts += 1
        run.assert_awaited_once()
        send.assert_awaited_once()
        completed.assert_called_once()
        if preparation_step == "exhausted" or preparation_step == "before" and attempts < 3:
            raise ConnectionError("commit unavailable")
        if preparation_step == "response_error":
            raise ResponseError("invalid command")
        if preparation_step == "cancelled":
            raise asyncio.CancelledError
        result = await original_execute(pipe, raise_on_error)
        if preparation_step == "lost_ack" and attempts == 1:
            raise TimeoutError("EXEC succeeded but acknowledgment was lost")
        return result

    monkeypatch.setattr(Pipeline, "execute", execute)

    async def reply() -> object:
        return await modern_action.AIReplyAction().handle(
            source,
            {"context": SimpleNamespace(connection=connection), "services": test_services},
            modern_action.AIReplyActionDataModel(prompt="Respond"),
        )

    errors = {"exhausted": ConnectionError, "response_error": ResponseError, "cancelled": asyncio.CancelledError}
    if preparation_step in errors:
        with pytest.raises(errors[preparation_step]):
            await reply()
    else:
        assert await reply() is sent

    load_cache.assert_awaited_once()
    load_message.assert_awaited_once()
    run.assert_awaited_once()
    send.assert_awaited_once()
    completed.assert_called_once()
    assert attempts == (
        0 if reset else 3 if preparation_step in {"before", "exhausted"} else 2 if preparation_step == "lost_ack" else 1
    )
    assert await test_services.redis.get(epoch_key) == (b"8" if reset else b"7")
    cached = await get_cached_messages(chat.tid, redis=test_services.redis)
    if preparation_step in {"before", "lost_ack"}:
        assert [(row.message_id, row.text) for row in cached] == [(8, "fitted answer")]
        assert list(await get_tool_exchanges(chat.tid, redis=test_services.redis)) == [8]
        tool_config.assert_awaited_once()
    else:
        assert cached == ()
        assert await get_tool_exchanges(chat.tid, redis=test_services.redis) == {}
        tool_config.assert_not_awaited()


async def test_delivered_cache_retry_freezes_message_before_first_commit(
    monkeypatch: pytest.MonkeyPatch,
    test_redis: Redis,
) -> None:
    chat_tid = -100123
    message = MessageType(user_id=123, message_id=8, text="delivered answer", created_at=datetime.now(UTC))
    original_execute = Pipeline.execute
    attempts = 0

    async def execute(pipe: Pipeline, raise_on_error: bool = True) -> list[object]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            message.text = "changed after delivery"
            raise ConnectionError("unavailable before EXEC")
        return await original_execute(pipe, raise_on_error)

    monkeypatch.setattr(Pipeline, "execute", execute)

    await cache_messages.store_delivered_message(chat_tid, message, redis=test_redis, expected_epoch=0)

    assert attempts == 2
    cached = await get_cached_messages(chat_tid, redis=test_redis)
    assert [(row.message_id, row.text) for row in cached] == [(8, "delivered answer")]


async def test_epoch_capture_does_not_retry_before_delivery(
    monkeypatch: pytest.MonkeyPatch,
    test_redis: Redis,
) -> None:
    read = AsyncMock(side_effect=ConnectionError("epoch unavailable"))
    monkeypatch.setattr(test_redis, "get", read)

    with pytest.raises(ConnectionError, match="epoch unavailable"):
        await cache_messages.read_context_epoch(-100123, redis=test_redis)

    read.assert_awaited_once()
