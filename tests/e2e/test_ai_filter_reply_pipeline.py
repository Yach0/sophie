from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import ExitStack
from datetime import UTC
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot
from aiogram.types import Message, Update
from aiogram_test_framework import TestClient
from aiogram_test_framework.factories import MessageFactory
from aiogram_test_framework.types import CapturedRequest, RequestType
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from sophie_bot.config import CONFIG
from sophie_bot.db.models import ChatModel, FiltersModel
from sophie_bot.db.models.ai.ai_mode import AIMode, AIModeModel
from sophie_bot.modules.ai.utils.ai_header import AI_CHATBOT_CUSTOM_EMOJI_ID, AI_GENERATING_EMOJI_ID
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, build_model_plan
from sophie_bot.modules.ai.utils.cache_messages import get_cached_messages
from sophie_bot.modules.ai.utils.chatbot_tool_history import get_tool_exchanges
from tests.e2e.helpers import create_test_user_and_group, grant_admin, set_feature

TABLE_OUTPUT = "Comparison\n\n| Item | Value |\n| --- | --- |\n| Alpha | **one** |\n| Beta | two |"


def _model_response(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[TextPart(TABLE_OUTPUT)])


async def _model_stream(_messages: list[ModelMessage], _info: AgentInfo) -> AsyncIterator[str]:
    yield "Comparison\n\n"
    yield TABLE_OUTPUT[len("Comparison\n\n") :]


def _telegram_rich_response(test_client: TestClient, stack: ExitStack, *, bind_bot: bool = False) -> None:
    session = test_client.bot.session
    original_response = session._generate_response

    def generate_response(bot: Bot, method_name: str, params: dict[str, Any]) -> Any:
        if method_name == "sendRichMessage":
            method_name = "sendMessage"
        response = original_response(bot=bot, method_name=method_name, params=params)
        if isinstance(response, Message) and response.date.tzinfo is None:
            response = response.model_copy(update={"date": response.date.astimezone(UTC)})
        if isinstance(response, Message) and method_name == "sendMessage":
            # Share the incoming-message factory's sequence, as Telegram does in a chat.
            response = response.model_copy(
                update={
                    "message_id": MessageFactory.create(
                        text="", from_user=response.from_user, chat=response.chat
                    ).message_id
                }
            )
        if isinstance(response, Message) and method_name == "editMessageText":
            response = response.model_copy(update={"message_id": params["message_id"]})
        if bind_bot and isinstance(response, Message):
            response = response.as_(bot)
        return response

    stack.enter_context(patch.object(session, "_generate_response", side_effect=generate_response))


def _final_payload(requests: list[CapturedRequest], *, streaming: bool) -> str:
    rich_requests = [request for request in requests if "rich_message" in request.params]
    assert rich_requests
    final = rich_requests[-1]
    payload = final.params["rich_message"]["html"]
    assert "<table" in payload
    assert "Alpha" in payload and "<b>one</b>" in payload
    assert AI_CHATBOT_CUSTOM_EMOJI_ID in payload
    assert "🔋" in payload
    assert payload.index("Beta") < payload.index("🔋")
    if streaming:
        sends = [request for request in rich_requests if request.request_type == RequestType.OTHER]
        edits = [request for request in rich_requests if request.request_type == RequestType.EDIT_MESSAGE_TEXT]
        assert len(sends) == 1
        assert AI_GENERATING_EMOJI_ID in sends[0].params["rich_message"]["html"]
        assert len(edits) >= 2
        assert all(edit.params["message_id"] == sends[0].response.message_id for edit in edits)
        assert "Comparison" in edits[0].params["rich_message"]["html"]
    else:
        assert len(rich_requests) == 1
        assert final.request_type == RequestType.OTHER
    assert not any(request.request_type == RequestType.SEND_MESSAGE for request in requests)
    return payload


@pytest.mark.parametrize("handler", ["ai:compare spam", "spam", "re:sp[a-z]+"])
@pytest.mark.parametrize("streaming", [True, False])
async def test_ai_filter_payload_matches_ordinary_reply(test_client: TestClient, handler: str, streaming: bool) -> None:
    user, group, _user_model = await create_test_user_and_group(test_client)
    await grant_admin(group.id, user.id)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await AIModeModel.set_mode(chat, AIMode.support)
    await set_feature(test_client, "ai_moderation", False, chat_tid=group.id)
    await set_feature(test_client, "ai_chatbot_show_model_name", True, chat_tid=group.id)
    await set_feature(test_client, "ai_filters_jev", True, chat_tid=group.id)
    histories: list[list[ModelMessage]] = []

    def response(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        histories.append(messages)
        return _model_response(messages, info)

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        histories.append(messages)
        async for chunk in _model_stream(messages, info):
            yield chunk

    model = FunctionModel(response, stream_function=stream, model_name="test-model")
    plan = build_model_plan([AIModelCandidate(model=model, model_name=model.model_name)])
    schedule = MagicMock()
    with ExitStack() as stack:
        _telegram_rich_response(test_client, stack)
        # Keep the last-resort provider at the same external test boundary as the primary.
        stack.enter_context(patch("sophie_bot.modules.ai.utils.ai_run.get_ai_model", return_value=model))
        stack.enter_context(
            patch(
                "sophie_bot.modules.ai.utils.ai_chatbot_reply.get_chat_default_model_plan", AsyncMock(return_value=plan)
            )
        )
        stack.enter_context(
            patch(
                "sophie_bot.modules.ai.magic_handlers.modern_action.get_chat_default_model_plan",
                AsyncMock(return_value=plan),
            )
        )
        stack.enter_context(
            patch(
                "sophie_bot.modules.ai.utils.ai_chatbot_reply.resolve_chat_service_tier", AsyncMock(return_value=None)
            )
        )
        stack.enter_context(
            patch("sophie_bot.modules.ai.utils.ai_quota.estimate_model_credit_cost", AsyncMock(return_value=0))
        )
        match = stack.enter_context(
            patch("sophie_bot.modules.filters.utils_.match_handler._match_jev_filter", AsyncMock(return_value=True))
        )
        stack.enter_context(patch.object(test_client.bot, "send_chat_action", AsyncMock(return_value=True)))
        stack.enter_context(
            patch.object(test_client.dispatcher.workflow_data["services"].deletions, "schedule", schedule)
        )
        if not streaming:
            for module in ("utils.ai_chatbot_reply", "magic_handlers.modern_action"):
                stack.enter_context(
                    patch(f"sophie_bot.modules.ai.{module}.build_message_streamer", AsyncMock(return_value=None))
                )
        ordinary = await test_client.send_command(command="ai", args="compare", from_user=user, chat=group)
        ordinary_payload = _final_payload(ordinary, streaming=streaming)
        assert "(Test Model)" in ordinary_payload
        filter_item = await FiltersModel(
            chat=chat.iid,
            handler=handler,
            action=None,
            actions={"ai_text": {"prompt": "Compare these items"}},
            silent=True,
        ).insert()
        # Production matching, dispatch, AI runtime, rendering and Telegram delivery all run.
        filtered = await test_client.send_message(text="spam comparison", from_user=user, chat=group)
        assert _final_payload(filtered, streaming=streaming) == ordinary_payload
        assert match.await_count == (1 if handler.startswith("ai:") else 0)
        schedule.assert_called_once()
        produced = next(
            request
            for request in filtered
            if "rich_message" in request.params and request.request_type == RequestType.OTHER
        )
        assert produced.params["chat_id"] == group.id
        assert produced.params["reply_parameters"]["message_id"] == schedule.call_args.args[1][0]
        assert produced.response.message_id in schedule.call_args.args[1]
        assert len(schedule.call_args.args[1]) == 2
        assert await FiltersModel.get_by_id(filter_item.id) is not None
        services = test_client.dispatcher.workflow_data["services"]
        final_message = [request for request in filtered if "rich_message" in request.params][-1].response
        cached = [
            entry
            for entry in await get_cached_messages(group.id, redis=services.redis)
            if entry.message_id == final_message.message_id
        ]
        assert len(cached) == 1
        answer = cached[0]
        assert "Alpha" in answer.text and "Beta" in answer.text
        assert "🔋" not in answer.text
        assert answer.is_bot and answer.user_id == CONFIG.bot_id
        assert answer.handled_by_ai and not answer.eligible_for_proactive_ai
        assert answer.reply_to_message_id == produced.params["reply_parameters"]["message_id"]
        assert answer.reply_to_user_id == user.id
        # Filters have no tools: an answer must not acquire exchanges from an earlier run.
        assert final_message.message_id not in await get_tool_exchanges(group.id, redis=services.redis)
        follow_up = MessageFactory.create(
            text="/ai explain that answer", from_user=user, chat=group, reply_to_message=final_message
        )
        await test_client.dispatcher.feed_update(
            bot=test_client.bot, update=Update(update_id=follow_up.message_id, message=follow_up)
        )
        assert len(histories) == 3
        assert any(
            isinstance(part, TextPart) and "Alpha" in part.content and "Beta" in part.content
            for history_message in histories[-1]
            for part in history_message.parts
        )


async def test_static_filter_response_keeps_plain_send(test_client: TestClient) -> None:
    user, group, _user_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await FiltersModel(chat=chat.iid, handler="spam", action=None, actions={"reply": {"text": "Static reply"}}).insert()
    requests = await test_client.send_message(text="spam", from_user=user, chat=group)
    replies = [request for request in requests if request.request_type == RequestType.SEND_MESSAGE]
    assert len(replies) == 1
    assert replies[0].text == " \n<b><b>[🪄 Reply]</b></b>\nStatic reply"
    assert not any("rich_message" in request.params for request in requests)


@pytest.mark.parametrize("streaming", [True, False])
@pytest.mark.parametrize("provider_failure", [True, False], ids=["provider-failure", "mixed-ai-static"])
async def test_ai_filter_silent_mode_tracks_final_replies(
    test_client: TestClient, streaming: bool, provider_failure: bool
) -> None:
    user, group, _user_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await AIModeModel.set_mode(chat, AIMode.support)
    await set_feature(test_client, "ai_moderation", False, chat_tid=group.id)

    def response(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if provider_failure:
            raise ModelHTTPError(status_code=400, model_name="test-model", body="Provider rejected request")
        return _model_response(messages, info)

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        if provider_failure:
            raise ModelHTTPError(status_code=400, model_name="test-model", body="Provider rejected request")
        async for chunk in _model_stream(messages, info):
            yield chunk

    model = FunctionModel(response, stream_function=stream, model_name="test-model")
    plan = build_model_plan([AIModelCandidate(model=model, model_name=model.model_name)])
    actions = {"ai_text": {"prompt": "Compare these items"}}
    if not provider_failure:
        actions["reply"] = {"text": "Static reply"}
    await FiltersModel(chat=chat.iid, handler="spam", action=None, actions=actions, silent=True).insert()
    deletions = test_client.dispatcher.workflow_data["services"].deletions
    schedule = MagicMock()
    with ExitStack() as stack:
        _telegram_rich_response(test_client, stack, bind_bot=provider_failure)
        if provider_failure:
            # Exhaust the last-resort provider offline so the filter receives AIRequestFailed.
            stack.enter_context(patch("sophie_bot.modules.ai.utils.ai_run.get_ai_model", return_value=model))
        stack.enter_context(
            patch(
                "sophie_bot.modules.ai.magic_handlers.modern_action.get_chat_default_model_plan",
                AsyncMock(return_value=plan),
            )
        )
        stack.enter_context(
            patch("sophie_bot.modules.ai.utils.ai_quota.estimate_model_credit_cost", AsyncMock(return_value=0))
        )
        stack.enter_context(patch.object(test_client.bot, "send_chat_action", AsyncMock(return_value=True)))
        stack.enter_context(patch.object(deletions, "schedule", schedule))
        if not streaming:
            stack.enter_context(
                patch(
                    "sophie_bot.modules.ai.magic_handlers.modern_action.build_message_streamer",
                    AsyncMock(return_value=None),
                )
            )
        requests = await test_client.send_message(text="spam comparison", from_user=user, chat=group)

        plain_sends = [request for request in requests if request.request_type == RequestType.SEND_MESSAGE]
        if provider_failure:
            failures = [request for request in requests if "AI request failed" in (request.text or "")]
            assert len(failures) == 1
            final_ai = failures[0]
            assert final_ai.request_type == (RequestType.EDIT_MESSAGE_TEXT if streaming else RequestType.SEND_MESSAGE)
            assert len(plain_sends) == (0 if streaming else 1)
            rich_sends = [request for request in requests if "rich_message" in request.params]
            assert len(rich_sends) == (1 if streaming else 0)
            if streaming:
                assert final_ai.response.message_id == rich_sends[0].response.message_id
            reply_ids = [final_ai.response.message_id]
        else:
            _final_payload(
                [request for request in requests if request.request_type != RequestType.SEND_MESSAGE],
                streaming=streaming,
            )
            assert len(plain_sends) == 1
            assert plain_sends[0].text == " \n<b><b>[🪄 Reply]</b></b>\nStatic reply"
            final_ai = [request for request in requests if "rich_message" in request.params][-1]
            reply_ids = [final_ai.response.message_id, plain_sends[0].response.message_id]

        trigger_id = next(
            request.params["reply_parameters"]["message_id"]
            for request in requests
            if request.request_type in {RequestType.SEND_MESSAGE, RequestType.OTHER}
            and "reply_parameters" in request.params
        )
        schedule.assert_called_once_with(group.id, [trigger_id, *reply_ids], delay_seconds=30)
        assert len(set(reply_ids)) == len(reply_ids)
        await deletions.delete_messages_after_delay(group.id, schedule.call_args.args[1], delay_seconds=0)
        deletion = next(request for request in test_client.capture.all_requests if "message_ids" in request.params)
        assert deletion.params["chat_id"] == group.id
        assert deletion.params["message_ids"] == [trigger_id, *reply_ids]


@pytest.mark.parametrize("ordinary", [False, True], ids=["filter", "ordinary"])
@pytest.mark.parametrize("streaming", [True, False])
@pytest.mark.parametrize("failure_stage", ["generation", "delivery", "caching"])
async def test_ai_reply_unexpected_failure_cleans_progress(
    test_client: TestClient, ordinary: bool, streaming: bool, failure_stage: str
) -> None:
    user, group, _user_model = await create_test_user_and_group(test_client)
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await AIModeModel.set_mode(chat, AIMode.support)
    await set_feature(test_client, "ai_moderation", False, chat_tid=group.id)
    await FiltersModel(
        chat=chat.iid, handler="spam", action=None, actions={"ai_text": {"prompt": "Compare these items"}}
    ).insert()
    error = ValueError("Unexpected reply failure")

    def response(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if failure_stage == "generation":
            raise error
        return _model_response(messages, info)

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        if failure_stage == "generation":
            yield "Draft"
            yield "Draft with a deferred update"
            raise error
        async for chunk in _model_stream(messages, info):
            yield chunk

    model = FunctionModel(response, stream_function=stream, model_name="test-model")
    plan = build_model_plan([AIModelCandidate(model=model, model_name=model.model_name)])
    with ExitStack() as stack:
        _telegram_rich_response(test_client, stack)
        for module in ("utils.ai_chatbot_reply", "magic_handlers.modern_action"):
            stack.enter_context(
                patch(f"sophie_bot.modules.ai.{module}.get_chat_default_model_plan", AsyncMock(return_value=plan))
            )
            if not streaming:
                stack.enter_context(
                    patch(f"sophie_bot.modules.ai.{module}.build_message_streamer", AsyncMock(return_value=None))
                )
        stack.enter_context(
            patch(
                "sophie_bot.modules.ai.utils.ai_chatbot_reply.resolve_chat_service_tier", AsyncMock(return_value=None)
            )
        )
        stack.enter_context(
            patch("sophie_bot.modules.ai.utils.ai_quota.estimate_model_credit_cost", AsyncMock(return_value=0))
        )
        stack.enter_context(patch.object(test_client.bot, "send_chat_action", AsyncMock(return_value=True)))
        reported = stack.enter_context(
            patch("sophie_bot.modules.error.handlers.error.capture_sentry", return_value=None)
        )
        if failure_stage == "caching":
            stack.enter_context(
                patch("sophie_bot.modules.ai.utils.ai_chatbot_reply.cache_message", AsyncMock(side_effect=error))
            )
        if failure_stage == "delivery":
            generate = test_client.bot.session._generate_response

            def fail_final(bot: Bot, method_name: str, params: dict[str, Any]) -> Any:
                payload = params.get("rich_message", {}).get("html", "")
                if "<table" in payload and AI_GENERATING_EMOJI_ID not in payload:
                    raise error
                return generate(bot=bot, method_name=method_name, params=params)

            stack.enter_context(patch.object(test_client.bot.session, "_generate_response", side_effect=fail_final))
        if ordinary:
            await test_client.send_command(command="ai", args="compare", from_user=user, chat=group)
        else:
            await test_client.send_message(text="spam comparison", from_user=user, chat=group)
        # The same unexpected exception reaches the framework's error reporter.
        reported.assert_called_once_with(error)
        requests = test_client.capture.all_requests
        placeholders = [
            request
            for request in requests
            if request.request_type == RequestType.OTHER
            and "rich_message" in request.params
            and AI_GENERATING_EMOJI_ID in request.params["rich_message"]["html"]
        ]
        deletes = [request for request in requests if request.request_type == RequestType.DELETE_MESSAGE]
        assert len(placeholders) == (1 if streaming else 0)
        assert len(deletes) == (1 if streaming and failure_stage != "caching" else 0)
        if failure_stage == "caching":
            _final_payload([request for request in requests if "rich_message" in request.params], streaming=streaming)
        elif streaming:
            assert deletes[0].params["message_id"] == placeholders[0].response.message_id
        cached = await get_cached_messages(group.id, redis=test_client.dispatcher.workflow_data["services"].redis)
        assert not any(entry.is_bot for entry in cached)
