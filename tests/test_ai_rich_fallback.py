from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from stfu_tg import Doc

from sophie_bot.modules.ai.utils import chatbot_streaming, proactive_replies
from sophie_bot.modules.ai.utils.chatbot_streaming import ChatbotMessageStreamer, StreamMode


class _RichFailure(Exception):
    pass


@pytest.mark.asyncio
async def test_chatbot_final_resend_retries_rich_before_html(monkeypatch: pytest.MonkeyPatch) -> None:
    source = SimpleNamespace(chat=SimpleNamespace(id=1), message_id=2)
    response = SimpleNamespace(
        chat=SimpleNamespace(id=1),
        message_id=3,
        bot=SimpleNamespace(edit_message_text=AsyncMock(side_effect=_RichFailure())),
    )
    streamer = ChatbotMessageStreamer(source, "header", StreamMode.RICH_EDIT, 0)
    streamer.response_message = response
    rich_resend = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(chatbot_streaming, "TelegramAPIError", _RichFailure)
    monkeypatch.setattr(chatbot_streaming, "send_ai_rich_message", rich_resend)

    await streamer.send_final(Doc("answer"))

    rich_resend.assert_awaited_once()


@pytest.mark.asyncio
async def test_proactive_answer_uses_shared_rich_sender(monkeypatch: pytest.MonkeyPatch) -> None:
    target = SimpleNamespace(message_id=7, message_thread_id=None, text="question", username="user", user_id=1)
    chat = SimpleNamespace(iid="chat", tid=1, type="group", first_name_or_title="Group")
    doc = Doc("answer")
    sent = SimpleNamespace(message_id=8, text="answer", date=None, message_thread_id=None)
    rich_sender = AsyncMock(return_value=sent)
    monkeypatch.setattr(proactive_replies, "send_ai_rich_message_to_chat", rich_sender)
    monkeypatch.setattr(
        proactive_replies,
        "get_chat_default_model_plan",
        AsyncMock(return_value=SimpleNamespace(primary=SimpleNamespace(model_name="model"))),
    )
    monkeypatch.setattr(proactive_replies, "get_service_tier", AsyncMock(return_value=None))
    monkeypatch.setattr(
        proactive_replies,
        "_build_answer_history",
        AsyncMock(return_value=SimpleNamespace(prompt=[], message_history=[])),
    )
    monkeypatch.setattr(
        proactive_replies,
        "build_chatbot_run_config",
        AsyncMock(return_value=SimpleNamespace(agent=None, deps=None, usage_limits=None, request_options=None)),
    )
    monkeypatch.setattr(
        proactive_replies,
        "run_ai_text",
        AsyncMock(return_value=SimpleNamespace(served_model=None, usage=None, output="answer", message_history=[])),
    )
    monkeypatch.setattr(proactive_replies, "build_chatbot_header", AsyncMock(return_value=Doc("header")))
    monkeypatch.setattr(proactive_replies, "build_reply_doc", AsyncMock(return_value=doc))
    monkeypatch.setattr(proactive_replies, "cache_message", AsyncMock())

    await proactive_replies._answer_message(1, chat, target)

    rich_sender.assert_awaited_once_with(1, doc, reply_to_message_id=7, message_thread_id=None)
