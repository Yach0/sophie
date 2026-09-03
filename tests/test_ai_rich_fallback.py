from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from stfu_tg import Doc

from sophie_bot.modules.ai.utils import ai_send, chatbot_streaming, proactive_replies
from sophie_bot.modules.ai.utils.chatbot_streaming import ChatbotMessageStreamer, StreamMode


class _RichFailure(Exception):
    pass


@pytest.mark.asyncio
async def test_chatbot_final_resend_uses_rich_message(monkeypatch: pytest.MonkeyPatch) -> None:
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
async def test_rich_sender_uses_rich_payload() -> None:
    send_rich_message = AsyncMock(return_value=SimpleNamespace())
    message = SimpleNamespace(
        chat=SimpleNamespace(id=1),
        message_id=2,
        message_thread_id=None,
        bot=SimpleNamespace(send_rich_message=send_rich_message),
    )

    await ai_send.send_ai_rich_message(message, Doc("answer"))

    send_rich_message.assert_awaited_once()
    assert send_rich_message.call_args.kwargs["rich_message"].html == Doc("answer").to_rich()


@pytest.mark.asyncio
async def test_rich_sender_propagates_telegram_errors() -> None:
    error = _RichFailure()
    message = SimpleNamespace(
        chat=SimpleNamespace(id=1),
        message_id=2,
        message_thread_id=None,
        bot=SimpleNamespace(send_rich_message=AsyncMock(side_effect=error)),
    )

    with pytest.raises(_RichFailure):
        await ai_send.send_ai_rich_message(message, Doc("answer"))


@pytest.mark.asyncio
async def test_send_ai_rich_message_falls_back_when_reply_target_deleted() -> None:
    from aiogram.exceptions import TelegramBadRequest

    calls: list[dict] = []

    async def mock_send_rich_message(**kwargs):
        calls.append(kwargs)
        if "reply_parameters" in kwargs:
            raise TelegramBadRequest(method=None, message="Bad Request: message to be replied not found")  # type: ignore[arg-type]
        return SimpleNamespace(message_id=99)

    message = SimpleNamespace(
        chat=SimpleNamespace(id=100),
        message_id=200,
        message_thread_id=5,
        bot=SimpleNamespace(send_rich_message=mock_send_rich_message),
    )

    result = await ai_send.send_ai_rich_message(message, Doc("answer"))
    assert result.message_id == 99
    assert len(calls) == 2
    assert "reply_parameters" in calls[0]
    assert "reply_parameters" not in calls[1]


@pytest.mark.asyncio
async def test_chatbot_final_resend_falls_back_to_direct_send_when_reply_fails() -> None:
    from aiogram.exceptions import TelegramBadRequest

    direct_send_mock = AsyncMock(return_value=SimpleNamespace(message_id=77))
    source = SimpleNamespace(
        chat=SimpleNamespace(id=1),
        message_id=2,
        message_thread_id=None,
        reply=AsyncMock(side_effect=TelegramBadRequest(method=None, message="Bad Request: message to be replied not found")),  # type: ignore[arg-type]
        bot=SimpleNamespace(send_message=direct_send_mock),
    )
    response = SimpleNamespace(
        chat=SimpleNamespace(id=1),
        message_id=3,
        edit_text=AsyncMock(side_effect=TelegramBadRequest(method=None, message="Bad Request: message to edit not found")),  # type: ignore[arg-type]
    )
    streamer = ChatbotMessageStreamer(source, "header", StreamMode.HTML_EDIT, 0)
    streamer.response_message = response

    await streamer.send_final(Doc("answer"))

    direct_send_mock.assert_awaited_once()
    assert direct_send_mock.call_args.kwargs["chat_id"] == 1

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
    monkeypatch.setattr(proactive_replies, "get_ai_header_style", AsyncMock(return_value="table"))
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


@pytest.mark.asyncio
async def test_send_ai_rich_message_to_chat_normalizes_reply_to_message_id(monkeypatch: pytest.MonkeyPatch) -> None:
    send_rich_mock = AsyncMock(return_value=SimpleNamespace(message_id=42))
    monkeypatch.setattr(ai_send.bot, "send_rich_message", send_rich_mock)

    await ai_send.send_ai_rich_message_to_chat(
        chat_id=12345,
        doc=Doc("Hello rich"),
        reply_to_message_id=999,
        message_thread_id=10,
    )

    send_rich_mock.assert_awaited_once()
    kwargs = send_rich_mock.call_args.kwargs
    assert kwargs["chat_id"] == 12345
    assert kwargs["message_thread_id"] == 10
    assert kwargs["reply_parameters"].message_id == 999
