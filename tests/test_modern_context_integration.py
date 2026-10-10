from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock
from xml.etree import ElementTree

import httpx2
import pytest
from aiogram.types import Chat, Message, User
from beanie import PydanticObjectId
from pydantic_ai.messages import ModelRequest, ModelResponse, ThinkingPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from sophie_bot.config import CONFIG
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext
from sophie_bot.modules.ai.utils.chatbot_agent import build_chatbot_agent
from sophie_bot.modules.ai.utils.modern_context import ModernContext
from sophie_bot.services.application import ApplicationServices
from tests.e2e.helpers import next_group_id, next_message_id, next_user_id


@pytest.mark.asyncio
async def test_native_authenticated_thinking_and_tool_exchange_survive_persisted_replay(
    test_services: ApplicationServices,
) -> None:
    user_tid = next_user_id()
    chat = Chat(id=next_group_id(), type="supergroup")
    user = User(id=user_tid, is_bot=False, first_name="Alice", username="alice_private")
    context = SophieAIToolContext(
        connection=MagicMock(spec=ChatConnection), chat_tid=chat.id, chat_iid=PydanticObjectId(),
        user_tid=user_tid, mode=AIMode.support, services=test_services,
    )
    message = Message(
        message_id=next_message_id(), date=datetime.now(UTC), chat=chat, from_user=user,
        text=f"Check @alice_private user_id={user_tid}; amount=25000; Alice <account> & details",
    )
    thinking = f"Check @alice_private user_id={user_tid} without changing the account amount 25000."
    signing_key = b"deterministic-offline-provider-authentication"
    signature = hmac.new(signing_key, thinking.encode(), hashlib.sha256).hexdigest()
    call_id = f"toolu_alice_private_{user_tid}"
    tool_input = {"user_id": user_tid, "amount": 25000}
    tool_output = {
        "user_id": user_tid, "username": "@alice_private", "amount": 25000,
        "profile": f"tg://user?id={user_tid}", "text": "Alice <account> & details",
    }
    requests: list[dict[str, Any]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["authorization"] == "Bearer offline"
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            assistant = {
                "role": "assistant", "content": None,
                "reasoning_details": [{
                    "type": "reasoning.text", "id": "signed-reasoning",
                    "format": "anthropic-claude-v1", "index": 0,
                    "text": thinking, "signature": signature,
                }],
                "tool_calls": [{
                    "id": call_id, "type": "function",
                    "function": {"name": "account_lookup", "arguments": json.dumps(tool_input)},
                }],
            }
            finish_reason = "tool_calls"
        else:
            reasoning = [
                detail for event in payload["messages"] if event["role"] == "assistant"
                for detail in event.get("reasoning_details", [])
            ]
            assert len(reasoning) == 1
            expected_signature = hmac.new(signing_key, reasoning[0]["text"].encode(), hashlib.sha256).hexdigest()
            assert hmac.compare_digest(reasoning[0]["signature"], expected_signature)
            assert reasoning[0]["text"] == thinking and reasoning[0]["format"] == "anthropic-claude-v1"
            calls = [
                call for event in payload["messages"] if event["role"] == "assistant"
                for call in event.get("tool_calls", [])
            ]
            assert len(calls) == 1 and calls[0]["id"] == call_id
            assert calls[0]["function"]["name"] == "account_lookup"
            assert json.loads(calls[0]["function"]["arguments"]) == tool_input
            returns = [event for event in payload["messages"] if event["role"] == "tool"]
            assert len(returns) == 1 and returns[0]["tool_call_id"] == call_id
            assert json.loads(returns[0]["content"]) == tool_output
            assistant = {"role": "assistant", "content": "Account checked." if len(requests) == 2 else "The previous account lookup remains usable."}
            finish_reason = "stop"
        return httpx2.Response(200, json={
            "id": f"msg_alice_private_{user_tid}_{len(requests)}",
            "object": "chat.completion", "created": 0, "provider": "Anthropic",
            "model": "anthropic/claude-sonnet-4.5",
            "choices": [{"index": 0, "message": assistant, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40},
        })

    def account_lookup(user_id: int, amount: int) -> dict[str, str | int]:
        assert user_id == user_tid and amount == 25000
        return tool_output

    history = await ModernContext.build(
        message, context, token_budget=8192, instructions="Check the account.", runtime_context="",
    )
    try:
        current = ElementTree.fromstring(history.prompt[-1])
        assert current.findtext("text") == message.text
        assert current.attrib["speaker_id"] == "u1"
        assert not {"user_id", "username", "first_name"}.intersection(current.attrib)
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as client:
            model = OpenRouterModel("anthropic/claude-sonnet-4.5", provider=OpenRouterProvider(api_key="offline", http_client=client))
            agent = build_chatbot_agent(model, [account_lookup], AIMode.support, modern_context=history)
            result = await agent.run(
                history.prompt, message_history=history.message_history, deps=context,
                model_settings={"max_tokens": 2048},
            )
            assert result.output == "Account checked."
            events = result.new_messages()
            response = next(event for event in events if isinstance(event, ModelResponse))
            assert response.provider_response_id == f"msg_alice_private_{user_tid}_1"
            assert response.run_id is not None
            assert next(part for part in response.parts if isinstance(part, ThinkingPart)).signature == signature
            call = next(part for part in response.parts if isinstance(part, ToolCallPart))
            assert call.args_as_dict() == tool_input and isinstance(call.args_as_dict()["user_id"], int)
            returned = next(part for event in events if isinstance(event, ModelRequest) for part in event.parts if isinstance(part, ToolReturnPart))
            assert returned.content == tool_output and returned.tool_call_id == call_id
            delivered = Message(
                message_id=next_message_id(), date=datetime.now(UTC), chat=chat,
                from_user=User(id=CONFIG.bot_id, is_bot=True, first_name="Sophie"), text=result.output,
            )
            await history.finish_run(events, delivered)
            followup = message.model_copy(update={"message_id": next_message_id(), "text": "Use the previous account lookup."})
            replay = await ModernContext.build(
                followup, context, token_budget=8192, instructions="Check the account.", runtime_context="",
            )
            try:
                assert replay.session_id == history.session_id
                replay_agent = build_chatbot_agent(model, [account_lookup], AIMode.support, modern_context=replay)
                replay_result = await replay_agent.run(
                    replay.prompt, message_history=replay.message_history, deps=context,
                    model_settings={"max_tokens": 2048},
                )
                assert replay_result.output == "The previous account lookup remains usable."
                assert len(requests) == 3
            finally:
                await replay.abort()
    finally:
        await history.abort()
        await ModernContext.reset(chat.id, redis=test_services.redis)
