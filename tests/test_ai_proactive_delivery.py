from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, ReplyParameters
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import UsageLimits
from stfu_tg import Bold, Doc

from sophie_bot.db.models import AIUsageModel, ChatModel
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.utils import ai_run, chatbot_agent, proactive_replies, transcription
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, AIModelPlan
from sophie_bot.modules.ai.utils.ai_run import AIRequestOptions
from sophie_bot.modules.ai.utils.ai_send import send_ai_rich_message
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext
from sophie_bot.modules.ai.utils.cache_messages import CachedMedia, cache_message, get_cached_messages
from sophie_bot.modules.ai.utils.chatbot_agent import ChatbotRunConfig
from sophie_bot.modules.ai.utils.modern_context import ModernContext
from sophie_bot.modules.ai.utils.proactive_replies import ProactiveAction, ProactiveDecision, ProactiveReplySettings
from sophie_bot.modules.ai.utils.proactive_tracking import decision_failure_key, eligible_key, lock_key
from sophie_bot.services.application import ApplicationServices
from tests.e2e.helpers import next_group_id
from tests.test_ai_media_pathways import _download, _media_message
from tests.test_transcription_provider_errors import billing_failure
from tests.utils.ai_tokenizers import bundled_test_tokenizers  # noqa: F401

ANSWER = "**Answer** with [context](https://example.com) and `code`"


@dataclass
class Delivery:
    message: Message
    chat: ChatModel
    services: ApplicationServices
    send: AsyncMock
    decision: AsyncMock
    metric: Mock
    ordering: list[str]
    histories: list[ModernContext]

    async def run(self) -> None:
        await proactive_replies.maybe_run_proactive_reply(self.message, self.chat, services=self.services)


@pytest.fixture
async def delivery(
    monkeypatch: pytest.MonkeyPatch,
    test_services: ApplicationServices,
    modern: bool,
) -> Delivery:
    telegram_chat = Chat(id=next_group_id(), type="supergroup", title="Group")
    chat = await ChatModel.upsert_group(telegram_chat)
    message = Message(message_id=3, date=datetime.now(UTC), chat=telegram_chat, text="hello", message_thread_id=9)
    for message_id in (1, 2, 3):
        await cache_message(
            "hello",
            chat.tid,
            42,
            message_id,
            message.date,
            "sender",
            message_thread_id=9,
            redis=test_services.redis,
        )
    ordering: list[str] = []
    histories: list[ModernContext] = []

    async def record_effect() -> str:
        ordering.append("tool")
        await test_services.redis.incr(f"offline:tool:{chat.tid}")
        return "done"

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        ordering.append("model")
        if isinstance(messages[-1].parts[0], ToolReturnPart):
            return ModelResponse(parts=[TextPart(ANSWER)])
        return ModelResponse(parts=[ToolCallPart("record_effect", {}, tool_call_id="effect-1")])

    model = FunctionModel(respond, model_name="offline")
    plan = AIModelPlan(candidates=(AIModelCandidate(model, "offline"),))
    agent = Agent(model, tools=[record_effect])
    monkeypatch.setattr(
        chatbot_agent,
        "_build_chatbot_run_config",
        AsyncMock(return_value=ChatbotRunConfig(agent, UsageLimits(), AIRequestOptions())),
    )
    monkeypatch.setattr(ai_run, "_last_resort_candidate", lambda candidates: None)
    monkeypatch.setattr(proactive_replies, "get_chat_default_model_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(proactive_replies, "resolve_chat_service_tier", AsyncMock(return_value=None))
    monkeypatch.setattr(proactive_replies, "get_chat_mode", AsyncMock(return_value=AIMode.support))
    monkeypatch.setattr(
        proactive_replies,
        "is_enabled",
        AsyncMock(side_effect=lambda feature, **kwargs: modern and feature == "ai_chatbot_modern_context"),
    )
    # The only external catalog lookup is replaced; the production usage charge still runs.
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_quota.estimate_model_credit_cost", AsyncMock(return_value=1))

    async def prepare(message: Message, context: SophieAIToolContext, **kwargs: object) -> ModernContext:
        history = await ModernContext.build(
            message,
            context,
            token_budget=100000,
            instructions="Be helpful.",
            runtime_context="",
        )
        histories.append(history)
        return history

    monkeypatch.setattr(proactive_replies, "prepare_chatbot_history", prepare)
    sent = message.model_copy(update={"message_id": 100})

    async def send(**kwargs: object) -> Message:
        # Generation, tool side effects, and charging all precede Telegram delivery.
        assert ordering == ["model", "tool", "model"]
        assert await AIUsageModel.get_today(chat.iid) == 1
        assert await test_services.redis.get(f"offline:tool:{chat.tid}") == b"1"
        ordering.append("send")
        return sent

    request = AsyncMock(side_effect=send)
    monkeypatch.setattr(test_services.bot, "send_rich_message", request)
    monkeypatch.setattr(
        test_services.bot, "get_chat", AsyncMock(return_value=type("ChatInfo", (), {"available_reactions": None})())
    )
    decision = AsyncMock(
        return_value=ProactiveDecision(
            actions=[
                ProactiveAction(action="answer", message_id=1),
                ProactiveAction(action="react", message_id=2, emoji="👍"),
            ]
        )
    )
    monkeypatch.setattr(proactive_replies, "_generate_decision", decision)
    monkeypatch.setattr(
        proactive_replies, "_get_settings", AsyncMock(return_value=ProactiveReplySettings(min_messages=1))
    )
    monkeypatch.setattr(
        proactive_replies, "check_quota", AsyncMock(return_value=type("Quota", (), {"allowed": True})())
    )
    monkeypatch.setattr(test_services.bot, "set_message_reaction", AsyncMock(return_value=True))
    metric = Mock()
    monkeypatch.setattr(proactive_replies, "track_ai_proactive_event", metric)
    return Delivery(message, chat, test_services, request, decision, metric, ordering, histories)


@pytest.mark.parametrize("modern", [False, True])
async def test_proactive_delivery_preserves_rich_reply_and_commits_output(delivery: Delivery) -> None:
    await delivery.run()
    delivery.send.assert_awaited_once()
    kwargs = delivery.send.await_args.kwargs
    assert kwargs["chat_id"] == delivery.chat.tid
    assert kwargs["reply_parameters"] == ReplyParameters(message_id=1)
    assert kwargs["message_thread_id"] == 9
    assert "<b>Answer</b>" in kwargs["rich_message"].html
    assert '<a href="https://example.com">context</a>' in kwargs["rich_message"].html
    assert "<code>code</code>" in kwargs["rich_message"].html
    cached = await get_cached_messages(delivery.chat.tid, redis=delivery.services.redis)
    answer = next(row for row in cached if row.message_id == 100)
    assert answer.text == ANSWER
    assert answer.handled_by_ai and answer.proactively_answered
    assert not answer.eligible_for_proactive_ai
    assert answer.reply_to_message_id == 1 and answer.reply_to_user_id == 42
    assert answer.message_thread_id == 9
    assert not await delivery.services.redis.exists(eligible_key(delivery.chat.tid), lock_key(delivery.chat.tid))
    for history in delivery.histories:
        assert history._closed
        assert await delivery.services.redis.exists(history._session_key)
        assert not await delivery.services.redis.exists(history._lock_key)
    delivery.metric.assert_any_call("answer_sent", proactive_replies._METRIC_ATTRIBUTES)


@pytest.mark.parametrize("modern", [False, True])
@pytest.mark.parametrize("reason", ["message to be replied not found", "REPLY_MESSAGE_ID_INVALID"])
@pytest.mark.parametrize("prefix", ["", "Bad Request: "])
async def test_missing_proactive_target_skips_and_continues(delivery: Delivery, reason: str, prefix: str) -> None:
    original_send = delivery.send.side_effect

    async def unavailable(**kwargs: object) -> Message:
        await original_send(**kwargs)
        raise TelegramBadRequest(
            method=SendMessage(chat_id=delivery.chat.tid, text="answer"), message=f"{prefix}{reason}"
        )

    delivery.send.side_effect = unavailable
    await delivery.run()
    delivery.send.assert_awaited_once()
    delivery.decision.assert_awaited_once()
    assert delivery.ordering == ["model", "tool", "model", "send"]
    assert await AIUsageModel.get_today(delivery.chat.iid) == 1
    assert await delivery.services.redis.get(f"offline:tool:{delivery.chat.tid}") == b"1"
    cached = await get_cached_messages(delivery.chat.tid, redis=delivery.services.redis)
    assert {row.message_id for row in cached} == {1, 2, 3}
    assert not any(row.proactively_answered for row in cached)
    delivery.services.bot.set_message_reaction.assert_awaited_once()
    assert not await delivery.services.redis.exists(eligible_key(delivery.chat.tid), lock_key(delivery.chat.tid))
    assert not await delivery.services.redis.exists(decision_failure_key(delivery.services.bot.id, delivery.chat.tid))
    for history in delivery.histories:
        assert history._closed
        assert not await delivery.services.redis.exists(history._session_key, history._lock_key)
    delivery.metric.assert_any_call(
        "answer_skipped", {**proactive_replies._METRIC_ATTRIBUTES, "reason": "target_missing"}
    )
    assert not any(entry.args[0] == "answer_sent" for entry in delivery.metric.call_args_list)
    # A later trigger can complete a batch without hitting a leaked lock or retrying this answer.
    delivery.decision.return_value = ProactiveDecision(actions=[])
    await delivery.run()
    assert delivery.decision.await_count == 2
    delivery.send.assert_awaited_once()
    assert delivery.ordering == ["model", "tool", "model", "send"]


@pytest.mark.parametrize("modern", [False, True])
@pytest.mark.parametrize(
    "reason", ["chat not found", "not enough rights", "MESSAGE_ID_INVALID", "REPLY_MESSAGE_ID_INVALID_OTHER", "network"]
)
async def test_unrelated_proactive_send_failure_propagates(delivery: Delivery, reason: str) -> None:
    error_type = TelegramNetworkError if reason == "network" else TelegramBadRequest
    failure = error_type(method=SendMessage(chat_id=delivery.chat.tid, text="answer"), message=f"Bad Request: {reason}")
    delivery.send.side_effect = failure
    with pytest.raises(error_type) as raised:
        await delivery.run()
    assert raised.value is failure
    delivery.send.assert_awaited_once()
    assert delivery.ordering == ["model", "tool", "model"]
    assert await AIUsageModel.get_today(delivery.chat.iid) == 1
    assert await delivery.services.redis.get(f"offline:tool:{delivery.chat.tid}") == b"1"
    delivery.services.bot.set_message_reaction.assert_not_awaited()
    assert not await delivery.services.redis.exists(lock_key(delivery.chat.tid))
    assert await delivery.services.redis.zcard(eligible_key(delivery.chat.tid)) == 1
    for history in delivery.histories:
        assert history._closed
        assert not await delivery.services.redis.exists(history._session_key, history._lock_key)


@pytest.mark.parametrize("reason", ["message to be replied not found", "REPLY_MESSAGE_ID_INVALID"])
@pytest.mark.parametrize("prefix", ["", "Bad Request: "])
async def test_explicit_reply_keeps_existing_fallback(
    reason: str, prefix: str, test_services: ApplicationServices
) -> None:
    message = Message(
        message_id=1, date=datetime.now(UTC), chat=Chat(id=-100, type="supergroup"), message_thread_id=9
    ).as_(test_services.bot)
    sent = message.model_copy(update={"message_id": 2})
    send = AsyncMock(
        side_effect=[
            TelegramBadRequest(method=SendMessage(chat_id=-100, text="answer"), message=f"{prefix}{reason}"),
            sent,
        ]
    )
    test_services.bot.send_rich_message = send
    assert await send_ai_rich_message(message, Doc(Bold("Answer"))) is sent
    original, fallback = (entry.kwargs for entry in send.await_args_list)
    assert original["reply_parameters"] == ReplyParameters(message_id=1)
    assert "reply_parameters" not in fallback
    assert fallback["rich_message"] == original["rich_message"]
    assert fallback["message_thread_id"] == 9


@pytest.mark.parametrize("modern", [True])
@pytest.mark.parametrize("failure_kind", ["billing", "unrelated"])
@pytest.mark.usefixtures("native_catalog")
async def test_proactive_transcription_preparation_failure(
    delivery: Delivery,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
    runtime_sentry_events: list[dict[str, Any]],
) -> None:
    delivery.decision.return_value = ProactiveDecision(actions=[ProactiveAction(action="answer", message_id=3)])
    attachment = _media_message("voice")
    await cache_message(
        "voice caption",
        delivery.chat.tid,
        42,
        2,
        delivery.message.date,
        "sender",
        media=CachedMedia.from_message(attachment),
        message_thread_id=9,
        redis=delivery.services.redis,
    )
    failure = billing_failure() if failure_kind == "billing" else ValueError("unrelated bug")
    complete = AsyncMock(side_effect=failure)
    monkeypatch.setattr(
        transcription,
        "get_mistral_client",
        AsyncMock(
            return_value=SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(complete_async=complete)))
        ),
    )
    _download(delivery.services.bot, b"audio", monkeypatch)
    warning = Mock()
    monkeypatch.setattr(transcription.log, "warning", warning)
    info = Mock()
    monkeypatch.setattr(proactive_replies, "_log_proactive_info", info)
    aborted: list[ModernContext] = []
    original_abort = ModernContext.abort

    async def abort(history: ModernContext) -> None:
        aborted.append(history)
        await original_abort(history)

    monkeypatch.setattr(ModernContext, "abort", abort)
    if failure_kind == "billing":
        await delivery.run()
        (event,) = runtime_sentry_events
        assert event["event_id"]
        assert event["tags"]["ai.operation"] == "transcription"
        assert event["tags"]["ai.origin"] == "automatic"
        assert event["tags"]["ai.status_code"] == "402"
        assert "exception" not in event
        assert "billing_no_api_budget_configured" not in json.dumps(event)
        assert "user_facing_message" not in event["contexts"]["ai_request"]
        warning.assert_called_once()
        # This voice message is optional background history, so its failure must
        # retain the caption and allow the current text response to complete.
        info.assert_any_call(
            "Proactive AI answer sent",
            chat_id=delivery.chat.tid,
            message_id=3,
            sent_message_id=100,
        )
        delivery.send.assert_awaited_once()
        assert delivery.ordering == ["model", "tool", "model", "send"]
        assert await AIUsageModel.get_today(delivery.chat.iid) == 1
        assert not await delivery.services.redis.exists(
            decision_failure_key(delivery.services.bot.id, delivery.chat.tid)
        )
        assert "voice caption" in str(delivery.histories[0].message_history)
    else:
        with pytest.raises(ValueError) as raised:
            await delivery.run()
        assert raised.value is failure
        assert not await delivery.services.redis.exists(
            decision_failure_key(delivery.services.bot.id, delivery.chat.tid)
        )
        delivery.send.assert_not_awaited()
        assert delivery.ordering == []
        assert await AIUsageModel.get_today(delivery.chat.iid) == 0
    complete.assert_awaited_once()
    assert not await delivery.services.redis.exists(lock_key(delivery.chat.tid))
    assert aborted
    for history in aborted:
        assert history._closed
        assert not await delivery.services.redis.exists(history._lock_key)
        assert bool(await delivery.services.redis.exists(history._session_key)) == (failure_kind == "billing")


@pytest.mark.parametrize("modern", [False, True])
async def test_quiet_proactive_decision_billing_failure_emits_sanitized_event(
    delivery: Delivery,
    runtime_sentry_events: list[dict[str, Any]],
) -> None:
    private_input = "private decision input and credentials"
    delivery.decision.side_effect = ModelHTTPError(402, "offline", body={"error": {"message": private_input}})
    await delivery.run()

    (event,) = runtime_sentry_events
    assert event["event_id"]
    assert event["tags"]["ai.operation"] == "decision"
    assert event["tags"]["ai.origin"] == "automatic"
    assert event["tags"]["ai.status_code"] == "402"
    assert event["contexts"]["ai_request"]["origin"] == "automatic"
    assert "user_facing_message" not in event["contexts"]["ai_request"]
    assert "exception" not in event
    assert private_input not in json.dumps(event)
    delivery.send.assert_not_awaited()
    assert (
        0 < await delivery.services.redis.ttl(decision_failure_key(delivery.services.bot.id, delivery.chat.tid)) <= 60
    )
    await delivery.run()
    delivery.decision.assert_awaited_once()
    assert runtime_sentry_events == [event]
