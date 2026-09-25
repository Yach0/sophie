from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from stfu_tg import Doc

from sophie_bot.modules.ai.handlers.research import ResearchProgressMessage
from sophie_bot.modules.ai.utils import ai_send, proactive_replies
from sophie_bot.modules.ai.utils.chatbot_streaming import ChatbotMessageStreamer, StreamMode


class _RichFailure(Exception):
    pass


@pytest.mark.asyncio
async def test_chatbot_final_edit_propagates_telegram_failure(
    test_redis: object,
) -> None:
    source = SimpleNamespace(chat=SimpleNamespace(id=1), message_id=2)
    error = _RichFailure()
    edit_message_text = AsyncMock(side_effect=error)
    response = SimpleNamespace(
        chat=SimpleNamespace(id=1),
        message_id=3,
        bot=SimpleNamespace(edit_message_text=edit_message_text),
    )
    streamer = ChatbotMessageStreamer(
        source,
        "header",
        StreamMode.EDIT,
        0,
        redis=test_redis,
    )
    streamer.response_message = response

    with pytest.raises(_RichFailure) as raised:
        await streamer.send_final(Doc("answer"))

    assert raised.value is error
    edit_message_text.assert_awaited_once()

@pytest.mark.asyncio
async def test_chatbot_progress_edit_propagates_telegram_failure(test_redis: object) -> None:
    source = SimpleNamespace(chat=SimpleNamespace(id=1), message_id=2)
    error = _RichFailure()
    edit_message_text = AsyncMock(side_effect=error)
    response = SimpleNamespace(
        chat=SimpleNamespace(id=1),
        message_id=3,
        bot=SimpleNamespace(edit_message_text=edit_message_text),
    )
    streamer = ChatbotMessageStreamer(source, "header", StreamMode.EDIT, 0, redis=test_redis)
    streamer.response_message = response

    with pytest.raises(_RichFailure) as raised:
        await streamer.stream("partial answer")

    assert raised.value is error
    edit_message_text.assert_awaited_once()

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
async def test_research_final_edit_propagates_telegram_failure() -> None:
    error = _RichFailure()
    edit_message_text = AsyncMock(side_effect=error)
    bot = SimpleNamespace(edit_message_text=edit_message_text)
    progress = ResearchProgressMessage(SimpleNamespace(chat=SimpleNamespace(id=1), message_id=2), "emoji", bot)

    with pytest.raises(_RichFailure) as raised:
        await progress.send_final(Doc("answer"))

    assert raised.value is error
    edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_ai_rich_message_propagates_deleted_reply_target() -> None:
    calls: list[dict] = []
    error = TelegramBadRequest(method=None, message="Bad Request: message to be replied not found")  # type: ignore[arg-type]

    async def mock_send_rich_message(**kwargs):
        calls.append(kwargs)
        raise error

    message = SimpleNamespace(
        chat=SimpleNamespace(id=100),
        message_id=200,
        message_thread_id=5,
        bot=SimpleNamespace(send_rich_message=mock_send_rich_message),
    )

    with pytest.raises(TelegramBadRequest) as raised:
        await ai_send.send_ai_rich_message(message, Doc("answer"))

    assert raised.value is error
    assert len(calls) == 1
    assert "reply_parameters" in calls[0]


@pytest.mark.asyncio
async def test_proactive_answer_uses_shared_rich_sender(monkeypatch: pytest.MonkeyPatch) -> None:
    target = SimpleNamespace(message_id=7, message_thread_id=None, text="question", username="user", user_id=1)
    chat = SimpleNamespace(iid="chat", tid=1, type="group", first_name_or_title="Group")
    doc = Doc("answer")
    sent = SimpleNamespace(message_id=8, text="answer", date=None, message_thread_id=None)
    rich_sender = AsyncMock(return_value=sent)
    model = SimpleNamespace(model_name="model")
    build_chatbot_header = AsyncMock(return_value=Doc("header"))
    build_reply_doc = AsyncMock(return_value=doc)
    monkeypatch.setattr(proactive_replies, "send_ai_rich_message_to_chat", rich_sender)
    monkeypatch.setattr(
        proactive_replies,
        "get_chat_default_model_plan",
        AsyncMock(return_value=SimpleNamespace(primary=model)),
    )
    monkeypatch.setattr(proactive_replies, "get_service_tier", AsyncMock(return_value=None))
    get_ai_header_style = AsyncMock(return_value="simple")
    monkeypatch.setattr(proactive_replies, "get_ai_header_style", get_ai_header_style)
    monkeypatch.setattr(
        proactive_replies,
        "is_enabled",
        AsyncMock(side_effect=lambda feature, **_kwargs: feature == "ai_chatbot_strip_alien_html_tags"),
    )
    monkeypatch.setattr(
        proactive_replies,
        "_build_answer_history",
        AsyncMock(return_value=SimpleNamespace(prompt=[], message_history=[])),
    )
    monkeypatch.setattr(proactive_replies, "build_chatbot_header", build_chatbot_header)
    monkeypatch.setattr(proactive_replies, "build_reply_doc", build_reply_doc)
    monkeypatch.setattr(proactive_replies, "cache_message", AsyncMock())
    run_chatbot = AsyncMock(
        return_value=SimpleNamespace(
            served_model=None,
            usage=None,
            output="answer",
            message_history=[],
        )
    )
    monkeypatch.setattr(proactive_replies, "run_chatbot", run_chatbot)
    services = SimpleNamespace(redis=object(), bot=object())

    await proactive_replies._answer_message(
        1,
        chat,
        target,
        services=services,
    )

    get_ai_header_style.assert_awaited_once_with("chatbot", 1, redis=services.redis)
    build_chatbot_header.assert_awaited_once_with(
        "chat",
        "simple",
        None,
        redis=services.redis,
    )
    assert build_reply_doc.await_args is not None
    rich_sender.assert_awaited_once_with(
        1,
        doc,
        reply_to_message_id=7,
        message_thread_id=None,
        bot=services.bot,
    )
    cache_message = proactive_replies.cache_message
    cache_message.assert_awaited_once()
    cache_kwargs = cache_message.await_args.kwargs
    assert cache_message.await_args.args[0] == "answer"
    assert cache_kwargs["is_bot"] is True
    assert cache_kwargs["reply_to_message_id"] == 7
    assert cache_kwargs["reply_to_user_id"] == 1


@pytest.mark.asyncio
async def test_send_ai_rich_message_to_chat_normalizes_reply_to_message_id() -> None:
    send_rich_mock = AsyncMock(return_value=SimpleNamespace(message_id=42))
    bot = SimpleNamespace(send_rich_message=send_rich_mock)

    await ai_send.send_ai_rich_message_to_chat(
        chat_id=12345,
        doc=Doc("Hello rich"),
        reply_to_message_id=999,
        message_thread_id=10,
        bot=bot,
    )

    send_rich_mock.assert_awaited_once()
    kwargs = send_rich_mock.call_args.kwargs
    assert kwargs["chat_id"] == 12345
    assert kwargs["message_thread_id"] == 10
    assert kwargs["reply_parameters"].message_id == 999
