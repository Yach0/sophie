from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest
from aiogram_test_framework import TestClient
from pydantic_ai.messages import ModelResponse, TextPart

from sophie_bot.db.models import ChatModel
from sophie_bot.db.models.ai.ai_mode import AIMode, AIModeModel
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, build_model_plan
from sophie_bot.modules.ai.utils.cache_messages import get_cached_messages
from sophie_bot.modules.ai.utils.chatbot_tool_history import get_tool_exchanges
from sophie_bot.modules.ai.utils.modern_context import ModernContext, _Session
from tests.e2e.helpers import create_test_user_and_group, set_feature
from tests.e2e.test_ai_filter_reply_pipeline import _telegram_rich_response
from tests.test_ai_public_text import PUBLIC_ANSWER, PUBLIC_ROUNDS, lookup, public_text_model
from tests.utils.ai_tokenizers import bundled_test_tokenizers  # noqa: F401


@pytest.mark.parametrize("streaming", [True, False])
@pytest.mark.parametrize("modern", [True, False])
async def test_tool_round_public_text_matches_final_and_cache(
    test_client: TestClient, streaming: bool, modern: bool
) -> None:
    user, group, _user_model = await create_test_user_and_group(test_client)
    services = test_client.dispatcher.workflow_data["services"]
    chat = await ChatModel.get_by_tid(group.id)
    assert chat is not None
    await AIModeModel.set_mode(chat, AIMode.support)
    await set_feature(test_client, "ai_moderation", False, chat_tid=group.id)
    await set_feature(test_client, "ai_chatbot_modern_context", modern, chat_tid=group.id)
    model = public_text_model(model_name="gpt-4o")
    plan = build_model_plan([AIModelCandidate(model=model, model_name=model.model_name, context_window_tokens=32000)])
    with ExitStack() as stack:
        _telegram_rich_response(test_client, stack, bind_bot=True)
        stack.enter_context(patch("sophie_bot.modules.ai.utils.ai_run.get_ai_model", return_value=model))
        stack.enter_context(
            patch(
                "sophie_bot.modules.ai.utils.ai_chatbot_reply.get_chat_default_model_plan", AsyncMock(return_value=plan)
            )
        )
        stack.enter_context(
            patch("sophie_bot.modules.ai.utils.chatbot_agent.get_chatbot_tools", AsyncMock(return_value=[lookup]))
        )
        stack.enter_context(
            patch("sophie_bot.modules.ai.utils.ai_quota.estimate_model_credit_cost", AsyncMock(return_value=0))
        )
        stack.enter_context(patch.object(test_client.bot, "send_chat_action", AsyncMock(return_value=True)))
        if not streaming:
            stack.enter_context(
                patch(
                    "sophie_bot.modules.ai.utils.ai_chatbot_reply.build_message_streamer", AsyncMock(return_value=None)
                )
            )
        requests = await test_client.send_command(command="ai", args="look up twice", from_user=user, chat=group)

    final = [request for request in requests if "rich_message" in request.params][-1]
    assert final.response is not None
    payload = final.params["rich_message"]["html"]
    assert all(payload.count(text.strip()) == 1 for text in PUBLIC_ROUNDS)
    assert (
        payload.index(PUBLIC_ROUNDS[0].strip())
        < payload.index(PUBLIC_ROUNDS[1].strip())
        < payload.index(PUBLIC_ROUNDS[2])
    )
    assert "PRIVATE" not in payload
    cached = [
        entry
        for entry in await get_cached_messages(group.id, redis=services.redis)
        if entry.message_id == final.response.message_id
    ]
    assert len(cached) == 1
    assert cached[0].text == PUBLIC_ANSWER
    if modern:
        session_keys = await services.redis.smembers(ModernContext._index_key(group.id))
        assert len(session_keys) == 1
        raw_session = await services.redis.get(next(iter(session_keys)))
        assert raw_session is not None
        session = _Session.model_validate_json(raw_session)
        persisted_text = "".join(
            part.content
            for event in session.events
            if event.kind == "turn"
            for message in event.messages()
            if isinstance(message, ModelResponse)
            for part in message.parts
            if isinstance(part, TextPart)
        )
        assert persisted_text == cached[0].text
    else:
        exchanges = await get_tool_exchanges(group.id, redis=services.redis)
        assert len(exchanges[final.response.message_id]) == 4
