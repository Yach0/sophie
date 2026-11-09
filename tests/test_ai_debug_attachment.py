from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, TypedDict, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import SendDocument
from aiogram.types import BufferedInputFile, Chat, Message, User
from aiogram_test_framework import MockBot, RequestCapture
from aiogram_test_framework.types import RequestType
from beanie import PydanticObjectId
from pydantic_ai import Agent, UsageLimits
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from sophie_bot.config import CONFIG
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.ai.utils import ai_chatbot_reply as chatbot_reply
from sophie_bot.modules.ai.utils import ai_run, chatbot_agent, chatbot_debug
from sophie_bot.modules.ai.utils.ai_errors import AIRequestFailed
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, AIModelPlan
from sophie_bot.modules.ai.utils.ai_run import AIAgentResult, AIRequestOptions
from sophie_bot.modules.ai.utils.ai_tokens import ModelTokenBudget, TokenCounter
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext
from sophie_bot.modules.ai.utils.chatbot_agent import ChatbotRunConfig
from sophie_bot.modules.ai.utils.modern_context import ModernContext
from sophie_bot.modules.ai.utils.old_context import OldContext
from sophie_bot.services.application import ApplicationServices

pytest_plugins = ("tests.utils.ai_tokenizers",)

LARGE_CONTEXT = "Контекст 🦊 <raw>\n" * 900
OPERATOR_TID = 123456


class DebugPipeline(TypedDict):
    bot: MockBot
    message: Message
    connection: ChatConnection
    history: OldContext
    prepare: AsyncMock
    streamer: AsyncMock
    documents: list[SendDocument]
    config: ChatbotRunConfig
    send_reply: AsyncMock


async def _read_document(document: BufferedInputFile, bot: Bot) -> bytes:
    return b"".join([chunk async for chunk in document.read(bot)])


@pytest.fixture
def debug_pipeline(monkeypatch: pytest.MonkeyPatch, test_services: ApplicationServices) -> DebugPipeline:
    bot = MockBot(
        capture=RequestCapture(), token="42:TEST", bot_id=42, bot_username="sophie_test_bot", bot_first_name="Sophie"
    )
    message = Message(
        message_id=10,
        date=datetime.now(UTC),
        chat=Chat(id=-100123456, type="supergroup"),
        from_user=User(id=OPERATOR_TID, is_bot=False, first_name="Operator"),
        text="question ^llm_debug",
        message_thread_id=7,
        is_topic_message=True,
    ).as_(cast(Bot, bot))
    model = TestModel(custom_output_text="Answer", settings={"temperature": 0.25, "max_tokens": 999})
    plan = AIModelPlan((AIModelCandidate(model=model, model_name=model.model_name, context_window_tokens=32768),))
    history = OldContext(services=test_services)
    history.message_history = [
        ModelRequest(parts=[UserPromptPart("earlier user turn")]),
        ModelResponse(parts=[TextPart("earlier assistant turn")]),
    ]
    history.prompt = [LARGE_CONTEXT, "current question"]
    connection = MagicMock(spec=ChatConnection)
    connection.tid = message.chat.id
    connection.db_model = MagicMock(iid=PydanticObjectId(), tid=message.chat.id)
    monkeypatch.setattr(CONFIG, "operators", [OPERATOR_TID])
    monkeypatch.setattr(CONFIG, "api_operator_token", "synthetic-operator-credential")
    monkeypatch.setattr(chatbot_reply, "_resolve_model_plan", AsyncMock(return_value=plan))
    prepare = AsyncMock(return_value=history)
    monkeypatch.setattr(chatbot_reply, "prepare_chatbot_history", prepare)
    monkeypatch.setattr(chatbot_reply, "is_enabled", AsyncMock(side_effect=lambda name, **kwargs: name == "ai_chatbot"))
    streamer = AsyncMock(return_value=None)
    monkeypatch.setattr(chatbot_reply, "build_message_streamer", streamer)
    monkeypatch.setattr(chatbot_reply, "resolve_chat_service_tier", AsyncMock(return_value="priority"))
    monkeypatch.setattr(chatbot_reply, "build_chatbot_header", AsyncMock(return_value=None))
    monkeypatch.setattr(chatbot_reply, "should_offer_help_mode", AsyncMock(return_value=False))
    monkeypatch.setattr(chatbot_reply, "_cache_chatbot_reply", AsyncMock())
    send_reply = AsyncMock(return_value=message)
    monkeypatch.setattr(chatbot_reply, "send_ai_rich_message", send_reply)
    monkeypatch.setattr(chatbot_agent, "charge_ai_usage", AsyncMock())
    monkeypatch.setattr(ai_run, "_last_resort_candidate", lambda candidates: None)
    monkeypatch.setattr(chatbot_debug, "get_value", AsyncMock(return_value=8192))

    def inspect_context(value: str) -> str:
        """Inspect a context value."""
        return value

    agent = Agent(model, instructions="System instructions for this request", tools=[inspect_context])
    config = ChatbotRunConfig(
        agent=agent,
        usage_limits=UsageLimits(request_limit=4, tool_calls_limit=6, output_tokens_limit=256),
        request_options=AIRequestOptions(service_tier="priority"),
    )
    monkeypatch.setattr(chatbot_agent, "_build_chatbot_run_config", AsyncMock(return_value=config))
    # Capture the real aiogram SendDocument, including its InputFile. MockBot handles the API.
    documents: list[SendDocument] = []

    async def capture_document(make_request: Any, api_bot: Bot, method: Any) -> Any:
        if isinstance(method, SendDocument):
            documents.append(method)
        return await make_request(api_bot, method)

    bot.session.middleware(capture_document)
    return {
        "bot": bot,
        "message": message,
        "connection": connection,
        "history": history,
        "prepare": prepare,
        "streamer": streamer,
        "documents": documents,
        "config": config,
        "send_reply": send_reply,
    }


@pytest.mark.parametrize("modern", [False, True])
async def test_real_reply_debug_branch_sends_complete_utf8_document(
    debug_pipeline: DebugPipeline, test_services: ApplicationServices, modern: bool
) -> None:
    state = debug_pipeline
    if modern:
        history = ModernContext(redis=test_services.redis, chat_tid=state["message"].chat.id)
        history.message_history = state["history"].message_history
        history.prompt = state["history"].prompt
        history.instructions = "Modern system instructions"
        history._budget = 8192
        history._token_budgets = (ModelTokenBudget(TokenCounter("test"), 8192, 32768),)
        state["prepare"].return_value = history

    await chatbot_reply.ai_chatbot_reply(state["message"], state["connection"], services=test_services)

    assert len(state["documents"]) == 1
    request = state["documents"][0]
    assert request.chat_id == state["message"].chat.id
    assert request.message_thread_id == 7
    assert request.reply_parameters.message_id == 10
    assert len(request.caption) < 100
    assert LARGE_CONTEXT not in request.caption
    assert "Provider debug" not in state["send_reply"].await_args.args[1].to_html()
    assert request.document.filename == "llm_debug.txt"
    content = await _read_document(request.document, state["bot"])
    assert len(content) > 4096
    decoded = content.decode("utf-8")
    payload = json.loads(decoded)
    assert payload["model"] == "test"
    assert payload["provider"] == "test"
    assert payload["model_settings"]["temperature"] == 0.25
    assert payload["model_settings"]["max_tokens"] == 256
    assert payload["flags"]["ai_chatbot_response_tokens_limit"] == 8192
    assert payload["usage_limits"]["output_tokens_limit"] == 256
    assert payload["model_plan"][0]["context_window_tokens"] == 32768
    assert payload["built_history"][0]["kind"] == "request"
    assert payload["built_history"][1]["kind"] == "response"
    assert payload["current_prompt"][0]["parts"][0]["content"] == [LARGE_CONTEXT, "current question"]
    prepared = payload["last_prepared_request"]
    assert prepared["messages"][0]["parts"][0]["content"] == "earlier user turn"
    assert prepared["messages"][1]["parts"][0]["content"] == "earlier assistant turn"
    assert prepared["messages"][2]["parts"][0]["content"] == [LARGE_CONTEXT, "current question"]
    assert "System instructions for this request" in decoded
    assert prepared["parameters"]["function_tools"][0]["name"] == "inspect_context"
    assert "parameters_json_schema" in prepared["parameters"]["function_tools"][0]
    assert "trimming" in payload["context_budget"]
    if modern:
        assert payload["context_budget"]["input_budget"] == 8192
    # The debug path does not attempt an oversized Telegram text reply.
    assert not state["bot"].capture.all_requests or all(
        captured.request_type.value == "sendDocument" for captured in state["bot"].capture.all_requests
    )


@pytest.mark.parametrize(
    ("operator", "text", "user_text", "debug_mode", "expected"),
    [
        (False, "^llm_debug", None, False, False),
        (True, "ordinary question", None, False, False),
        (True, "ordinary question", "question ^llm_debug", False, True),
        (False, "ordinary question", "^llm_debug", False, False),
        (True, "^llm_debug", "override without key", False, False),
        (False, "ordinary question", None, True, True),
        (None, "^llm_debug", None, False, False),
    ],
)
async def test_debug_authorization_preserves_magic_key_and_explicit_override(
    debug_pipeline: DebugPipeline,
    test_services: ApplicationServices,
    operator: bool | None,
    text: str,
    user_text: str | None,
    debug_mode: bool,
    expected: bool,
) -> None:
    state = debug_pipeline
    message = (
        state["message"]
        .model_copy(
            update={
                "text": text,
                "from_user": User(id=OPERATOR_TID if operator else 999, is_bot=False, first_name="User")
                if operator is not None
                else None,
            }
        )
        .as_(cast(Bot, state["bot"]))
    )
    await chatbot_reply.ai_chatbot_reply(
        message, state["connection"], user_text=user_text, debug_mode=debug_mode, services=test_services
    )
    assert bool(state["documents"]) is expected
    assert state["streamer"].await_args.args[1] is expected


async def test_debug_attachment_redacts_secrets_in_settings_context_and_tool_arguments(
    debug_pipeline: DebugPipeline, test_services: ApplicationServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = debug_pipeline
    monkeypatch.setattr(CONFIG, "kagi_api_key", "configured-opaque-secret")
    state["config"].agent.model.settings.update(
        {
            "extra_headers": {"Authorization": "Bearer header-secret", "X-API-Key": "header-api-secret"},
            "extra_body": {"api_key": "body-secret", "password": "password-secret"},
        }
    )
    state["history"].prompt.extend(
        [
            "raw known key: configured-opaque-secret",
            "Authorization: Bearer auth-secret\nnext line",
            "Bearer standalone-secret",
            'tool args: {"api_key": "argument-secret", "credentials": "credential-secret"}',
            "https://user:url-password@example.com/path",
            "sk-test-unconfigured-secret",
            "client_secret=client-secret-value",
            "redis://user:redis-password@example.com",
        ]
    )
    state["history"].message_history.extend(
        [
            ModelResponse(
                parts=[ToolCallPart("inspect_context", '{"value": "api_key=tool-argument-secret"}', "call-1")]
            ),
            ModelRequest(parts=[ToolReturnPart("inspect_context", "Bearer tool-return-secret", "call-1")]),
        ]
    )
    await chatbot_reply.ai_chatbot_reply(state["message"], state["connection"], services=test_services)
    content = (await _read_document(state["documents"][0].document, state["bot"])).decode("utf-8")
    for secret in (
        "configured-opaque-secret",
        "header-secret",
        "header-api-secret",
        "body-secret",
        "password-secret",
        "auth-secret",
        "standalone-secret",
        "argument-secret",
        "credential-secret",
        "url-password",
        "sk-test-unconfigured-secret",
        "client-secret-value",
        "redis-password",
        "tool-argument-secret",
        "tool-return-secret",
    ):
        assert secret not in content
    assert "[REDACTED]" in content
    assert "current question" in content
    assert json.loads(content)["model_settings"]["max_tokens"] == 256
    assert state["config"].agent.model.settings["extra_headers"]["Authorization"] == "Bearer header-secret"
    assert "raw known key: configured-opaque-secret" in state["history"].prompt


@pytest.mark.parametrize("cost", [None, Decimal("0.0001234567890123456789")])
async def test_debug_attachment_serializes_real_run_usage(
    debug_pipeline: DebugPipeline, test_services: ApplicationServices, cost: Decimal | None
) -> None:
    state = debug_pipeline
    model = TestModel()
    usage = RunUsage(requests=2, tool_calls=1, input_tokens=123, output_tokens=45, details={"cached": 12}, cost=cost)
    result = AIAgentResult[str](
        output="Answer",
        message_history=[],
        usage=usage,
        served_model=model,
        request_usage_limits=UsageLimits(cost_limit=Decimal("1.25")),
    )
    context = SophieAIToolContext(
        connection=state["connection"],
        chat_tid=state["message"].chat.id,
        chat_iid=state["connection"].db_model.iid,
        services=test_services,
    )
    document = await chatbot_debug.build_chatbot_debug_file(
        state["history"], result, AIModelPlan((AIModelCandidate(model, model.model_name),)), context, service_tier=None
    )
    payload = json.loads(await _read_document(document, state["bot"]))
    assert payload["execution_status"] == "completed"
    assert payload["usage"]["cost"] == (str(cost) if cost is not None else None)
    assert payload["usage"]["requests"] == 2
    assert payload["usage"]["tool_calls"] == 1
    assert payload["usage"]["input_tokens"] == 123
    assert payload["usage"]["output_tokens"] == 45
    assert payload["usage"]["details"] == {"cached": 12}
    assert payload["usage_limits"]["cost_limit"] == "1.25"


@pytest.mark.parametrize("prefix", ["", "tool args: "])
async def test_debug_attachment_redacts_embedded_json_with_escaped_values_and_keys(
    debug_pipeline: DebugPipeline, test_services: ApplicationServices, prefix: str
) -> None:
    state = debug_pipeline
    embedded = (
        r'{"api_key": "escaped\"quote-secret-tail", "pass\u0077ord": "unicode-key-secret", '
        r'"nested": [{"token": "nested\\secret-tail"}], "safe": "preserve \"quotes\"", '
        r'"arguments": "{\"api_\\u006bey\": \"double-encoded-secret\"}"}'
    )
    state["history"].prompt.append(prefix + embedded)

    await chatbot_reply.ai_chatbot_reply(state["message"], state["connection"], services=test_services)

    content = (await _read_document(state["documents"][0].document, state["bot"])).decode("utf-8")
    for secret in ("quote-secret-tail", "unicode-key-secret", "secret-tail", "double-encoded-secret"):
        assert secret not in content
    payload = json.loads(content)
    redacted = payload["current_prompt"][0]["parts"][0]["content"][-1]
    decoded = json.loads(redacted.removeprefix(prefix))
    assert decoded["api_key"] == "[REDACTED]"
    assert decoded["password"] == "[REDACTED]"
    assert decoded["nested"][0]["token"] == "[REDACTED]"
    assert json.loads(decoded["arguments"])["api_key"] == "[REDACTED]"
    assert decoded["safe"] == 'preserve "quotes"'
    assert state["history"].prompt[-1] == prefix + embedded


async def test_debug_attachment_still_delivers_built_context_when_ai_request_fails(
    debug_pipeline: DebugPipeline, test_services: ApplicationServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = debug_pipeline
    monkeypatch.setattr(chatbot_agent, "run_ai_text", AsyncMock(side_effect=AIRequestFailed("synthetic failure")))
    failure_reply = AsyncMock(return_value=state["message"])
    monkeypatch.setattr(chatbot_reply, "_send_chatbot_ai_failure_reply", failure_reply)

    await chatbot_reply.ai_chatbot_reply(state["message"], state["connection"], services=test_services)

    assert len(state["documents"]) == 1
    content = (await _read_document(state["documents"][0].document, state["bot"])).decode("utf-8")
    payload = json.loads(content)
    assert payload["current_prompt"][0]["parts"][0]["content"] == [LARGE_CONTEXT, "current question"]
    assert payload["last_prepared_request"] is None
    assert payload["execution_status"] == "unavailable"
    assert "AIRequestFailed" in payload["execution_note"]
    assert "configured defaults only" in payload["execution_note"]
    for field in (
        "model",
        "provider",
        "provider_base_url",
        "model_settings",
        "usage_limits",
        "usage",
        "retries",
        "result_history",
    ):
        assert payload[field] is None
    assert payload["model_plan"][0]["model"] == "test"
    assert payload["model_plan"][0]["settings"]["max_tokens"] == 999
    assert payload["service_tier_default"] == "priority"
    assert payload["built_history"][0]["parts"][0]["content"] == "earlier user turn"
    failure_reply.assert_awaited_once()


@pytest.mark.parametrize("request_failed", [False, True])
@pytest.mark.parametrize("delivery_error", [TelegramBadRequest, TelegramForbiddenError])
async def test_real_reply_continues_after_debug_document_delivery_failure(
    debug_pipeline: DebugPipeline,
    test_services: ApplicationServices,
    monkeypatch: pytest.MonkeyPatch,
    request_failed: bool,
    delivery_error: type[TelegramAPIError],
) -> None:
    state = debug_pipeline
    failure = AIRequestFailed("synthetic-reference")
    if request_failed:
        monkeypatch.setattr(chatbot_agent, "run_ai_text", AsyncMock(side_effect=failure))
    document_reply = AsyncMock(
        side_effect=delivery_error(
            method=SendDocument(chat_id=state["message"].chat.id, document="test-document"),
            message="Bad Request: CHAT_SEND_DOCS_FORBIDDEN synthetic-secret",
        )
    )
    monkeypatch.setattr(Message, "reply_document", document_reply)
    cache_reply = AsyncMock()
    monkeypatch.setattr(chatbot_reply, "_cache_chatbot_reply", cache_reply)
    warning = MagicMock()
    monkeypatch.setattr(chatbot_reply.log, "warning", warning)

    result = await chatbot_reply.ai_chatbot_reply(state["message"], state["connection"], services=test_services)

    document_reply.assert_awaited_once()
    warning.assert_called_once_with("Failed to deliver LLM diagnostics attachment", error_type=delivery_error.__name__)
    if request_failed:
        requests = state["bot"].capture.get_by_type(RequestType.SEND_MESSAGE)
        assert len(requests) == 1
        assert requests[0].text == chatbot_reply.ai_request_failed_message(error=failure)["text"]
        assert isinstance(result, Message)
        state["send_reply"].assert_not_awaited()
        cache_reply.assert_not_awaited()
    else:
        assert result is state["message"]
        state["send_reply"].assert_awaited_once()
        assert "Answer" in state["send_reply"].await_args.args[1].to_html()
        cache_reply.assert_awaited_once()
        assert cache_reply.await_args.args[1] is result
        assert cache_reply.await_args.args[2] == "<p>Answer</p>"


async def test_debug_attachment_reports_the_model_and_settings_that_served_after_failover(
    debug_pipeline: DebugPipeline, test_services: ApplicationServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = debug_pipeline

    async def fail_primary(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(status_code=400, model_name="primary", body={"message": "synthetic failure"})

    primary = FunctionModel(fail_primary, model_name="primary", settings={"temperature": 0.1})
    fallback = TestModel(model_name="served-fallback", custom_output_text="Answer", settings={"temperature": 0.9})
    plan = AIModelPlan((AIModelCandidate(primary, "primary"), AIModelCandidate(fallback, "served-fallback")))
    monkeypatch.setattr(chatbot_reply, "_resolve_model_plan", AsyncMock(return_value=plan))
    config = replace(state["config"], agent=Agent(primary, instructions="System instructions"))
    monkeypatch.setattr(chatbot_agent, "_build_chatbot_run_config", AsyncMock(return_value=config))

    await chatbot_reply.ai_chatbot_reply(state["message"], state["connection"], services=test_services)

    content = await _read_document(state["documents"][0].document, state["bot"])
    payload = json.loads(content.decode("utf-8"))
    assert payload["model"] == "served-fallback"
    assert payload["model_settings"]["temperature"] == 0.9
    assert payload["model_settings"]["max_tokens"] == 256
    assert payload["model_plan"][0]["model"] == "primary"
    assert LARGE_CONTEXT in payload["last_prepared_request"]["messages"][-1]["parts"][0]["content"]
