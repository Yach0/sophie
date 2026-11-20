from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, User
from beanie.exceptions import RevisionIdWasChanged
from pydantic_ai.exceptions import ModelHTTPError, UsageLimitExceeded
from pymongo.errors import DuplicateKeyError, PyMongoError
from redis.exceptions import RedisError

from sophie_bot.db.models.ai.ai_evidence_memory import AIEvidenceMemoryModel
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.middlewares.request_context import RequestContext
from sophie_bot.modules.ai.middlewares import cache_user_messages
from sophie_bot.modules.ai.schedules.write_memory import WriteMemory
from sophie_bot.modules.ai.utils.ai_mode import set_chat_mode
from sophie_bot.modules.ai.utils.cache_messages import get_cached_messages
from sophie_bot.modules.ai.utils.evidence_memory import mutate_memory
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.feature_flags import set_chat_override


@pytest.fixture(autouse=True)
async def clean_memory_documents(test_services: ApplicationServices) -> AsyncIterator[None]:
    await AIEvidenceMemoryModel.delete_all()
    yield
    await AIEvidenceMemoryModel.delete_all()


async def test_shared_capture_does_not_write_mongo(
    test_services: ApplicationServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telegram_chat = Chat(id=-1001991, type="supergroup", title="Busy chat")
    chat = await ChatModel.upsert_group(telegram_chat)
    await set_chat_mode(chat, AIMode.entertainment, redis=test_services.redis)
    await set_chat_override("ai_memory_writer", chat.tid, True, redis=test_services.redis)
    insert = AsyncMock(side_effect=AssertionError("Capture must not insert a memory document"))
    replace = AsyncMock(side_effect=AssertionError("Capture must not replace a memory document"))
    monkeypatch.setattr(AIEvidenceMemoryModel, "insert", insert)
    monkeypatch.setattr(AIEvidenceMemoryModel, "replace", replace)
    proactive = AsyncMock()
    monkeypatch.setattr(cache_user_messages, "maybe_run_proactive_reply", proactive)
    handler = AsyncMock(return_value="handled")
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=telegram_chat,
        from_user=User(id=1122, is_bot=False, first_name="Member"),
        text="Hello",
    )
    result = await cache_user_messages.CacheUserMessagesMiddleware(excluded_commands=())(
        handler,
        message,
        {"context": RequestContext(event_chat=chat), "services": test_services},
    )
    assert result == "handled"
    handler.assert_awaited_once()
    proactive.assert_awaited_once()
    cached = await get_cached_messages(chat.tid, redis=test_services.redis)
    assert len(cached) == 1 and cached[0].text == "Hello" and cached[0].eligible_for_memory
    insert.assert_not_awaited()
    replace.assert_not_awaited()


@pytest.mark.parametrize(
    "error",
    [
        ValueError("invalid plan"),
        UsageLimitExceeded("limit"),
        ModelHTTPError(401, "test-model", {}),
        RevisionIdWasChanged(),
        DuplicateKeyError("conflict"),
        PyMongoError("storage"),
        RedisError("cache"),
    ],
)
async def test_writer_continues_to_other_chats_after_expected_errors(
    test_services: ApplicationServices,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    for chat_tid in (-1001992, -1001993):
        chat = await ChatModel.upsert_group(Chat(id=chat_tid, type="supergroup", title="Writer chat"))
        await mutate_memory(chat, lambda state: None, redis=test_services.redis)
    worker = WriteMemory(test_services)
    process = AsyncMock(side_effect=[error, None])
    monkeypatch.setattr(worker, "process_chat", process)
    await worker.handle()
    assert process.await_count == 2
    assert process.await_args_list[0].args[0].tid != process.await_args_list[1].args[0].tid


async def test_writer_does_not_hide_programming_errors(
    test_services: ApplicationServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat = await ChatModel.upsert_group(Chat(id=-1001994, type="supergroup", title="Writer chat"))
    await mutate_memory(chat, lambda state: None, redis=test_services.redis)
    worker = WriteMemory(test_services)
    monkeypatch.setattr(worker, "process_chat", AsyncMock(side_effect=TypeError("unexpected")))
    with pytest.raises(TypeError, match="unexpected"):
        await worker.handle()


@pytest.mark.parametrize("discover_new_chat", [False, True])
async def test_scan_failure_preserves_discovered_chats(
    test_services: ApplicationServices,
    monkeypatch: pytest.MonkeyPatch,
    discover_new_chat: bool,
) -> None:
    known = await ChatModel.upsert_group(Chat(id=-1001995, type="supergroup", title="Known"))
    await mutate_memory(known, lambda state: None, redis=test_services.redis)
    new = await ChatModel.upsert_group(Chat(id=-1001996, type="supergroup", title="New"))

    async def failing_scan(*args: object, **kwargs: object) -> AsyncIterator[bytes]:
        if discover_new_chat:
            yield f"messages:{new.tid}".encode()
        raise RedisError("Interrupted scan")

    monkeypatch.setattr(test_services.redis, "scan_iter", failing_scan)
    worker = WriteMemory(test_services)
    process = AsyncMock()
    monkeypatch.setattr(worker, "process_chat", process)
    await worker.handle()
    assert {call.args[0].tid for call in process.await_args_list} == (
        {known.tid, new.tid} if discover_new_chat else {known.tid}
    )


async def test_invalid_keys_and_trim_failures_do_not_block_other_chats(
    test_services: ApplicationServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = test_services.redis
    for chat_tid in (-1001997, -1001998):
        await ChatModel.upsert_group(Chat(id=chat_tid, type="supergroup", title="Cached"))
        await redis.zadd(f"messages:{chat_tid}", {"entry": datetime.now(UTC).timestamp()})
    await redis.set("messages:invalid", "not a chat")
    await redis.set(b"messages:\xff", "not an integer")
    trim = AsyncMock(side_effect=[RedisError("Cleanup failed"), 0])
    monkeypatch.setattr(redis, "zremrangebyscore", trim)
    worker = WriteMemory(test_services)
    process = AsyncMock()
    monkeypatch.setattr(worker, "process_chat", process)
    await worker.handle()
    assert trim.await_count == 2
    assert {call.args[0].tid for call in process.await_args_list} == {-1001997, -1001998}
