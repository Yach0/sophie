from __future__ import annotations

import asyncio
from typing import Any

from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message
from pydantic_ai.messages import ModelRequest, ModelResponse
from pydantic_ai.models import Model
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError
from sentry_sdk.ai import set_conversation_id
from stfu_tg import BlockQuote, Doc, Section
from stfu_tg.doc import Element

from sophie_bot.config import CONFIG
from sophie_bot.db.models.ai.ai_catalog import AIModelPurpose
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.metrics import track_ai_conversation
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.ai.utils.ai_chat_models import get_chat_default_model_plan, resolve_chat_service_tier
from sophie_bot.modules.ai.utils.ai_errors import AIRequestFailed, AIRetryCallback, ai_request_failed_message
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, AIModelPlan, build_model_plan
from sophie_bot.modules.ai.utils.ai_run import (
    AIAgentResult,
    ChatbotStreamOptions,
    TextStreamCallback,
    ToolCallCallback,
    run_ai_stream,
    run_ai_text,
)
from sophie_bot.modules.ai.utils.ai_send import send_ai_rich_message
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
    build_truncated_note,
    truncate_output,
)
from sophie_bot.modules.ai.utils.chatbot_streaming import ChatbotMessageStreamer, StreamMode, build_message_streamer
from sophie_bot.modules.ai.utils.chatbot_tool_history import remember_chatbot_tool_history
from sophie_bot.modules.ai.utils.help_tip import (
    build_help_mode_keyboard,
    build_help_mode_tip,
    should_offer_help_mode,
)
from sophie_bot.modules.ai.utils.mention_usernames import resolve_mention_index
from sophie_bot.modules.ai.utils.message_history import AIMessageHistory
from sophie_bot.modules.ai.utils.research import build_research_markdown_file, retrieve_latest_research_response
from sophie_bot.utils.ai_features import AI_FEATURE_CHATBOT
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.logger import log

__all__ = ("CHATBOT_TOOLS", "ChatbotMessageStreamer", "ai_chatbot_reply")


def _is_explicit_debug_mode(message: Message, user_text: str | None, debug_mode: bool) -> bool:
    if debug_mode:
        return True
    if "^llm_debug" in (user_text or message.text or ""):
        from_user = message.from_user
        return from_user is not None and from_user.id in CONFIG.operators
    return False


async def _reply_debug_history(message: Message, history: AIMessageHistory) -> None:
    await message.reply(
        Section(BlockQuote(history.history_debug(), expandable=True), title="LLM History").to_html(),
        disable_web_page_preview=True,
    )


async def _resolve_model_plan(connection: ChatConnection, model: Model | None, mode: AIMode) -> AIModelPlan:
    """The chatbot's candidates for this chat, with a caller's own model pinned in front.

    A caller that hand-picked a model still gets exactly that model first; what it gains is the
    mode's own candidates behind it, so a pin that fails is no longer a dead end.
    """
    plan = await get_chat_default_model_plan(connection.db_model.iid, chat_tid=connection.db_model.tid, mode=mode)
    if model is None:
        return plan
    pinned = AIModelCandidate(model=model, model_name=model.model_name)
    return build_model_plan([pinned, *plan.candidates], failover=plan.failover)


async def _build_chatbot_header(
    connection: ChatConnection,
    model: Model,
    message_history: list[ModelRequest | ModelResponse],
) -> Element:
    return await build_chatbot_header(connection.db_model.iid, model, message_history)


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
    mention_index = await resolve_mention_index(chat_tid) if "@" in output_text else None
    for _attempt_index in range(8):
        doc = await build_reply_doc(
            header,
            fitted_output_text,
            model,
            result,
            explicit_debug_mode,
            chat_tid=chat_tid,
            mention_index=mention_index,
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
        mention_index=mention_index,
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
    on_reasoning_stream: TextStreamCallback | None = None,
    on_research_progress: ResearchProgressCallback | None = None,
    on_retry: AIRetryCallback | None = None,
    user_text: str | None = None,
    mode: AIMode = AIMode.support,
    stream_options: ChatbotStreamOptions | None = None,
    model_plan: AIModelPlan | None = None,
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
            on_reasoning_stream=on_reasoning_stream,
            on_retry=on_retry,
            stream_options=stream_options,
            model_plan=model_plan,
        )

    return await run_ai_text(
        run_config.agent,
        user_prompt=history.prompt,
        message_history=history.message_history,
        deps=run_config.deps,
        usage_limits=run_config.usage_limits,
        request_options=run_config.request_options,
        on_retry=on_retry,
        model_plan=model_plan,
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
        model_plan = await _resolve_model_plan(connection, model, mode)
        model = model_plan.primary
        message_streamer = await build_message_streamer(message, model, explicit_debug_mode)
        context = SophieAIToolContext(
            connection=connection,
            chat_tid=connection.tid,
            chat_iid=connection.db_model.iid,
            mode=mode,
            user_text=user_text,
            user_tid=message.from_user.id if message.from_user else None,
        )
        history = await prepare_chatbot_history(message, context)
        # Snapshotted before the run: whatever tool calls it already contains were replayed from a
        # previous answer and must not be stored a second time.
        previous_history = list(history.message_history)
        if explicit_debug_mode:
            await _reply_debug_history(message, history)

        service_tier = await resolve_chat_service_tier(
            AIModelPurpose.chatbot, connection.db_model.iid, message.chat.id, mode
        )
        tool_thinking, reasoning_enabled, continuation, partial_on_limit = await asyncio.gather(
            is_enabled("ai_chatbot_tool_thinking", chat_tid=message.chat.id),
            is_enabled("ai_chatbot_stream_reasoning", chat_tid=message.chat.id),
            is_enabled("ai_chatbot_stream_continuation", chat_tid=message.chat.id),
            is_enabled("ai_chatbot_partial_on_limit", chat_tid=message.chat.id),
        )
        on_tool_call = message_streamer.update_thinking_for_tool if message_streamer and tool_thinking else None
        on_reasoning_stream = message_streamer.stream_reasoning if message_streamer and reasoning_enabled else None
        stream_options = ChatbotStreamOptions(continuation=continuation, partial_on_limit=partial_on_limit)
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
                on_reasoning_stream=on_reasoning_stream,
                on_research_progress=message_streamer.update_research_progress if message_streamer else None,
                on_retry=message_streamer.update_retrying if message_streamer else None,
                user_text=user_text,
                mode=mode,
                stream_options=stream_options,
                model_plan=model_plan,
            )
        except AIRequestFailed as err:
            return await _send_chatbot_ai_failure_reply(message, message_streamer, err, **kwargs)

        # Failover may have moved the reply off the model the streamer opened with, so the charge and
        # the header both follow the model that actually answered.
        model = result.served_model or model

        if result.usage:
            try:
                await charge_ai_usage(connection.db_model.iid, AI_FEATURE_CHATBOT, model, result.usage)
            except (PyMongoError, RedisError) as err:
                log.warning("Failed to charge AI usage for chatbot", error=str(err))

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
        # Appended to the doc rather than to the text, so the length-fitting loop above cannot eat
        # it — a truncated reply is exactly the case where the loop is shrinking hardest.
        if result.truncated:
            doc += build_truncated_note()
        if await should_offer_help_mode(message, mode, result.message_history):
            doc += build_help_mode_tip()
            # A private AI session already carries its own reply keyboard, and a message can only
            # have one: there the tip is reachable from that keyboard instead.
            if not kwargs.get("reply_markup"):
                kwargs["reply_markup"] = build_help_mode_keyboard(message)

        if message_streamer:
            final_message = await message_streamer.send_final(doc, **kwargs)
        else:
            final_message = await send_ai_rich_message(message, doc, reply_markup=kwargs.get("reply_markup"))

        # Best effort inside the helper: the reply is already out, so a storage failure only costs
        # the next run its replay.
        await remember_chatbot_tool_history(
            message.chat.id, final_message.message_id, result.message_history, previous_history
        )
        if research_response is not None:
            try:
                await final_message.reply_document(
                    build_research_markdown_file(research_response), caption=_("Research")
                )
            except TelegramAPIError as err:
                log.warning("Failed to send research document", error=str(err))
        return final_message
