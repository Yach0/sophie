from __future__ import annotations

import datetime
from typing import Mapping

from aiogram.types import Message
from stfu_tg import Doc, HList, Section, Template, VList

from sophie_bot.db.models.ai.ai_mode import AIMode

from sophie_bot.db.models import AIChatSummaryModel, AIMemoryModel, ChatModel
from sophie_bot.modules.ai.utils.ai_mode import get_capabilities
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext
from sophie_bot.modules.ai.utils.message_history import CHATBOT_CACHE_MESSAGE_LIMIT, AIMessageHistory
from sophie_bot.modules.help.utils.extract_info import HELP_MODULES
from sophie_bot.modules.notes.utils.semantic_search import semantic_search_notes
from sophie_bot.utils.feature_flags import FeatureType, get_value, is_enabled
from sophie_bot.utils.i18n import gettext as _


def _base_chatbot_instruction_doc(system_prompt: str, today: datetime.datetime, *, tables_enabled: bool = False) -> Doc:
    # Tables are the one element that does not survive the plain-HTML rendering path.
    markdown_instruction = _("Prefer to use tables when comparing items") if tables_enabled else _("Do not use tables.")
    return Doc(
        system_prompt,
        markdown_instruction,
        _("Use the conversation history only for context, but respond specifically to the latest prompt."),
        _("Today is ") + today.strftime("%d %B %Y, %H:%M"),
        _("You can use the web search tool to search for information. Include information sources as links."),
    )


async def _build_chatbot_runtime_context(context: SophieAIToolContext, mode: AIMode) -> Doc:
    capabilities = get_capabilities(mode)
    chat_name_enabled = await is_enabled("ai_chatbot_chat_name", chat_tid=context.chat_tid)
    context_doc = Doc(
        _("You can also save important things to the memory.") if capabilities.memory else None,
        _(
            "If the user asks anything regarding using Sophie bot, make sure to execute the `sophie_help` tool to obtain a help context, do not search internet for bot information. Do not use it for questions that are not about Sophie."
        ),
        Template(_("Available Sophie modules: {modules}"), modules=HList(*HELP_MODULES.keys())),
    )

    if await is_enabled("ai_research", chat_tid=context.chat_tid):
        context_doc += _("You can use the research tool to research complicated topics instead of plain web search.")

    if chat_name_enabled:
        chat_model = await ChatModel.get_by_tid(context.chat_tid)
        if chat_model and chat_model.first_name_or_title:
            context_doc += Template(
                _("This conversation is taking place in chat: {chat_name}"),
                chat_name=chat_model.first_name_or_title,
            )

    if await is_enabled("ai_system_prompt_summaries", chat_tid=context.chat_tid):
        summary_lines = await AIChatSummaryModel.get_recent_lines(context.chat_iid)
        if summary_lines:
            rendered_summaries = [
                Template(
                    _("{title} | first message #{message_id} | users: {users} | excerpt: {excerpt}"),
                    title=line.title,
                    message_id=line.first_message_id,
                    users=", ".join(line.usernames) if line.usernames else "-",
                    excerpt=line.source_excerpt or "-",
                )
                for line in summary_lines
            ]
            context_doc += Section(VList(*rendered_summaries), title=_("Recent chat summaries"))

    if context.user_text and await is_enabled("ai_notes_related_system_prompt", chat_tid=context.chat_tid):
        related_notes = await semantic_search_notes(context.chat_iid, context.user_text, limit=5)
        if related_notes:
            include_note_content = await is_enabled(
                "ai_notes_related_system_prompt_full_content", chat_tid=context.chat_tid
            )
            if include_note_content:
                rendered_related_notes = [
                    Template(
                        _("{notename} | title: {title} | content: {content}"),
                        notename=note.names[0],
                        title=note.description or "-",
                        content=note.text or "-",
                    )
                    for note in related_notes
                ]
                section_title = _("Related chat notes with content.")
            else:
                rendered_related_notes = [
                    Template(
                        _("{notename} | title: {title}"),
                        notename=note.names[0],
                        title=note.description or "-",
                    )
                    for note in related_notes
                ]
                section_title = _(
                    "Related chat notes. Use get_note_content with the notename when note details may help."
                )
            context_doc += Section(VList(*rendered_related_notes), title=section_title)

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
    mode = context.mode
    prompt_flag = _SYSTEM_PROMPT_FLAG_BY_MODE.get(mode, "ai_chatbot_system_prompt")
    system_prompt = str(await get_value(prompt_flag, chat_tid=context.chat_tid))
    tables_enabled = await is_enabled("ai_chatbot_tables", chat_tid=context.chat_tid)
    instruction_doc = _base_chatbot_instruction_doc(
        system_prompt, datetime.datetime.now(), tables_enabled=tables_enabled
    )
    instruction_doc += await _build_chatbot_runtime_context(context, mode)
    return instruction_doc.to_md()


async def prepare_chatbot_history(message: Message, context: SophieAIToolContext) -> AIMessageHistory:
    history = AIMessageHistory()
    max_age_minutes = int(await get_value("ai_chatbot_history_max_age_minutes", chat_tid=context.chat_tid))
    max_age = datetime.timedelta(minutes=max_age_minutes) if max_age_minutes > 0 else None
    await history.add_from_cache(
        context.chat_tid, limit=CHATBOT_CACHE_MESSAGE_LIMIT, fold_background=True, max_age=max_age
    )
    await history.add_from_message(message, custom_text=context.user_text)
    history.apply_context_block()
    return history
