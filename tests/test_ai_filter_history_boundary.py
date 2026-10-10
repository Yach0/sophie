from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import Chat, Message, User
from beanie import PydanticObjectId
from pydantic_ai import Agent, ModelHTTPError
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from tenacity import wait_none

from sophie_bot.db.models import FiltersModel
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.ai.magic_handlers import modern_action
from sophie_bot.modules.ai.utils import ai_errors, ai_run, chatbot_context, message_history
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, AIModelPlan
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext
from sophie_bot.modules.ai.utils.cache_messages import cache_message, get_cached_messages
from sophie_bot.modules.ai.utils.message_history import AIMessageHistory
from sophie_bot.modules.filters.enforce_middleware import EnforceFiltersMiddleware
from sophie_bot.modules.filters.utils_ import match_handler
from sophie_bot.services.application import ApplicationServices

CHAT_TID = -1007654321


def _message(message_id: int, text: str, *, reply: Message | None = None) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=CHAT_TID, type="supergroup", title="History test"),
        from_user=User(id=123, is_bot=False, first_name="Alice"),
        text=text,
        reply_to_message=reply,
    )


async def _cache(
    services: ApplicationServices,
    message_id: int,
    text: str,
    *,
    chat_tid: int = CHAT_TID,
    handled_by_ai: bool = False,
) -> None:
    await cache_message(
        text,
        chat_tid,
        123,
        message_id,
        datetime.now(UTC) - timedelta(seconds=100 - message_id),
        "Alice",
        handled_by_ai=handled_by_ai,
        redis=services.redis,
    )


@pytest.fixture
def names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(message_history.ChatModel, "get_by_tid", AsyncMock(return_value=None))
    monkeypatch.setattr(message_history, "_admin_context_name", AsyncMock(return_value="Alice"))


def _user_text(messages: list[ModelMessage]) -> str:
    return "\n".join(
        part.content
        if isinstance(part.content, str)
        else "\n".join(content for content in part.content if isinstance(content, str))
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    )


def _systems(messages: list[ModelMessage]) -> list[str]:
    return [
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, SystemPromptPart)
    ]


@pytest.mark.parametrize("handler", ["ai:respond to spam", "exact:trigger"])
@pytest.mark.parametrize("cache_state", ["miss", "background", "dialogue"])
async def test_filter_action_provider_gets_trigger_once_and_system(
    handler: str,
    cache_state: str,
    names: None,
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    if cache_state != "miss":
        await _cache(test_services, 30, "trigger", handled_by_ai=cache_state == "dialogue")
    before = await get_cached_messages(CHAT_TID, redis=test_services.redis)
    requests: list[list[ModelMessage]] = []

    def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        requests.append(messages.copy())
        if len(requests) == 1:
            raise ModelHTTPError(503, "local-function", "temporary failure")
        return ModelResponse(parts=[TextPart("Answer")])

    model = FunctionModel(respond)
    chat_db = SimpleNamespace(iid=PydanticObjectId(), tid=CHAT_TID, first_name_or_title="Alice")
    monkeypatch.setattr(modern_action.ChatModel, "get_by_tid", AsyncMock(return_value=chat_db))
    monkeypatch.setattr(modern_action.AIQuotaFilter, "__call__", AsyncMock(return_value=True))
    monkeypatch.setattr(
        modern_action,
        "get_chat_default_model_plan",
        AsyncMock(return_value=AIModelPlan((AIModelCandidate(model, model.model_name),))),
    )
    monkeypatch.setattr(modern_action, "charge_ai_usage", AsyncMock())
    monkeypatch.setattr(ai_run, "_last_resort_candidate", lambda _candidates: None)
    monkeypatch.setattr(ai_errors, "AI_REQUEST_RETRY_WAIT", wait_none())
    monkeypatch.setattr(test_services.modules, "action_handlers", {"ai_text": modern_action.AIReplyAction()})
    monkeypatch.setattr(test_services.modules, "actions", {"ai_text": modern_action.AI_REPLY_ACTION})

    matched_filter = SimpleNamespace(
        id=PydanticObjectId(),
        handler=handler,
        effective_version=1,
        silent=False,
        actions={"ai_text": {"prompt": "Filter instruction"}},
    )
    monkeypatch.setattr(FiltersModel, "get_filters", AsyncMock(return_value=[matched_filter]))
    ai_match = AsyncMock(return_value=True)
    monkeypatch.setattr(match_handler, "match_ai_handler", ai_match)
    sent = AsyncMock(return_value=[31])
    monkeypatch.setattr(EnforceFiltersMiddleware, "_handle_action_messages", sent)
    data = {
        "context": SimpleNamespace(
            connection=SimpleNamespace(tid=CHAT_TID, db_model=chat_db),
            event_chat=chat_db,
            user_in_group=None,
        ),
        "services": test_services,
    }
    with pytest.raises(SkipHandler):
        await EnforceFiltersMiddleware()._process_filters(_message(30, "trigger"), data)

    assert ai_match.await_count == int(handler.startswith("ai:"))
    assert data["ai_filter_handled"] == handler.startswith("ai:")
    sent.assert_awaited_once()
    assert sent.await_args is not None
    assert "Answer" in str(sent.await_args.args[1][0])
    assert len(requests) == 2
    for request in requests:
        assert _user_text(request).count("Alice: trigger") == 1
        assert _systems(request) == ["Filter instruction"]
    assert await get_cached_messages(CHAT_TID, redis=test_services.redis) == before


@pytest.mark.parametrize("fold_background", [False, True])
@pytest.mark.parametrize("excluded_chat", [CHAT_TID, CHAT_TID - 1])
async def test_cache_exclusion_uses_exact_identity_before_transformation(
    fold_background: bool,
    excluded_chat: int,
    names: None,
    test_services: ApplicationServices,
) -> None:
    await _cache(test_services, 29, "same text", handled_by_ai=True)
    await _cache(test_services, 30, "same text", handled_by_ai=True)
    history = AIMessageHistory(services=test_services)
    await history.add_from_cache(CHAT_TID, fold_background=fold_background, exclude_message=(excluded_chat, 30))
    await history.add_from_message(_message(31, "latest", reply=_message(30, "same text")))
    history.apply_context_block()
    seen: list[list[ModelMessage]] = []

    def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        seen.append(messages.copy())
        return ModelResponse(parts=[TextPart("Answer")])

    await Agent(FunctionModel(respond)).run(history.prompt, message_history=history.message_history)
    # Excluded targets must still be available through deliberate reply quotation. Different
    # Telegram IDs with identical text remain distinct even when one is excluded from cache.
    assert _user_text(seen[0]).count("Alice: same text") == 2
    assert (CHAT_TID, 29) in history._cached_message_ids
    assert ((CHAT_TID, 30) in history._cached_message_ids) == (excluded_chat != CHAT_TID)
    assert len(await get_cached_messages(CHAT_TID, redis=test_services.redis)) == 2


@pytest.mark.parametrize("cached_reply", [False, True])
async def test_filter_reply_context_preserves_same_text_with_different_ids(
    cached_reply: bool,
    names: None,
    test_services: ApplicationServices,
) -> None:
    if cached_reply:
        await _cache(test_services, 29, "same text")
    await _cache(test_services, 30, "same text")
    history = AIMessageHistory(services=test_services)
    history.add_system("Filter instruction")
    await history.add_from_cache(CHAT_TID, fold_background=True, exclude_message=(CHAT_TID, 30))
    await history.add_from_message(_message(30, "same text", reply=_message(29, "same text")))
    history.apply_context_block()

    def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        text = _user_text(messages)
        assert text.count("same text") == 2
        assert text.count("Alice (reply to Alice): same text") == 1
        assert _systems(messages) == ["Filter instruction"]
        return ModelResponse(parts=[TextPart("Answer")])

    await Agent(FunctionModel(respond)).run(history.prompt, message_history=history.message_history)


@pytest.mark.parametrize("cache_hit", [False, True])
async def test_normal_chatbot_provider_history_and_retry(
    cache_hit: bool,
    names: None,
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
) -> None:
    if cache_hit:
        await _cache(test_services, 29, "earlier")
    monkeypatch.setattr(chatbot_context, "get_value", AsyncMock(return_value=0))
    monkeypatch.setattr(chatbot_context, "load_chatbot_tool_history", AsyncMock(return_value={}))
    monkeypatch.setattr(ai_run, "_last_resort_candidate", lambda _candidates: None)
    monkeypatch.setattr(ai_errors, "AI_REQUEST_RETRY_WAIT", wait_none())
    context = SophieAIToolContext(
        connection=cast(ChatConnection, SimpleNamespace()),
        chat_tid=CHAT_TID,
        chat_iid=PydanticObjectId(),
        services=test_services,
        user_text="latest",
    )
    history = await chatbot_context.prepare_chatbot_history(_message(30, "latest"), context)
    requests: list[list[ModelMessage]] = []

    def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        requests.append(messages.copy())
        assert _user_text(messages).count("Alice: latest") == 1
        assert _user_text(messages).count("Alice: earlier") == int(cache_hit)
        if len(requests) == 1:
            raise ModelHTTPError(503, "local-function", "temporary failure")
        return ModelResponse(parts=[TextPart("Answer")])

    result = await ai_run.run_ai_text(
        Agent(FunctionModel(respond)),
        history.prompt,
        message_history=history.message_history,
    )
    assert result.output == "Answer"
    assert len(requests) == 2
    assert _user_text(requests[0]) == _user_text(requests[1])


def test_folding_preserves_non_user_parts_order_and_is_idempotent(test_services: ApplicationServices) -> None:
    history = AIMessageHistory(services=test_services)
    system = SystemPromptPart("System instruction")
    retry = RetryPromptPart("Try again")
    image = UserPromptPart([BinaryContent(b"image", media_type="image/png")])
    mixed = ModelRequest(
        parts=[system, UserPromptPart("first"), retry, UserPromptPart("second"), image],
        run_id="prior-run",
        metadata={"source": "history"},
    )
    history.message_history = [mixed, ModelRequest(parts=[UserPromptPart("third"), UserPromptPart("fourth")])]
    history._fold_trailing_requests()

    assert len(history.message_history) == 1
    assert history.message_history[0].parts == [system, retry, image]
    assert history.message_history[0].run_id == "prior-run"
    assert history.message_history[0].metadata == {"source": "history"}
    assert history.context_lines == ["first", "second", "third", "fourth"]
    history._fold_trailing_requests()
    assert history.message_history[0].parts == [system, retry, image]
    assert history.context_lines == ["first", "second", "third", "fourth"]
    history.prompt = ["latest"]
    history.apply_context_block()
    prompt = history.prompt.copy()
    history.apply_context_block()
    assert history.prompt == prompt


def test_folding_keeps_tool_boundary_and_earlier_requests(test_services: ApplicationServices) -> None:
    history = AIMessageHistory(services=test_services)
    earlier = ModelRequest(parts=[UserPromptPart("before tool")])
    call = ModelResponse(parts=[ToolCallPart("lookup", {}, tool_call_id="call-1")])
    returned = ModelRequest(
        parts=[
            SystemPromptPart("Keep system"),
            ToolReturnPart("lookup", "result", tool_call_id="call-1"),
            UserPromptPart("with tool return"),
        ]
    )
    history.message_history = [earlier, call, returned, ModelRequest(parts=[UserPromptPart("after tool")])]
    history._fold_trailing_requests()
    history._fold_trailing_requests()
    assert history.message_history == [earlier, call, returned]
    assert history.context_lines == ["after tool"]
