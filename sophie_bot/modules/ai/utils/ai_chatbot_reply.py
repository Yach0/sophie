from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram.exceptions import TelegramAPIError
from aiogram.types import InputRichMessage, Message, ReplyParameters
from pydantic_ai.messages import ModelRequest, ModelResponse
from pydantic_ai.models import Model
from sentry_sdk.ai import set_conversation_id
from stfu_tg import BlockQuote, Doc, Section
from stfu_tg.doc import Element

from sophie_bot.metrics import track_ai_conversation
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.ai.utils.ai_run import AIAgentResult, run_ai_stream, run_ai_text
from sophie_bot.modules.ai.utils.ai_errors import AIRequestFailed, AIRetryCallback, ai_request_failed_message
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.utils.ai_chat_models import get_chat_default_model
from sophie_bot.modules.ai.utils.ai_tool_context import ResearchProgressCallback, SophieAIToolContext
from sophie_bot.modules.ai.utils.ai_usage_service import charge_ai_usage
from sophie_bot.modules.ai.utils.chatbot_agent import (
    CHATBOT_TOOLS,
    build_chatbot_run_config,
)
from sophie_bot.modules.ai.utils.chatbot_context import prepare_chatbot_history
from sophie_bot.modules.ai.utils.chatbot_response import (
    TELEGRAM_MESSAGE_SAFE_LIMIT,
    build_chatbot_header,
    build_reply_doc,
    truncate_output,
)
from sophie_bot.modules.ai.utils.chatbot_streaming import ChatbotMessageStreamer, StreamMode, build_message_streamer
from sophie_bot.modules.ai.utils.message_history import AIMessageHistory
from sophie_bot.modules.ai.utils.research import build_research_markdown_file, retrieve_latest_research_response
from sophie_bot.utils.ai_features import AI_FEATURE_CHATBOT
from sophie_bot.utils.feature_flags import get_service_tier, is_enabled
from sophie_bot.utils.i18n import gettext as _

TextStreamCallback = Callable[[str], Awaitable[None]]
ToolCallCallback = Callable[[str], Awaitable[None]]

__all__ = ("CHATBOT_TOOLS", "ChatbotMessageStreamer", "ai_chatbot_reply")


def _is_explicit_debug_mode(message: Message, user_text: str | None, debug_mode: bool) -> bool:
    if debug_mode:
        return True
    if "^llm_debug" in (user_text or message.text or ""):
        from sophie_bot.config import CONFIG

        from_user = message.from_user
        return from_user is not None and from_user.id in CONFIG.operators
    return False


async def _reply_debug_history(message: Message, history: AIMessageHistory) -> None:
    await message.reply(
        Section(BlockQuote(history.history_debug(), expandable=True), title="LLM History").to_html(),
        disable_web_page_preview=True,
    )


async def _resolve_model(connection: ChatConnection, model: Model | None, mode: AIMode) -> Model:
    if model is not None:
        return model
    return await get_chat_default_model(connection.db_model.iid, chat_tid=connection.db_model.tid, mode=mode)


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


def _truncate_to_boundary(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    if max_length <= 3:
        return text[:max_length]

    truncated_text = text[: max_length - 3]
    split_index = max(
        truncated_text.rfind("\n\n"),
        truncated_text.rfind("\n"),
        truncated_text.rfind(". "),
        truncated_text.rfind(" "),
    )
    if split_index >= max_length // 2:
        truncated_text = truncated_text[:split_index]
    return truncated_text.rstrip() + "..."


async def _build_fitting_reply_doc(
    header: Element,
    output_text: str,
    model: Model | None,
    result: AIAgentResult[str] | None,
    explicit_debug_mode: bool,
    chat_tid: int,
) -> Doc:
    fitted_output_text = output_text
    for _attempt_index in range(8):
        doc = await build_reply_doc(
            header,
            fitted_output_text,
            model,
            result,
            explicit_debug_mode,
            chat_tid=chat_tid,
        )
        html_length = len(doc.to_html())
        if html_length <= TELEGRAM_MESSAGE_SAFE_LIMIT:
            return doc

        overflow = html_length - TELEGRAM_MESSAGE_SAFE_LIMIT
        next_length = len(fitted_output_text) - overflow - 128
        if next_length >= len(fitted_output_text):
            next_length = len(fitted_output_text) - 256
        if next_length <= 0:
            break
        fitted_output_text = _truncate_to_boundary(fitted_output_text, next_length)

    return await build_reply_doc(
        header,
        _truncate_to_boundary(fitted_output_text, max(0, min(len(fitted_output_text), 512))),
        model,
        result,
        explicit_debug_mode,
        chat_tid=chat_tid,
    )


async def _generate_chatbot_result(
    message: Message,
    connection: ChatConnection,
    history: AIMessageHistory,
    model: Model,
    explicit_debug_mode: bool,
    service_tier: str | None = None,
    on_text_stream: TextStreamCallback | None = None,
    on_tool_call: ToolCallCallback | None = None,
    on_research_progress: ResearchProgressCallback | None = None,
    on_retry: AIRetryCallback | None = None,
    user_text: str | None = None,
    mode: AIMode = AIMode.support,
) -> AIAgentResult[str]:
    run_config = await build_chatbot_run_config(
        connection.tid,
        connection,
        model,
        user_text=user_text,
        user_tid=message.from_user.id if message.from_user else None,
        progress_callback=on_research_progress,
        thread_id=message.message_thread_id,
        service_tier=service_tier,
        mode=mode,
    )

    if on_text_stream is not None:
        return await run_ai_stream(
            run_config.agent,
            user_prompt=history.prompt,
            message_history=history.message_history,
            on_text_stream=on_text_stream,
            deps=run_config.deps,
            usage_limits=run_config.usage_limits,
            request_options=run_config.request_options,
            on_tool_call=on_tool_call,
            on_retry=on_retry,
        )

    return await run_ai_text(
        run_config.agent,
        user_prompt=history.prompt,
        message_history=history.message_history,
        deps=run_config.deps,
        usage_limits=run_config.usage_limits,
        request_options=run_config.request_options,
        on_retry=on_retry,
    )


async def _send_chatbot_ai_failure_reply(
    message: Message,
    message_streamer: ChatbotMessageStreamer | None,
    error: AIRequestFailed,
    **reply_kwargs: Any,
) -> Message:
    failure_message = ai_request_failed_message(error.sentry_event_id)
    if message_streamer and message_streamer.response_message is not None:
        try:
            edited_message = await message_streamer.response_message.edit_text(
                text=failure_message["text"],
                disable_web_page_preview=True,
                **reply_kwargs,
            )
            if isinstance(edited_message, Message):
                return edited_message
            return message_streamer.response_message
        except TelegramAPIError:
            pass

    return await message.reply(**failure_message, disable_web_page_preview=True, **reply_kwargs)


async def ai_chatbot_reply(
    message: Message,
    connection: ChatConnection,
    user_text: str | None = None,
    debug_mode: bool = False,
    model: Model | None = None,
    mode: AIMode = AIMode.support,
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
        set_conversation_id(str(connection.db_model.iid))
        explicit_debug_mode = _is_explicit_debug_mode(message, user_text, debug_mode)
        model = await _resolve_model(connection, model, mode)
        message_streamer = await build_message_streamer(message, connection, model, explicit_debug_mode)
        context = SophieAIToolContext(
            connection=connection,
            chat_tid=connection.tid,
            chat_iid=connection.db_model.iid,
            mode=mode,
            user_text=user_text,
            user_tid=message.from_user.id if message.from_user else None,
        )
        history = await prepare_chatbot_history(message, context)
        if explicit_debug_mode:
            await _reply_debug_history(message, history)

        use_rich_streaming = await is_enabled("ai_chatbot_rich_streaming", chat_tid=message.chat.id)
        service_tier = await get_service_tier("ai_chatbot_service_tier", chat_tid=message.chat.id)
        on_tool_call = (
            message_streamer.update_thinking_for_tool
            if message_streamer and await is_enabled("ai_chatbot_tool_thinking", chat_tid=message.chat.id)
            else None
        )
        try:
            result = await _generate_chatbot_result(
                message,
                connection,
                history,
                model,
                explicit_debug_mode,
                service_tier,
                on_text_stream=message_streamer.stream
                if message_streamer and message_streamer.mode != StreamMode.THINKING_ONLY
                else None,
                on_tool_call=on_tool_call,
                on_research_progress=message_streamer.update_research_progress if message_streamer else None,
                on_retry=message_streamer.update_retrying if message_streamer else None,
                user_text=user_text,
                mode=mode,
            )
        except AIRequestFailed as err:
            return await _send_chatbot_ai_failure_reply(message, message_streamer, err, **kwargs)

        if result.usage:
            await charge_ai_usage(connection.db_model.iid, AI_FEATURE_CHATBOT, model, result.usage)

        header = await _build_chatbot_header(connection, model, result.message_history)
        research_response = (
            retrieve_latest_research_response(result.message_history)
            if await is_enabled("ai_chatbot_research_quote", chat_tid=message.chat.id)
            else None
        )
        output_text = truncate_output(header, str(result.output))
        doc = await _build_fitting_reply_doc(
            header,
            output_text,
            model,
            result,
            explicit_debug_mode,
            chat_tid=message.chat.id,
        )
        if message_streamer:
            final_message = await message_streamer.send_final(doc, **kwargs)
        elif use_rich_streaming:
            try:
                final_message = await message.bot.send_rich_message(  # ty: ignore[unresolved-attribute]
                    chat_id=message.chat.id,
                    rich_message=InputRichMessage(html=doc.to_rich()),
                    reply_parameters=ReplyParameters(message_id=message.message_id),
                    message_thread_id=message.message_thread_id,
                    reply_markup=kwargs.get("reply_markup"),
                )
            except TelegramAPIError:
                final_message = await message.reply(doc.to_html(), disable_web_page_preview=True, **kwargs)
        else:
            final_message = await message.reply(doc.to_html(), disable_web_page_preview=True, **kwargs)

        if research_response is not None:
            await final_message.reply_document(build_research_markdown_file(research_response), caption=_("Research"))
        return final_message
