from typing import Any, cast

from aiogram.enums import ChatType
from aiogram.types import Message
from beanie import PydanticObjectId
from pydantic_ai.common_tools.tavily import tavily_search_tool
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models import Model
from stfu_tg import BlockQuote, Doc, HList, KeyValue, PreformattedHTML, Section, Template, VList
from stfu_tg.doc import Element

from sophie_bot.config import CONFIG
from sophie_bot.db.models import AIChatSummaryModel, AIMemoryModel
from sophie_bot.metrics import track_ai_conversation, track_ai_usage
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.ai.agent_tools.cmds_help import CmdsHelpAgentTool
from sophie_bot.modules.ai.agent_tools.memory import ForgetMemoryAgentTool, MemoryAgentTool
from sophie_bot.modules.ai.agent_tools.notes import note_content_ai_tool, notes_list_ai_tool
from sophie_bot.modules.ai.utils.ai_get_provider import get_chat_default_model
from sophie_bot.modules.ai.utils.ai_header import ai_chatbot_header, ai_credit_header
from sophie_bot.modules.ai.utils.ai_models import AI_MODEL_TO_SHORT_NAME
from sophie_bot.modules.ai.utils.ai_quota import get_quota_info
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContenxt
from sophie_bot.modules.ai.utils.ai_usage_service import charge_ai_usage
from sophie_bot.modules.ai.utils.draft_stream import MessageDraftStreamer
from sophie_bot.modules.ai.utils.new_ai_chatbot import new_ai_generate, new_ai_generate_stream
from sophie_bot.modules.ai.utils.new_message_history import CHATBOT_CACHE_MESSAGE_LIMIT, NewAIMessageHistory
from sophie_bot.modules.help.utils.extract_info import HELP_MODULES
from sophie_bot.modules.notes.utils.semantic_search import semantic_search_notes
from sophie_bot.modules.notes.utils.unparse_legacy import legacy_markdown_to_html
from sophie_bot.utils.ai_features import AI_FEATURE_CHATBOT
from sophie_bot.utils.feature_flags import get_service_tier, is_enabled
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

CHATBOT_TOOLS: list[Any] = [
    MemoryAgentTool(),
    ForgetMemoryAgentTool(),
    CmdsHelpAgentTool(),
    notes_list_ai_tool(),
    note_content_ai_tool(),
]

if CONFIG.tavily_api_key:
    CHATBOT_TOOLS.append(tavily_search_tool(api_key=CONFIG.tavily_api_key))

CHATBOT_TOOLS_DICT: dict[str, Any] = {tool.name: tool for tool in CHATBOT_TOOLS}
CHATBOT_TOOLS_TITLES: dict[str, Any] = {
    "write_memory": l_("Memory updated 💾"),
    "forget_memory": l_("Memory forgotten 🗑"),
    "cmds_help": l_("Commands help 📋"),
    "tavily_search": l_("Internet Search 🔍"),
    "get_notes": l_("Scanned notes 🗒"),
    "get_note_content": l_("Read note 🗒"),
}
CHATBOT_TOOLS_SHORT_TITLES: dict[str, Any] = {
    "write_memory": l_("Memory 💾"),
    "forget_memory": l_("Forget 🗑"),
    "cmds_help": l_("Commands 📋"),
    "tavily_search": l_("Search 🔍"),
    "get_notes": l_("Notes 🗒"),
    "get_note_content": l_("Note 🗒"),
}


def retrieve_tools_titles(message_history: list[ModelRequest | ModelResponse], *, short: bool = False) -> list[Element]:
    # Flatten all parts from all messages
    all_parts = [part for message in message_history for part in message.parts]

    # Filter for tool call and return parts
    tool_parts = [part for part in all_parts if isinstance(part, (ToolCallPart, ToolReturnPart))]

    tool_titles = CHATBOT_TOOLS_SHORT_TITLES if short else CHATBOT_TOOLS_TITLES

    # Extract unique tool names that exist in our titles dictionary
    unique_tool_names = {part.tool_name for part in tool_parts if part.tool_name in tool_titles}

    # Map tool names to their corresponding titles
    return [cast(Element, tool_titles[name]) for name in unique_tool_names]


def _build_session_id(chat_iid: object, thread_id: int | None) -> str:
    if thread_id:
        return f"{chat_iid}:{thread_id}"
    return str(chat_iid)


async def _build_system_prompt(chat_iid: PydanticObjectId, chat_tid: int, user_text: str | None = None) -> Doc:
    memory_lines = await AIMemoryModel.get_lines(chat_iid)
    system_prompt = Doc(
        _("You can use Tavily to search for information. Include information sources as links."),
        _("You can also save important things to the memory."),
        _(
            "If the user asks anything regarding using Sophie bot, make sure to execute `cmds_help` tool to obtain a help context, do not search internet for bot information."
        ),
        Template(_("Available Sophie modules: {modules}"), modules=HList(*HELP_MODULES.keys())),
    )
    if await is_enabled("ai_system_prompt_summaries", chat_tid=chat_tid):
        summary_lines = await AIChatSummaryModel.get_recent_lines(chat_iid)
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
            system_prompt += Section(VList(*rendered_summaries), title=_("Recent chat summaries"))
    if user_text and await is_enabled("ai_notes_related_system_prompt", chat_tid=chat_tid):
        related_notes = await semantic_search_notes(chat_iid, user_text, limit=5)
        if related_notes:
            include_note_content = await is_enabled("ai_notes_related_system_prompt_full_content", chat_tid=chat_tid)
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
            system_prompt += Section(VList(*rendered_related_notes), title=section_title)
    if memory_lines:
        indexed_memory_lines = [f"{index + 1}. {line}" for index, line in enumerate(memory_lines)]
        system_prompt += Section(
            VList(*indexed_memory_lines), title=_("You have the following information in your memory")
        )
    return system_prompt


async def _prepare_history(message: Message, chat_iid: PydanticObjectId, user_text: str | None) -> NewAIMessageHistory:
    history = NewAIMessageHistory()
    system_prompt = await _build_system_prompt(chat_iid, message.chat.id, user_text)
    await history.initialize_chat_history(
        message.chat.id,
        additional_system_prompt=system_prompt.to_md(),
        cache_limit=CHATBOT_CACHE_MESSAGE_LIMIT,
    )
    await history.add_from_message(message, custom_text=user_text)
    return history


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
    return await get_chat_default_model(connection.db_model.iid)


async def _generate_chatbot_result(
    message: Message,
    connection: ChatConnection,
    history: NewAIMessageHistory,
    model: Model,
    explicit_debug_mode: bool,
    service_tier: str | None = None,
):
    allow_draft_streaming = message.chat.type == ChatType.PRIVATE and not explicit_debug_mode
    draft_streamer = MessageDraftStreamer(message=message, enabled=allow_draft_streaming)
    agent_kwargs = {"deps": SophieAIToolContenxt(connection=connection)}
    session_id = _build_session_id(connection.db_model.iid, message.message_thread_id)

    if allow_draft_streaming:
        return await new_ai_generate_stream(
            history,
            tools=CHATBOT_TOOLS,
            model=model,
            agent_kwargs=agent_kwargs,
            on_text_stream=draft_streamer.stream,
            user_tracking_id=connection.db_model.iid,
            session_id=session_id,
            service_tier=service_tier,
        )

    return await new_ai_generate(
        history,
        tools=CHATBOT_TOOLS,
        model=model,
        agent_kwargs=agent_kwargs,
        user_tracking_id=connection.db_model.iid,
        session_id=session_id,
        service_tier=service_tier,
    )


async def _build_chatbot_header(
    connection: ChatConnection, model: Model, message_history: list[ModelRequest | ModelResponse]
) -> Element:
    short_title_enabled = await is_enabled("ai_chatbot_short_title", chat_tid=connection.tid)
    quota_info = await get_quota_info(connection.db_model.iid)
    header_items = [*retrieve_tools_titles(message_history, short=short_title_enabled), HList(divider=", ")]
    if quota_info:
        percentage = (
            int((quota_info.remaining_credits / quota_info.total_credits) * 100) if quota_info.total_credits > 0 else 0
        )
        header_items.append(ai_credit_header(percentage, short=short_title_enabled))
    return await ai_chatbot_header(connection.tid, model, *header_items)


def _build_debug_doc(model: Model, result: Any) -> Section:
    return Section(
        BlockQuote(
            Doc(
                KeyValue("Model", AI_MODEL_TO_SHORT_NAME[model.model_name]),
                KeyValue("LLM Requests", result.usage.requests),
                KeyValue("Retries", result.retries),
                KeyValue("Request tokens", result.usage.request_tokens),
                KeyValue("Response tokens", result.usage.response_tokens),
                KeyValue("Total tokens", result.usage.total_tokens),
                KeyValue("Details", result.usage.details or "-"),
            ),
            expandable=True,
        ),
        title="Provider debug",
    )


def _truncate_output(header: Element, output_text: str) -> str:
    length = len(output_text) + len(header.to_html())
    if length > 4000:
        return output_text[:4000] + "..."
    return output_text


async def _build_reply_doc(
    header: Element,
    output_text: str,
    model: Model,
    result: Any,
    explicit_debug_mode: bool,
    chat_tid: int | None,
) -> Doc:
    reply_body = PreformattedHTML(legacy_markdown_to_html(output_text, extract_headings=True))
    if await is_enabled("ai_chatbot_blockquote", chat_tid=chat_tid):
        reply_body = BlockQuote(reply_body)

    doc = Doc(header, reply_body)
    if explicit_debug_mode:
        doc += " "
        doc += _build_debug_doc(model, result)
    return doc


async def ai_chatbot_reply(
    message: Message,
    connection: ChatConnection,
    user_text: str | None = None,
    debug_mode: bool = False,
    model: Model | None = None,
    **kwargs,
):
    """
    Sends a reply from AI based on user input and message history.
    """

    if not await is_enabled("ai_chatbot", chat_tid=message.chat.id):
        return

    if not connection.db_model:
        return

    # Track active AI conversation
    async with track_ai_conversation():
        history = await _prepare_history(message, connection.db_model.iid, user_text)
        explicit_debug_mode = _is_explicit_debug_mode(message, user_text, debug_mode)
        if explicit_debug_mode:
            await _reply_debug_history(message, history)

        model = await _resolve_model(connection, model)
        service_tier = await get_service_tier("ai_chatbot_service_tier", chat_tid=message.chat.id)
        result = await _generate_chatbot_result(message, connection, history, model, explicit_debug_mode, service_tier)

        # Track AI usage metrics
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
        return await message.reply(doc.to_html(), disable_web_page_preview=True, **kwargs)
