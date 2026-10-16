from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any

from aiogram.types import Message
from pydantic_ai.models import Model
from sentry_sdk.ai import set_conversation_id
from stfu_tg import BlockQuote, Section

from sophie_bot.config import CONFIG
from sophie_bot.db.models.ai.ai_catalog import AIModelPurpose
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.metrics import track_ai_conversation
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.ai.utils.ai_chat_models import get_chat_default_model_plan, resolve_chat_service_tier
from sophie_bot.modules.ai.utils.ai_errors import AIRequestFailed, ai_request_failed_message
from sophie_bot.modules.ai.utils.ai_model_factory import registered_context_window_tokens
from sophie_bot.modules.ai.utils.ai_model_plan import AIModelCandidate, AIModelPlan, build_model_plan
from sophie_bot.modules.ai.utils.ai_send import editable_reply_markup, send_ai_rich_message
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext
from sophie_bot.modules.ai.utils.cache_messages import MessageType, enqueue_cached_message
from sophie_bot.modules.ai.utils.chatbot_agent import (
    ChatbotRunCallbacks,
    ChatbotRunRequest,
    run_chatbot,
)
from sophie_bot.modules.ai.utils.chatbot_context import prepare_chatbot_history
from sophie_bot.modules.ai.utils.chatbot_response import (
    build_chatbot_reply_presentation,
    select_mention_policy,
)
from sophie_bot.modules.ai.utils.chatbot_streaming import ChatbotMessageStreamer, build_message_streamer
from sophie_bot.modules.ai.utils.chatbot_tool_history import collect_tool_call_ids, remember_chatbot_tool_history
from sophie_bot.modules.ai.utils.help_tip import (
    build_help_mode_keyboard,
    build_help_mode_tip,
    should_offer_help_mode,
)
from sophie_bot.modules.ai.utils.mention_usernames import MentionPolicy
from sophie_bot.modules.ai.utils.modern_context import ModernContext
from sophie_bot.modules.ai.utils.old_context import OldContext
from sophie_bot.modules.ai.utils.research import build_research_markdown_file, retrieve_latest_research_response
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


async def _reply_debug_history(message: Message, history: OldContext | ModernContext) -> None:
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
    """The chatbot's candidates for this chat, with a caller's own model pinned in front."""
    plan = await get_chat_default_model_plan(
        connection.db_model.id,
        chat_tid=connection.db_model.tid,
        mode=mode,
        redis=services.redis,
    )
    if model is None:
        return plan
    pinned = AIModelCandidate(
        model=model,
        model_name=model.model_name,
        context_window_tokens=registered_context_window_tokens(model.model_name),
    )
    return build_model_plan([pinned, *plan.candidates])


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
    """
    Sends a reply from AI based on user input and message history.
    """
    if not await is_enabled("ai_chatbot", chat_tid=message.chat.id, redis=services.redis):
        return None

    if not connection.db_model:
        return None

    async with track_ai_conversation(), AsyncExitStack() as cleanup:
        set_conversation_id(str(connection.db_model.id))
        explicit_debug_mode = _is_explicit_debug_mode(message, user_text, debug_mode)
        model_plan = await _resolve_model_plan(connection, model, mode, services=services)
        model = model_plan.primary
        strip_alien_html_tags = await is_enabled(
            "ai_chatbot_strip_alien_html_tags",
            chat_tid=message.chat.id,
            redis=services.redis,
        )
        message_streamer = await build_message_streamer(
            message,
            explicit_debug_mode,
            redis=services.redis,
            strip_alien_html_tags=strip_alien_html_tags,
        )
        context = SophieAIToolContext(
            connection=connection,
            chat_tid=connection.tid,
            chat_iid=connection.db_model.id,
            mode=mode,
            user_text=user_text,
            user_tid=message.from_user.id if message.from_user else None,
            research_progress_callback=(message_streamer.update_research_progress if message_streamer else None),
            services=services,
        )
        try:
            history = await prepare_chatbot_history(
                message,
                context,
                model_plan=model_plan,
                on_activity=(message_streamer.update_processing_activity if message_streamer else None),
            )
        except AIRequestFailed as err:
            return await _send_chatbot_ai_failure_reply(message, message_streamer, err, **kwargs)
        if isinstance(history, ModernContext):
            cleanup.push_async_callback(history.abort)
        mention_policy = select_mention_policy(modern=isinstance(history, ModernContext), mode=context.mode)
        speaker_names = (
            history.mention_speaker_names
            if isinstance(history, ModernContext) and mention_policy == MentionPolicy.MODERN_FIRST_NAMES
            else ()
        )
        if message_streamer:
            message_streamer.mention_policy = mention_policy
            message_streamer.speaker_names = speaker_names
        # Whatever tool calls history already contains were replayed from a previous
        # answer and must not be stored a second time.
        previous_history = list(history.message_history)
        if explicit_debug_mode:
            await _reply_debug_history(message, history)

        service_tier = await resolve_chat_service_tier(
            AIModelPurpose.chatbot,
            chat_tid=message.chat.id,
            redis=services.redis,
        )
        on_tool_call = message_streamer.update_thinking_for_tool if message_streamer else None
        on_reasoning_stream = message_streamer.stream_reasoning if message_streamer else None
        try:
            result = await run_chatbot(
                ChatbotRunRequest(
                    context=context,
                    history=history,
                    model_plan=model_plan,
                    service_tier=service_tier,
                    thread_id=message.message_thread_id,
                    callbacks=ChatbotRunCallbacks(
                        on_text_stream=(message_streamer.stream if message_streamer else None),
                        on_tool_call=on_tool_call,
                        on_reasoning_stream=on_reasoning_stream,
                        on_retry=(message_streamer.update_retrying if message_streamer else None),
                    ),
                )
            )
        except AIRequestFailed as err:
            return await _send_chatbot_ai_failure_reply(message, message_streamer, err, **kwargs)

        # The agent normalizes history, so message offsets cannot identify new research.
        research_response = (
            retrieve_latest_research_response(
                result.message_history, skip_tool_call_ids=collect_tool_call_ids(previous_history)
            )
            if await is_enabled(
                "ai_chatbot_research_quote",
                chat_tid=message.chat.id,
                redis=services.redis,
            )
            else None
        )
        offer_help_mode = await should_offer_help_mode(
            message,
            mode,
            result.message_history,
            previous_message_count=len(previous_history),
            redis=services.redis,
        )
        doc, output_text = await build_chatbot_reply_presentation(
            connection.db_model.id,
            message.chat.id,
            model,
            result,
            result.new_messages
            if isinstance(history, ModernContext)
            else result.message_history[len(previous_history) :],
            redis=services.redis,
            modern=isinstance(history, ModernContext),
            mode=context.mode,
            explicit_debug_mode=explicit_debug_mode,
            strip_alien_html_tags=strip_alien_html_tags,
            speaker_names=speaker_names,
            footer=build_help_mode_tip() if offer_help_mode else None,
        )
        # A private AI session already carries its own reply keyboard, and a message can only
        # have one: there the tip is reachable from that keyboard instead.
        if offer_help_mode and not kwargs.get("reply_markup"):
            kwargs["reply_markup"] = build_help_mode_keyboard(message)

        if message_streamer:
            final_message = await message_streamer.send_final(doc, **kwargs)
        else:
            final_message = await send_ai_rich_message(message, doc, reply_markup=kwargs.get("reply_markup"))

        cached_message = MessageType(
            text=output_text,
            user_id=CONFIG.bot_id,
            message_id=final_message.message_id,
            created_at=final_message.date,
            username="Sophie",
            is_bot=True,
            message_thread_id=final_message.message_thread_id,
            handled_by_ai=True,
            eligible_for_proactive_ai=False,
            reply_to_message_id=message.message_id,
            reply_to_user_id=message.from_user.id if message.from_user else None,
            reply_to_username=(
                message.from_user.username or message.from_user.full_name if message.from_user else None
            ),
        )

        if isinstance(history, ModernContext):
            await history.finish_run(result.new_messages, final_message, cached_message=cached_message)
        else:
            async with services.redis.pipeline(transaction=True) as pipe:
                enqueue_cached_message(pipe, message.chat.id, cached_message)
                await pipe.execute()
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
