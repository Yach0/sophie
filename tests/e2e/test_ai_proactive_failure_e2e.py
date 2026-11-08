from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.types import Update
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import MessageFactory
from fakeredis._socket import _base as redis_socket
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from sophie_bot.db.models import ChatModel
from sophie_bot.db.models.ai.ai_mode import AIMode, AIModeModel
from sophie_bot.modules.ai.utils import ai_run, proactive_replies
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, AIModelPlan
from sophie_bot.modules.ai.utils.proactive_tracking import decision_failure_key, eligible_key, lock_key
from sophie_bot.services.application import ApplicationServices
from tests.e2e.helpers import create_test_user_and_group, next_message_id, set_feature


async def test_terminal_proactive_decision_failure_lifecycle(
    test_client: TestClient,
    test_services: ApplicationServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, group, _user_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await AIModeModel.set_mode(chat, AIMode.entertainment)
    await set_feature(test_client, "ai_moderation", False, chat_tid=group.id)
    await set_feature(test_client, "ai_chatbot_modern_context", False, chat_tid=group.id)
    attempts: list[str] = []
    failing = True

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        attempts.append("inference")
        # Structured output is the only available tool: no action tools run during decisions.
        assert not info.function_tools
        if failing:
            raise ModelHTTPError(400, "offline", body="private prompt / credential / provider payload")
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"actions": []})])

    model = FunctionModel(respond, model_name="offline")
    plan = AIModelPlan(candidates=(AIModelCandidate(model, "offline"),))
    monkeypatch.setattr(proactive_replies, "get_proactive_replies_model_plan", AsyncMock(return_value=plan))
    monkeypatch.setattr(ai_run, "get_ai_model", Mock(return_value=model))
    # Pricing is an external catalog lookup; keep successful requests offline too.
    monkeypatch.setattr("sophie_bot.modules.ai.utils.ai_quota.estimate_model_credit_cost", AsyncMock(return_value=0))
    operational_log = Mock(wraps=proactive_replies._log_proactive_info)
    monkeypatch.setattr(proactive_replies, "_log_proactive_info", operational_log)
    execute = AsyncMock(wraps=proactive_replies._execute_actions)
    monkeypatch.setattr(proactive_replies, "_execute_actions", execute)
    chatbot = AsyncMock(side_effect=AssertionError("Decision must not execute chatbot tools"))
    monkeypatch.setattr(proactive_replies, "run_chatbot", chatbot)
    redis_now = redis_socket.time.time()
    monkeypatch.setattr(redis_socket, "time", SimpleNamespace(time=lambda: redis_now))

    async def send_chatter(text: str) -> None:
        initial_count = len(test_client.capture.all_requests)
        message = MessageFactory.create(
            text=text, from_user=user, chat=group, date=datetime.now(UTC) - timedelta(seconds=1)
        )
        await test_client.dispatcher.feed_update(test_client.bot, Update(update_id=next_message_id(), message=message))
        assert test_client.capture.all_requests[initial_count:] == [], (
            "Proactive failures must not send an error card, action, reaction, or message"
        )

    await proactive_replies._get_allowed_reactions(group.id, services=test_services)

    for message_number in range(15):
        await send_chatter(f"ordinary chatter {message_number}")

    failed_attempt_count = len(attempts)
    assert failed_attempt_count >= 1
    cooldown_key = decision_failure_key(test_services.bot.id, group.id)
    assert await test_services.redis.ttl(cooldown_key) == 60
    execute.assert_not_awaited()
    chatbot.assert_not_awaited()
    assert not await test_services.redis.exists(lock_key(group.id))
    assert await test_services.redis.zcard(eligible_key(group.id)) == 15
    failure_logs = [entry for entry in operational_log.call_args_list if "decision failed" in entry.args[0]]
    assert len(failure_logs) == 1
    assert failure_logs[0].kwargs == {
        "bot_id": test_services.bot.id,
        "chat_id": group.id,
        "error_type": "AIRequestFailed",
        "cooldown_seconds": 60,
    }

    # A later successful provider must still be gated until the Redis TTL expires.
    failing = False
    redis_now += 59
    await send_chatter("nearby message")
    assert len(attempts) == failed_attempt_count
    assert await test_services.redis.ttl(cooldown_key) == 1
    assert not await test_services.redis.exists(lock_key(group.id))
    execute.assert_not_awaited()

    redis_now += 2
    await send_chatter("after cooldown")
    assert len(attempts) > failed_attempt_count
    assert not await test_services.redis.exists(cooldown_key)
    execute.assert_awaited_once()
    assert not await test_services.redis.exists(lock_key(group.id))
    # Existing success cleanup clears the selected batch (15 of 17 messages).
    assert await test_services.redis.zcard(eligible_key(group.id)) == 2
