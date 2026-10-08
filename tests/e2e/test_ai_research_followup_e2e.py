"""Research attachments belong to the run producing them, even with replayed tools."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot
from aiogram.types import Message, RichBlockParagraph, RichMessage, RichTextCustomEmoji, Update
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import MessageFactory
from aiogram_test_framework.mock_bot import MockSession
from aiogram_test_framework.types import RequestType
from httpx2 import Request, Response
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel
from sophie_bot.db.models.ai.ai_mode import AIMode, AIModeModel
from sophie_bot.modules.ai.agent_tools.kagi_search import KagiSearchResult
from sophie_bot.modules.ai.utils.chatbot_tool_history import get_tool_exchanges
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.feature_flags import set_chat_override
from tests.e2e.helpers import create_test_user_and_group, next_message_id


@pytest.mark.asyncio
async def test_research_followups_only_attach_new_reports(test_client: TestClient) -> None:
    user, group, _user_model = await create_test_user_and_group(test_client)
    services: ApplicationServices = test_client.dispatcher.workflow_data["services"]
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await AIModeModel.set_mode(chat, AIMode.support)
    await set_chat_override("ai_moderation", group.id, False, redis=services.redis)
    await set_chat_override("ai_search_provider", group.id, "kagi", redis=services.redis)
    await set_chat_override("ai_research_max_rounds", group.id, 1, redis=services.redis)
    await set_chat_override("ai_chatbot_research_quote", group.id, True, redis=services.redis)
    await set_chat_override("ai_chatbot_model", group.id, "test-model", redis=services.redis)
    await set_chat_override("ai_research_model", group.id, "test-model", redis=services.redis)

    chatbot_requests: list[list[ModelMessage]] = []
    report_count = 0

    def llm_response(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal report_count
        if info.output_tools:
            output_tool = info.output_tools[0]
            if "queries" in output_tool.parameters_json_schema["properties"]:
                args = {"queries": [{"query": "bot research", "reason": "Find evidence"}]}
            else:
                report_count += 1
                args = {
                    "research_title": f"Report {report_count}",
                    "text": f"Research summary {report_count}",
                    "sources": [{"title": "Evidence", "url": "https://example.com/evidence"}],
                }
            return ModelResponse(parts=[ToolCallPart(output_tool.name, args, f"structured-{len(messages)}")])

        chatbot_requests.append(list(messages))
        latest_request = messages[-1]
        assert isinstance(latest_request, ModelRequest)
        if any(isinstance(part, ToolReturnPart) for part in latest_request.parts):
            return ModelResponse(parts=[TextPart(f"Research summary {report_count}")])
        user_prompt = next(part.content for part in reversed(latest_request.parts) if isinstance(part, UserPromptPart))
        latest_text = user_prompt if isinstance(user_prompt, str) else user_prompt[-1]
        if str(latest_text).endswith(("research this topic", "Please research an updated report")):
            return ModelResponse(
                parts=[ToolCallPart("research_topic", {"topic": "bot research"}, f"research-{report_count + 1}")]
            )
        return ModelResponse(parts=[TextPart("Ordinary follow-up answer")])

    async def llm_stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        response = llm_response(messages, info)
        for part_index, part in enumerate(response.parts):
            if isinstance(part, TextPart):
                yield part.content
            elif isinstance(part, ToolCallPart):
                yield {
                    part_index: DeltaToolCall(
                        name=part.tool_name, json_args=json.dumps(part.args), tool_call_id=part.tool_call_id
                    )
                }

    session = cast(MockSession, test_client.bot.session)
    original_response = session._generate_response
    sent_messages: dict[int, Message] = {}

    def telegram_response(bot: Bot, method_name: str, params: dict[str, Any]) -> Any:
        if method_name not in {"sendRichMessage", "editMessageText", "sendDocument"}:
            return original_response(bot, method_name, params)
        response = original_response(bot, "sendMessage", params)
        message_id = params.get("message_id", response.message_id)
        # Telegram preserves message IDs on edits and returns rich content. The mock transport
        # does neither; model those external API semantics so reply detection runs for real.
        response = response.model_copy(
            update={
                "message_id": message_id,
                "date": datetime.now(UTC),
                "chat": group,
                "message_thread_id": 42,
                "rich_message": RichMessage(
                    blocks=[
                        RichBlockParagraph(
                            text=[
                                RichTextCustomEmoji(custom_emoji_id="5325547803936572038", alternative_text="✨"),
                                params.get("rich_message", {}).get("html", ""),
                            ]
                        )
                    ]
                ),
            }
        )
        response = response.as_(bot)
        sent_messages[message_id] = response
        return response

    async def send_turn(text: str, reply_to: Message | None = None) -> Message:
        message = MessageFactory.create(
            text=text,
            from_user=user,
            chat=group,
            message_id=next_message_id(),
            date=datetime.now(UTC),
            reply_to_message=reply_to,
        ).model_copy(update={"message_thread_id": 42})
        edit_count = len(test_client.capture.get_by_type(RequestType.EDIT_MESSAGE_TEXT))
        await test_client.dispatcher.feed_update(test_client.bot, Update(update_id=message.message_id, message=message))
        # The final edit identifies the answer rather than its subsequent document.
        edits = test_client.capture.get_by_type(RequestType.EDIT_MESSAGE_TEXT)
        assert len(edits) > edit_count
        return sent_messages[edits[-1].params["message_id"]]

    with (
        patch(
            "sophie_bot.modules.ai.utils.ai_model_factory.get_ai_model",
            return_value=FunctionModel(llm_response, stream_function=llm_stream),
        ),
        patch.object(CONFIG, "kagi_api_key", "test-key"),
        patch(
            "sophie_bot.modules.ai.utils.ai_model_pricing.ai_http_client.get",
            AsyncMock(
                return_value=Response(200, json={"data": []}, request=Request("GET", "https://example.com/models"))
            ),
        ),
        patch(
            "sophie_bot.modules.ai.utils.research.search_kagi",
            AsyncMock(
                return_value=[
                    KagiSearchResult(title="Evidence", url="https://example.com/evidence", snippet="Source evidence")
                ]
            ),
        ) as search,
        patch.object(session, "_generate_response", side_effect=telegram_response),
    ):
        original = await send_turn("/ai research this topic")
        documents = test_client.capture.get_by_type(RequestType.SEND_DOCUMENT)
        assert len(documents) == 1
        assert documents[0].params["document"].filename == "Report_1.md"
        assert documents[0].params["reply_parameters"]["message_id"] == original.message_id
        stored = await get_tool_exchanges(group.id, redis=services.redis)
        assert original.message_id in stored
        assert report_count == 1

        for question in ("Explain the conclusion", "What are the limitations?"):
            await send_turn(question, original)
            replayed_returns = [
                part
                for message in chatbot_requests[-1]
                for part in message.parts
                if isinstance(part, ToolReturnPart) and part.tool_name == "research_topic"
            ]
            assert replayed_returns
            assert report_count == 1
            assert search.await_count == 1
            assert len(test_client.capture.get_by_type(RequestType.SEND_DOCUMENT)) == 1

        updated = await send_turn("Please research an updated report", original)
        documents = test_client.capture.get_by_type(RequestType.SEND_DOCUMENT)
        assert report_count == 2
        assert search.await_count == 2
        assert len(documents) == 2
        assert documents[-1].params["document"].filename == "Report_2.md"
        assert documents[-1].params["reply_parameters"]["message_id"] == updated.message_id
        await send_turn("Explain the updated conclusion", updated)
        assert len(test_client.capture.get_by_type(RequestType.SEND_DOCUMENT)) == 2
