from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram.enums import ChatType
from aiogram.types import Message
from pydantic_ai.messages import ModelRequest, ModelResponse
from pydantic_ai.models import Model
from stfu_tg import BlockQuote, Section
from stfu_tg.doc import Element

from sophie_bot.metrics import track_ai_conversation, track_ai_usage
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.ai.utils.ai_agent_run import AIAgentResult
from sophie_bot.modules.ai.utils.ai_get_provider import get_chat_default_model
from sophie_bot.modules.ai.utils.ai_tool_context import ResearchProgressCallback, SophieAIToolContext
from sophie_bot.modules.ai.utils.ai_usage_service import charge_ai_usage
from sophie_bot.modules.ai.utils.chatbot_agent import (
    CHATBOT_TOOLS,
    build_chatbot_agent,
    build_chatbot_usage_limits,
    get_chatbot_tools,
)
from sophie_bot.modules.ai.utils.chatbot_context import prepare_chatbot_history
from sophie_bot.modules.ai.utils.chatbot_response import build_chatbot_header, build_reply_doc, truncate_output
from sophie_bot.modules.ai.utils.chatbot_streaming import ChatbotMessageStreamer, build_message_streamer
from sophie_bot.modules.ai.utils.draft_stream import MessageDraftStreamer
from sophie_bot.modules.ai.utils.new_ai_chatbot import AIRequestOptions, new_ai_generate, new_ai_generate_stream
from sophie_bot.modules.ai.utils.new_message_history import NewAIMessageHistory
from sophie_bot.utils.ai_features import AI_FEATURE_CHATBOT
from sophie_bot.utils.feature_flags import get_service_tier, is_enabled

TextStreamCallback = Callable[[str], Awaitable[None]]
ToolCallCallback = Callable[[str], Awaitable[None]]

__all__ = ("CHATBOT_TOOLS", "ChatbotMessageStreamer", "ai_chatbot_reply")


def _build_session_id(chat_iid: object, thread_id: int | None) -> str:
    if thread_id:
        return f"{chat_iid}:{thread_id}"
    return str(chat_iid)


def _is_explicit_debug_mode(message: Message, user_text: str | None, debug_mode: bool) -> bool:
    if debug_mode:
        return True
    return "^llm_debug" in (user_text or message.text or "")


async def _reply_debug_history(message: Message, history: NewAIMessageHistory) -> None:
    await message.reply(
        Section(BlockQuote(history.history_debug(), expandable=True), title="LLM History").to_html(),
        disable_web_page_preview=True,
    )


async def _resolve_model(connection: ChatConnection, model: Model | None) -> Model:
    if model is not None:
        return model
    return await get_chat_default_model(connection.db_model.iid, chat_tid=connection.db_model.tid)


async def _build_chatbot_header(
    connection: ChatConnection,
    model: Model,
    message_history: list[ModelRequest | ModelResponse],
    additional_header_items: list[Element] | None = None,
    skip_battery: bool = False,
) -> Element:
    return await build_chatbot_header(
        connection.db_model.iid,
        model,
        message_history,
        additional_header_items=additional_header_items,
        skip_battery=skip_battery,
    )


_build_reply_doc = build_reply_doc
_truncate_output = truncate_output


async def _generate_chatbot_result(
    message: Message,
    connection: ChatConnection,
    history: NewAIMessageHistory,
    model: Model,
    explicit_debug_mode: bool,
    service_tier: str | None = None,
    on_text_stream: TextStreamCallback | None = None,
    on_tool_call: ToolCallCallback | None = None,
    on_research_progress: ResearchProgressCallback | None = None,
    user_text: str | None = None,
) -> AIAgentResult[str]:
    allow_draft_streaming = message.chat.type == ChatType.PRIVATE and not explicit_debug_mode and on_text_stream is None
    draft_streamer = MessageDraftStreamer(message=message, enabled=allow_draft_streaming)
    tools = await get_chatbot_tools(connection.tid)
    context = SophieAIToolContext(
        connection=connection,
        chat_tid=connection.tid,
        chat_iid=connection.db_model.iid,
        user_text=user_text,
        research_progress_callback=on_research_progress,
    )
    agent = build_chatbot_agent(model, tools)
    request_options = AIRequestOptions(
        user_tracking_id=connection.db_model.iid,
        session_id=_build_session_id(connection.db_model.iid, message.message_thread_id),
        service_tier=service_tier,
    )
    agent_kwargs = {
        "deps": context,
        "usage_limits": await build_chatbot_usage_limits(connection.tid),
    }

    if on_text_stream is not None or allow_draft_streaming:
        return await new_ai_generate_stream(
            history,
            model=model,
            agent=agent,
            agent_kwargs=agent_kwargs,
            on_text_stream=on_text_stream or draft_streamer.stream,
            request_options=request_options,
            on_tool_call=on_tool_call,
        )

    return await new_ai_generate(
        history,
        model=model,
        agent=agent,
        agent_kwargs=agent_kwargs,
        request_options=request_options,
    )


async def ai_chatbot_reply(
    message: Message,
    connection: ChatConnection,
    user_text: str | None = None,
    debug_mode: bool = False,
    model: Model | None = None,
    **kwargs: Any,
) -> Any:
    """
    Sends a reply from AI based on user input and message history.
    """
    if not await is_enabled("ai_chatbot", chat_tid=message.chat.id):
        return None

    if not connection.db_model:
        return None

    async with track_ai_conversation():
        explicit_debug_mode = _is_explicit_debug_mode(message, user_text, debug_mode)
        model = await _resolve_model(connection, model)
        message_streamer = await build_message_streamer(message, connection, model, explicit_debug_mode)
        context = SophieAIToolContext(
            connection=connection,
            chat_tid=connection.tid,
            chat_iid=connection.db_model.iid,
            user_text=user_text,
        )
        history = await prepare_chatbot_history(message, context)
        if explicit_debug_mode:
            await _reply_debug_history(message, history)

        service_tier = await get_service_tier("ai_chatbot_service_tier", chat_tid=message.chat.id)
        on_tool_call = (
            message_streamer.update_thinking_for_tool
            if message_streamer and await is_enabled("ai_chatbot_tool_thinking", chat_tid=message.chat.id)
            else None
        )
        result = await _generate_chatbot_result(
            message,
            connection,
            history,
            model,
            explicit_debug_mode,
            service_tier,
            on_text_stream=message_streamer.stream if message_streamer and message_streamer.enabled else None,
            on_tool_call=on_tool_call,
            on_research_progress=message_streamer.update_research_progress if message_streamer else None,
            user_text=user_text,
        )

        if result.usage:
            track_ai_usage(model, result.usage)
            await charge_ai_usage(connection.db_model.iid, AI_FEATURE_CHATBOT, model, result.usage)

        header = await _build_chatbot_header(connection, model, result.message_history)
        output_text = _truncate_output(header, str(result.output))
        doc = await _build_reply_doc(
            header,
            output_text,
            model,
            result,
            explicit_debug_mode,
            chat_tid=message.chat.id,
        )
        if message_streamer:
            return await message_streamer.send_final(doc, **kwargs)
        return await message.reply(doc.to_html(), disable_web_page_preview=True, **kwargs)
