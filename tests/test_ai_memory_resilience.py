from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, User
from beanie.exceptions import RevisionIdWasChanged
from pydantic_ai.exceptions import ModelHTTPError, UsageLimitExceeded
from pymongo.errors import DuplicateKeyError, PyMongoError
from redis.exceptions import RedisError

from sophie_bot.db.models.ai.ai_evidence_memory import AIEvidenceMemoryModel, MemoryState
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.db.models.chat import ChatModel
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


@pytest.mark.parametrize("error", [RevisionIdWasChanged(), DuplicateKeyError("conflict")])
@pytest.mark.parametrize("stage", ["initialize", "capture"])
async def test_memory_conflicts_do_not_block_message_pipeline(
    test_services: ApplicationServices,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    stage: str,
) -> None:
    telegram_chat = Chat(id=-1001991, type="supergroup", title="Busy chat")
    chat = await ChatModel.upsert_group(telegram_chat)
    await set_chat_mode(chat, AIMode.entertainment, redis=test_services.redis)
    await set_chat_override("ai_memory_capture", chat.tid, True, redis=test_services.redis)
    mutations = AsyncMock(side_effect=[error] if stage == "initialize" else [MemoryState(), error])
    monkeypatch.setattr(cache_user_messages, "mutate_memory", mutations)
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
    result = await cache_user_messages.CacheUserMessagesMiddleware()(
        handler,
        message,
        {"context": SimpleNamespace(event_chat=chat), "services": test_services},
    )
    assert result == "handled"
    handler.assert_awaited_once()
    proactive.assert_awaited_once()
    cached = await get_cached_messages(chat.tid, redis=test_services.redis)
    assert len(cached) == 1 and cached[0].text == "Hello"


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
        await mutate_memory(chat, lambda state: None)
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
    await mutate_memory(chat, lambda state: None)
    worker = WriteMemory(test_services)
    monkeypatch.setattr(worker, "process_chat", AsyncMock(side_effect=TypeError("unexpected")))
    with pytest.raises(TypeError, match="unexpected"):
        await worker.handle()
