from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest
from aiogram_test_framework import TestClient
from aiogram_test_framework.types import RequestType
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ThinkingPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaThinkingPart, DeltaToolCall, FunctionModel

from sophie_bot.db.models import ChatModel, FiltersModel
from sophie_bot.db.models.ai.ai_mode import AIMode, AIModeModel
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, build_model_plan
from sophie_bot.modules.ai.utils.cache_messages import get_cached_messages
from sophie_bot.modules.ai.utils.modern_context import ModernContext, _Session
from sophie_bot.utils.feature_flags import set_chat_override
from tests.e2e.helpers import create_test_user_and_group, set_feature
from tests.e2e.test_ai_filter_reply_pipeline import _telegram_rich_response
from tests.utils.ai_tokenizers import bundled_test_tokenizers  # noqa: F401

LONG_ANSWER = "A & B " * 600 + "TAIL"
ROW_ANSWER = "| Name | Value |\n| --- | --- |\n| kept | visible |\n| unseen_row | secret_row |"
COLUMN_ANSWER = "| Name | Value | unseen_column |\n| --- | --- | --- |\n| kept | visible | secret_column |"
RAGGED_ANSWER = (
    "| Name | Value |\n| --- | --- |\n| kept | visible | extra_visible | secret_column |\n| unseen_row | secret_row |"
)
DELIVERED_TABLE = (
    "<table bordered striped compact><tr><th>Name</th><th>Value</th></tr><tr><td>kept</td><td>visible</td></tr></table>"
)


@pytest.mark.parametrize(
    "answer", [LONG_ANSWER, ROW_ANSWER, COLUMN_ANSWER, RAGGED_ANSWER], ids=["length", "rows", "columns", "ragged"]
)
@pytest.mark.parametrize("reply_path", ["modern", "legacy", "filter"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_fitted_public_cache_matches_delivered_body(
    test_client: TestClient, reply_path: str, streaming: bool, answer: str
) -> None:
    user, group, _user_model = await create_test_user_and_group(test_client)
    services = test_client.dispatcher.workflow_data["services"]
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await AIModeModel.set_mode(chat, AIMode.support)
    await set_feature(test_client, "ai_moderation", False, chat_tid=group.id)
    await set_feature(test_client, "ai_chatbot_modern_context", reply_path == "modern", chat_tid=group.id)
    if reply_path == "filter":
        await FiltersModel(
            chat=chat.iid, handler="long answer", action=None, actions={"ai_text": {"prompt": "Give a long answer"}}
        ).insert()

    for feature in ("ai_chatbot_table_max_rows", "ai_chatbot_table_max_columns"):
        await set_chat_override(
            feature, group.id, 3 if feature.endswith("columns") and answer == RAGGED_ANSWER else 2, redis=services.redis
        )
    await set_chat_override("ai_chatbot_table_card_threshold", group.id, -1, redis=services.redis)

    native_responses: list[ModelResponse] = []
    model_inputs: list[list[ModelMessage]] = []

    def response(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        model_inputs.append(list(messages))
        has_return = any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts)
        is_followup = any(
            isinstance(part, TextPart) and part.content == answer for message in messages for part in message.parts
        )
        result = (
            ModelResponse(parts=[TextPart("Continued." if is_followup else answer)])
            if reply_path == "filter" or has_return
            else ModelResponse(
                parts=[
                    ThinkingPart("PRIVATE native reasoning", signature="native-signature", provider_name="test"),
                    ToolCallPart("get_notes", {}, "notes-call"),
                ]
            )
        )
        native_responses.append(result)
        return result

    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall] | dict[int, DeltaThinkingPart]]:
        for part_index, part in enumerate(response(messages, info).parts):
            if isinstance(part, TextPart):
                yield part.content
            elif isinstance(part, ThinkingPart):
                yield {part_index: DeltaThinkingPart(content=part.content, signature=part.signature)}
            elif isinstance(part, ToolCallPart):
                yield {
                    part_index: DeltaToolCall(
                        name=part.tool_name, json_args=json.dumps(part.args), tool_call_id=part.tool_call_id
                    )
                }

    model = FunctionModel(response, stream_function=stream, model_name="gpt-4o")
    plan = build_model_plan([AIModelCandidate(model=model, model_name=model.model_name, context_window_tokens=32000)])
    with ExitStack() as stack:
        _telegram_rich_response(test_client, stack, bind_bot=True)
        stack.enter_context(patch("sophie_bot.modules.ai.utils.ai_run.get_ai_model", return_value=model))
        for module in ("utils.ai_chatbot_reply", "magic_handlers.modern_action"):
            stack.enter_context(
                patch(f"sophie_bot.modules.ai.{module}.get_chat_default_model_plan", AsyncMock(return_value=plan))
            )
            if not streaming:
                stack.enter_context(
                    patch(f"sophie_bot.modules.ai.{module}.build_message_streamer", AsyncMock(return_value=None))
                )
        stack.enter_context(
            patch("sophie_bot.modules.ai.utils.ai_quota.estimate_model_credit_cost", AsyncMock(return_value=0))
        )
        stack.enter_context(patch.object(test_client.bot, "send_chat_action", AsyncMock(return_value=True)))
        requests = (
            await test_client.send_message(text="long answer", from_user=user, chat=group)
            if reply_path == "filter"
            else await test_client.send_command(command="ai", args="long answer", from_user=user, chat=group)
        )
        raw_session = None
        if reply_path == "modern":
            session_keys = await services.redis.smembers(ModernContext._index_key(group.id))
            assert len(session_keys) == 1
            raw_session = await services.redis.get(next(iter(session_keys)))
            assert raw_session is not None
            first_session = _Session.model_validate_json(raw_session)
            assert len([event for event in first_session.events if event.kind == "turn"]) == 1
            assert len(native_responses) == 2

            await test_client.send_command(command="ai", args="Continue", from_user=user, chat=group)
            assert len(native_responses) == 3
            prior_answers = [
                part.content
                for message in model_inputs[-1]
                if isinstance(message, ModelResponse)
                for part in message.parts
                if isinstance(part, TextPart)
            ]
            assert prior_answers == [answer]
            followup_session_raw = await services.redis.get(next(iter(session_keys)))
            assert followup_session_raw is not None
            followup_session = _Session.model_validate_json(followup_session_raw)
            assert len([event for event in followup_session.events if event.kind == "turn"]) == 2

    rich_requests = [request for request in requests if "rich_message" in request.params]
    sends = [request for request in rich_requests if request.request_type != RequestType.EDIT_MESSAGE_TEXT]
    assert len(sends) == 1
    final = rich_requests[-1]
    assert final.response is not None
    assert sends[0].response is not None
    assert final.response.message_id == sends[0].response.message_id
    assert all(
        request.params["message_id"] == final.response.message_id
        for request in rich_requests
        if request.request_type == RequestType.EDIT_MESSAGE_TEXT
    )
    assert not any(request.request_type == RequestType.SEND_MESSAGE for request in requests)
    if reply_path != "modern":
        assert len(native_responses) == (1 if reply_path == "filter" else 2)
    payload = final.params["rich_message"]["html"]
    if answer == LONG_ANSWER:
        # Restore the body paragraph unwrapped by the header and exclude the UI footer.
        delivered_body = "<p>" + payload[payload.index("A &amp; B") : payload.index("<br>")] + "</p>"
        assert delivered_body.endswith("...</p>")
        assert len(delivered_body) < len(answer) == 3604
        assert "TAIL" not in payload
    else:
        delivered_body = DELIVERED_TABLE
        if answer == RAGGED_ANSWER:
            delivered_body = delivered_body.replace("<td>visible</td>", "<td>visible</td><td>extra_visible</td>")
            assert "<td>extra_visible</td>" in payload
        assert "<th>Name</th><th>Value</th>" in payload
        assert "<td>kept</td><td>visible</td>" in payload
        assert "unseen" not in payload and "secret" not in payload
    cached = [
        entry
        for entry in await get_cached_messages(group.id, redis=services.redis)
        if entry.message_id == final.response.message_id
    ]
    assert len(cached) == 1
    assert cached[0].text == delivered_body
    assert "TAIL" not in cached[0].text
    assert "unseen" not in cached[0].text and "secret" not in cached[0].text
    assert "PRIVATE" not in payload and "PRIVATE" not in cached[0].text

    if reply_path == "modern":
        assert raw_session is not None
        session = _Session.model_validate_json(raw_session)
        native_messages = [message for event in session.events if event.kind == "turn" for message in event.messages()]
        persisted_responses = [message for message in native_messages if isinstance(message, ModelResponse)]
        assert len(persisted_responses) == 2
        # Native history must keep the original answer and complete tool exchange.
        assert persisted_responses[-1].parts == [TextPart(answer)]
        thinking = persisted_responses[0].parts[0]
        assert isinstance(thinking, ThinkingPart)
        assert thinking.content == "PRIVATE native reasoning"
        assert thinking.signature == "native-signature"
        assert persisted_responses[0].parts[1] == ToolCallPart("get_notes", "{}" if streaming else {}, "notes-call")
        if not streaming:
            assert persisted_responses[0].parts == native_responses[0].parts
        returns = [part for message in native_messages for part in message.parts if isinstance(part, ToolReturnPart)]
        assert len(returns) == 1
        assert returns[0].tool_name == "get_notes"
        assert returns[0].tool_call_id == "notes-call"
        assert returns[0].content == []
