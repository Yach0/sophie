from __future__ import annotations

import datetime
from collections.abc import Mapping

import sentry_sdk
from aiogram.types import Message
from stfu_tg import Doc, HList, Section, Template, VList

from sophie_bot.db.models import AIChatSummaryModel, AIMemoryModel, ChatModel
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.modules.ai.utils.ai_mode import get_capabilities
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext
from sophie_bot.modules.ai.utils.chatbot_tool_history import load_chatbot_tool_history
from sophie_bot.modules.ai.utils.message_history import CHATBOT_CACHE_MESSAGE_LIMIT, ActivityCallback, AIMessageHistory
from sophie_bot.modules.notes.utils.semantic_search import semantic_search_notes
from sophie_bot.utils.feature_flags import FeatureType, get_value, is_enabled
from sophie_bot.utils.i18n import gettext as _


def _base_chatbot_instruction_doc(system_prompt: str, today: datetime.datetime) -> Doc:
    return Doc(
        system_prompt,
        _("Prefer to use tables when comparing items"),
        _("Use the conversation history only for context, but respond specifically to the latest prompt."),
        _("Today is ") + today.strftime("%d %B %Y, %H:%M") + f" ({today.tzname()})",
        _("You can use the web search tool to search for information. Include information sources as links."),
    )


async def _build_chatbot_runtime_context(context: SophieAIToolContext, mode: AIMode) -> Doc:
    capabilities = get_capabilities(mode)
    context_doc = Doc(
        _("You can also save important things to the memory.") if capabilities.memory else None,
        _(
            "If the user asks anything regarding using Sophie bot, make sure to execute the `sophie_help` tool to obtain a help context, do not search internet for bot information. Do not use it for questions that are not about Sophie."
        ),
        Template(
            _("Available Sophie modules: {modules}"),
            modules=HList(*context.services.modules.help_modules),
        ),
    )

    context_doc += _("You can use the research tool to research complicated topics instead of plain web search.")

    context_doc += _(
        "Earlier tool calls and their results are part of the conversation history. Reuse that information instead of calling the same tool with the same arguments again, unless the user asks for an update or the information may have changed."
    )

    chat_model = await ChatModel.get_by_tid(context.chat_tid)
    if chat_model and chat_model.first_name_or_title:
        context_doc += Template(
            _("This conversation is taking place in chat: {chat_name}"),
            chat_name=chat_model.first_name_or_title,
        )

    summary_lines = await AIChatSummaryModel.get_recent_lines(context.chat_iid)
    if summary_lines:
        # The message ID lets the provider correlate the summary back to the real chat.
        hide_message_ids = await is_enabled(
            "ai_summary_improved_privacy",
            chat_tid=context.chat_tid,
            redis=context.services.redis,
        )
        summary_template = (
            _("{title} | users: {users} | excerpt: {excerpt}")
            if hide_message_ids
            else _("{title} | first message #{message_id} | users: {users} | excerpt: {excerpt}")
        )
        rendered_summaries = [
            Template(
                summary_template,
                title=line.title,
                message_id=line.first_message_id,
                users=", ".join(line.usernames) if line.usernames else "-",
                excerpt=line.source_excerpt or "-",
            )
            for line in summary_lines
        ]
        context_doc += Section(VList(*rendered_summaries), title=_("Recent chat summaries"))

    if context.user_text:
        related_notes = await semantic_search_notes(
            context.chat_iid, context.user_text, limit=5, redis=context.services.redis
        )
        if related_notes:
            rendered_related_notes = [
                Template(
                    _("{notename} | title: {title} | content: {content}"),
                    notename=note.names[0],
                    title=note.description or "-",
                    content=note.text or "-",
                )
                for note in related_notes
            ]
            context_doc += Section(VList(*rendered_related_notes), title=_("Related chat notes with content."))

    if capabilities.memory and (memory_lines := await AIMemoryModel.get_lines(context.chat_iid)):
        indexed_memory_lines = [f"{index + 1}. {line}" for index, line in enumerate(memory_lines)]
        context_doc += Section(
            VList(*indexed_memory_lines), title=_("You have the following information in your memory")
        )

    return context_doc


# Sophie-help exists to give the "chat with Sophie for help" button its own assistant, so it gets
# its own system prompt instead of the general chatbot one.
_SYSTEM_PROMPT_FLAG_BY_MODE: Mapping[AIMode, FeatureType] = {AIMode.sophie_help: "ai_help_system_prompt"}


async def build_chatbot_instructions(context: SophieAIToolContext) -> str:
    with sentry_sdk.start_span(op="ai.context", name="Build chatbot instructions") as span:
        mode = context.mode
        prompt_flag = _SYSTEM_PROMPT_FLAG_BY_MODE.get(mode, "ai_chatbot_system_prompt")
        system_prompt = str(
            await get_value(
                prompt_flag,
                chat_tid=context.chat_tid,
                redis=context.services.redis,
            )
        )
        instruction_doc = _base_chatbot_instruction_doc(system_prompt, datetime.datetime.now(datetime.UTC))
        instruction_doc += await _build_chatbot_runtime_context(context, mode)
        span.set_data("ai.mode", mode.value)
        span.set_data("ai.has_user_text", bool(context.user_text))
        return instruction_doc.to_md()


async def prepare_chatbot_history(
    message: Message, context: SophieAIToolContext, *, on_activity: ActivityCallback | None = None
) -> AIMessageHistory:
    with sentry_sdk.start_span(op="ai.context", name="Prepare chatbot history") as span:
        history = AIMessageHistory(services=context.services)
        max_age_minutes = int(
            await get_value(
                "ai_chatbot_history_max_age_minutes",
                chat_tid=context.chat_tid,
                redis=context.services.redis,
            )
        )
        max_age = datetime.timedelta(minutes=max_age_minutes) if max_age_minutes > 0 else None

        tool_exchanges = await load_chatbot_tool_history(context.chat_tid, redis=context.services.redis)
        await history.add_from_cache(
            context.chat_tid,
            limit=CHATBOT_CACHE_MESSAGE_LIMIT,
            fold_background=True,
            max_age=max_age,
            tool_exchanges=tool_exchanges,
        )

        await history.add_from_message(message, custom_text=context.user_text, on_activity=on_activity)
        history.apply_context_block()
        span.set_data("ai.mode", context.mode.value)
        span.set_data("ai.history_messages", len(history.message_history))
        span.set_data("ai.tool_history_runs", len(tool_exchanges))
        span.set_data("ai.max_age_enabled", max_age is not None)
        return history
