from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from aiogram.types import Message
from pydantic_ai.models import Model
from sentry_sdk.ai import set_conversation_id
from stfu_tg import BlockQuote, Doc, Section
from stfu_tg.doc import Element

from sophie_bot.config import CONFIG
from sophie_bot.db.models.ai.ai_catalog import AIModelPurpose
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.metrics import track_ai_conversation
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.ai.utils.ai_chat_models import get_chat_default_model_plan, resolve_chat_service_tier
from sophie_bot.modules.ai.utils.ai_errors import AIRequestFailed, ai_request_failed_message
from sophie_bot.modules.ai.utils.ai_header import AIHeaderStyle, get_ai_header_style
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, AIModelPlan, build_model_plan
from sophie_bot.modules.ai.utils.ai_run import AIAgentResult, ChatbotStreamOptions
from sophie_bot.modules.ai.utils.ai_send import editable_reply_markup, send_ai_rich_message
from sophie_bot.modules.ai.utils.ai_telemetry import ai_span
from sophie_bot.modules.ai.utils.ai_tool import AITool
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext
from sophie_bot.modules.ai.utils.cache_messages import cache_message
from sophie_bot.modules.ai.utils.chatbot_agent import (
    ChatbotRunCallbacks,
    ChatbotRunRequest,
    run_chatbot,
)
from sophie_bot.modules.ai.utils.chatbot_context import prepare_chatbot_history
from sophie_bot.modules.ai.utils.chatbot_response import (
    TELEGRAM_MESSAGE_SAFE_LIMIT,
    build_chatbot_header,
    build_reply_doc,
    model_display_name,
    truncate_output,
    used_tool_labels,
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
from sophie_bot.modules.ai.utils.self_reply import cut_titlebar
from sophie_bot.services.application import ApplicationServices
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.i18n import gettext as _

__all__ = ("ai_chatbot_reply",)


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


async def _resolve_model_plan(
    connection: ChatConnection,
    model: Model | None,
    mode: AIMode,
    *,
    services: ApplicationServices,
) -> AIModelPlan:
    """The chatbot's candidates for this chat, with a caller's own model pinned in front.

    A caller that hand-picked a model still gets exactly that model first; what it gains is the
    mode's own candidates behind it, so a pin that fails is no longer a dead end.
    """
    plan = await get_chat_default_model_plan(
        connection.db_model.iid,
        chat_tid=connection.db_model.tid,
        mode=mode,
        redis=services.redis,
    )
    if model is None:
        return plan
    pinned = AIModelCandidate(model=model, model_name=model.model_name)
    return build_model_plan([pinned, *plan.candidates], failover=plan.failover)


async def _build_chatbot_header(
    connection: ChatConnection,
    style: AIHeaderStyle,
    model_label: str | None = None,
    *,
    services: ApplicationServices,
) -> Element | str | None:
    return await build_chatbot_header(
        connection.db_model.iid,
        style,
        model_label,
        redis=services.redis,
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
    header: Element | str | None,
    output_text: str,
    model: Model | None,
    result: AIAgentResult[str] | None,
    explicit_debug_mode: bool,
    chat_tid: int,
    tool_labels: Sequence[AITool] = (),
    strip_alien_html_tags: bool = False,
    *,
    services: ApplicationServices,
) -> Doc:
    fitted_output_text = output_text
    mention_index = (
        await resolve_mention_index(
            chat_tid,
            redis=services.redis,
        )
        if "@" in output_text
        else None
    )
    for _attempt_index in range(8):
        doc = await build_reply_doc(
            header,
            fitted_output_text,
            model,
            result,
            explicit_debug_mode,
            chat_tid=chat_tid,
            mention_index=mention_index,
            redis=services.redis,
            tool_labels=tool_labels,
            strip_alien_html_tags=strip_alien_html_tags,
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
        redis=services.redis,
        tool_labels=tool_labels,
        strip_alien_html_tags=strip_alien_html_tags,
    )


async def _send_chatbot_ai_failure_reply(
    message: Message,
    message_streamer: ChatbotMessageStreamer | None,
    error: AIRequestFailed,
    **reply_kwargs: Any,
) -> Message:
    failure_message = ai_request_failed_message(error=error)
    if message_streamer and message_streamer.response_message is not None:
        await message_streamer.stop()
        edited_message = await message_streamer.response_message.edit_text(
            text=failure_message["text"],
            disable_web_page_preview=True,
            reply_markup=editable_reply_markup(reply_kwargs.get("reply_markup")),
        )
        if isinstance(edited_message, Message):
            return edited_message
        return message_streamer.response_message

    return await message.reply(**failure_message, disable_web_page_preview=True, **reply_kwargs)


async def ai_chatbot_reply(
    message: Message,
    connection: ChatConnection,
    user_text: str | None = None,
    debug_mode: bool = False,
    model: Model | None = None,
    mode: AIMode = AIMode.support,
    *,
    services: ApplicationServices,
    **kwargs: Any,
) -> Any:
    with ai_span("ai.chatbot_reply", mode=mode.value) as span:
        try:
            reply = await _ai_chatbot_reply(
                message,
                connection,
                user_text,
                debug_mode,
                model,
                mode,
                services=services,
                **kwargs,
            )
        except Exception as error:
            if span is not None:
                span.set_attribute("outcome", "error")
                span.set_attribute("error_type", type(error).__name__)
                status_code = getattr(error, "status_code", None)
                if isinstance(status_code, int):
                    span.set_attribute("status_code", status_code)
            raise
        if span is not None:
            span.set_attribute("outcome", "sent" if reply is not None else "skipped")
        return reply


async def _ai_chatbot_reply(
    message: Message,
    connection: ChatConnection,
    user_text: str | None = None,
    debug_mode: bool = False,
    model: Model | None = None,
    mode: AIMode = AIMode.support,
    *,
    services: ApplicationServices,
    **kwargs: Any,
) -> Any:
    """
    Sends a reply from AI based on user input and message history.
    """
    if not await is_enabled("ai_chatbot", chat_tid=message.chat.id, redis=services.redis):
        return None

    if not connection.db_model:
        return None

    async with track_ai_conversation():
        set_conversation_id(str(connection.db_model.iid))
        explicit_debug_mode = _is_explicit_debug_mode(message, user_text, debug_mode)
        model_plan = await _resolve_model_plan(connection, model, mode, services=services)
        model = model_plan.primary
        header_style = await get_ai_header_style("chatbot", message.chat.id, redis=services.redis)
        strip_alien_html_tags = await is_enabled(
            "ai_chatbot_strip_alien_html_tags",
            chat_tid=message.chat.id,
            redis=services.redis,
        )
        message_streamer = await build_message_streamer(
            message,
            model,
            explicit_debug_mode,
            header_style,
            redis=services.redis,
            strip_alien_html_tags=strip_alien_html_tags,
        )
        context = SophieAIToolContext(
            connection=connection,
            chat_tid=connection.tid,
            chat_iid=connection.db_model.iid,
            mode=mode,
            user_text=user_text,
            user_tid=message.from_user.id if message.from_user else None,
            research_progress_callback=(message_streamer.update_research_progress if message_streamer else None),
            services=services,
        )
        history = await prepare_chatbot_history(message, context)
        # Whatever tool calls history already contains were replayed from a previous
        # answer and must not be stored a second time.
        previous_history = list(history.message_history)
        if explicit_debug_mode:
            await _reply_debug_history(message, history)

        service_tier = await resolve_chat_service_tier(
            AIModelPurpose.chatbot,
            connection.db_model.iid,
            message.chat.id,
            mode,
            redis=services.redis,
        )
        reasoning_enabled, continuation = await asyncio.gather(
            is_enabled(
                "ai_chatbot_stream_reasoning",
                chat_tid=message.chat.id,
                redis=services.redis,
            ),
            is_enabled(
                "ai_chatbot_stream_continuation",
                chat_tid=message.chat.id,
                redis=services.redis,
            ),
        )
        on_tool_call = message_streamer.update_thinking_for_tool if message_streamer else None
        on_reasoning_stream = message_streamer.stream_reasoning if message_streamer and reasoning_enabled else None
        stream_options = ChatbotStreamOptions(continuation=continuation)
        try:
            result = await run_chatbot(
                ChatbotRunRequest(
                    context=context,
                    history=history,
                    model_plan=model_plan,
                    service_tier=service_tier,
                    thread_id=message.message_thread_id,
                    stream_options=stream_options,
                    callbacks=ChatbotRunCallbacks(
                        on_text_stream=(
                            message_streamer.stream
                            if message_streamer and message_streamer.mode != StreamMode.THINKING_ONLY
                            else None
                        ),
                        on_tool_call=on_tool_call,
                        on_reasoning_stream=on_reasoning_stream,
                        on_retry=(message_streamer.update_retrying if message_streamer else None),
                    ),
                )
            )
        except AIRequestFailed as err:
            return await _send_chatbot_ai_failure_reply(message, message_streamer, err, **kwargs)

        # Failover may have moved the reply off the model the streamer opened with, so the charge and
        # the header both follow the model that actually answered.
        model = result.served_model or model

        header_style, show_model_name = await asyncio.gather(
            get_ai_header_style("chatbot", message.chat.id, redis=services.redis),
            is_enabled(
                "ai_chatbot_show_model_name",
                chat_tid=message.chat.id,
                redis=services.redis,
            ),
        )
        header = await _build_chatbot_header(
            connection,
            header_style,
            model_display_name(model) if show_model_name else None,
            services=services,
        )
        research_response = (
            retrieve_latest_research_response(result.message_history)
            if await is_enabled(
                "ai_chatbot_research_quote",
                chat_tid=message.chat.id,
                redis=services.redis,
            )
            else None
        )
        tool_labels = used_tool_labels(result.message_history[len(previous_history) :])
        output_text = truncate_output(header, str(result.output))
        doc = await _build_fitting_reply_doc(
            header,
            output_text,
            model,
            result,
            explicit_debug_mode,
            chat_tid=message.chat.id,
            services=services,
            tool_labels=tool_labels,
            strip_alien_html_tags=strip_alien_html_tags,
        )
        if await should_offer_help_mode(
            message,
            mode,
            result.message_history,
            previous_message_count=len(previous_history),
            redis=services.redis,
        ):
            doc += build_help_mode_tip()
            # A private AI session already carries its own reply keyboard, and a message can only
            # have one: there the tip is reachable from that keyboard instead.
            if not kwargs.get("reply_markup"):
                kwargs["reply_markup"] = build_help_mode_keyboard(message)

        if message_streamer:
            final_message = await message_streamer.send_final(doc, **kwargs)
        else:
            final_message = await send_ai_rich_message(message, doc, reply_markup=kwargs.get("reply_markup"))

        await cache_message(
            cut_titlebar(doc.to_md(), tool_labels=tool_labels),
            message.chat.id,
            CONFIG.bot_id,
            final_message.message_id,
            final_message.date,
            "Sophie",
            is_bot=True,
            message_thread_id=final_message.message_thread_id,
            handled_by_ai=True,
            eligible_for_proactive_ai=False,
            reply_to_message_id=message.message_id,
            reply_to_user_id=message.from_user.id if message.from_user else None,
            reply_to_username=(
                message.from_user.username or message.from_user.full_name if message.from_user else None
            ),
            redis=services.redis,
        )

        await remember_chatbot_tool_history(
            message.chat.id,
            final_message.message_id,
            result.message_history,
            previous_history,
            redis=services.redis,
        )
        if research_response is not None:
            await final_message.reply_document(build_research_markdown_file(research_response), caption=_("Research"))
        return final_message
