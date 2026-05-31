from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from random import choice
from typing import Any, cast

from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
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
from sophie_bot.db.models import AIChatSummaryModel, AIMemoryModel, ChatModel
from sophie_bot.metrics import track_ai_conversation, track_ai_usage
from sophie_bot.middlewares.connections import ChatConnection
from sophie_bot.modules.ai.agent_tools.cmds_help import CmdsHelpAgentTool
from sophie_bot.modules.ai.agent_tools.kagi_search import kagi_search_ai_tool
from sophie_bot.modules.ai.agent_tools.memory import ForgetMemoryAgentTool, MemoryAgentTool
from sophie_bot.modules.ai.agent_tools.notes import (
    delete_note_ai_tool,
    note_content_ai_tool,
    notes_list_ai_tool,
    save_note_ai_tool,
)
from sophie_bot.modules.ai.utils.ai_get_provider import get_chat_default_model
from sophie_bot.modules.ai.utils.ai_header import ai_chatbot_header, ai_credit_header
from sophie_bot.modules.ai.utils.ai_models import AI_MODEL_TO_SHORT_NAME
from sophie_bot.modules.ai.utils.ai_quota import get_quota_info
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContenxt
from sophie_bot.modules.ai.utils.ai_usage_service import charge_ai_usage
from sophie_bot.modules.ai.utils.draft_stream import DEFAULT_DRAFT_MAX_TEXT_LENGTH, MessageDraftStreamer
from sophie_bot.modules.ai.utils.markdown_to_html import ai_markdown_to_html
from sophie_bot.modules.ai.utils.new_ai_chatbot import new_ai_generate, new_ai_generate_stream
from sophie_bot.modules.ai.utils.new_message_history import CHATBOT_CACHE_MESSAGE_LIMIT, NewAIMessageHistory
from sophie_bot.modules.help.utils.extract_info import HELP_MODULES
from sophie_bot.modules.notes.utils.semantic_search import semantic_search_notes
from sophie_bot.utils.ai_features import AI_FEATURE_CHATBOT
from sophie_bot.utils.feature_flags import get_service_tier, get_value, is_enabled
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

BASE_CHATBOT_TOOLS: list[Any] = [
    MemoryAgentTool(),
    ForgetMemoryAgentTool(),
    CmdsHelpAgentTool(),
    notes_list_ai_tool(),
    note_content_ai_tool(),
]

OPTIONAL_CHATBOT_TOOLS: list[Any] = []

CHATBOT_TOOLS: list[Any] = [*BASE_CHATBOT_TOOLS, *OPTIONAL_CHATBOT_TOOLS]
CHATBOT_TOOLS_TITLES: dict[str, Any] = {
    "write_memory": l_("Memory updated 💾"),
    "forget_memory": l_("Memory forgotten 🗑"),
    "cmds_help": l_("Commands help 📋"),
    "tavily_search": l_("Internet Search 🔍"),
    "kagi_search": l_("Internet Search 🔍"),
    "get_notes": l_("Scanned notes 🗒"),
    "get_note_content": l_("Read note 🗒"),
    "save_note": l_("Saved note 🗒"),
    "delete_note": l_("Deleted note 🗑"),
}
CHATBOT_TOOLS_SHORT_TITLES: dict[str, Any] = {
    "write_memory": l_("Memory 💾"),
    "forget_memory": l_("Forget 🗑"),
    "cmds_help": l_("Commands 📋"),
    "tavily_search": l_("Search 🔍"),
    "kagi_search": l_("Search 🔍"),
    "get_notes": l_("Notes 🗒"),
    "get_note_content": l_("Note 🗒"),
    "save_note": l_("Save note 🗒"),
    "delete_note": l_("Delete note 🗑"),
}

TextStreamCallback = Callable[[str], Awaitable[None]]
ToolCallCallback = Callable[[str], Awaitable[None]]

_DEFAULT_THINKING_CUSTOM_EMOJI_ID = "5258317508326214175"
_THINKING_CUSTOM_EMOJI_IDS = (
    "5256211041615889001",
    "5257997017866585370",
    _DEFAULT_THINKING_CUSTOM_EMOJI_ID,
    "5258417125797678733",
    "5258464881539040501",
    "5258494813166129825",
    "5258331634473650007",
)
_DEFAULT_STREAM_BACKOFF_SECONDS = 1.5
_MIN_STREAM_BACKOFF_SECONDS = 0.5
_MAX_STREAM_TEXT_LENGTH = DEFAULT_DRAFT_MAX_TEXT_LENGTH - 128
_TOOL_THINKING_TEXTS: dict[str, tuple[str, ...]] = {
    "tavily_search": (
        _("Searching the web..."),
        _("Looking it up online..."),
        _("Browsing the internet..."),
    ),
    "kagi_search": (
        _("Searching the web..."),
        _("Looking it up online..."),
        _("Browsing the internet..."),
    ),
    "write_memory": (
        _("Updating memory..."),
        _("Saving to memory..."),
    ),
    "forget_memory": (
        _("Removing from memory..."),
        _("Forgetting..."),
    ),
    "cmds_help": (
        _("Checking command help..."),
        _("Looking up commands..."),
    ),
    "get_notes": (
        _("Scanning notes..."),
        _("Looking through notes..."),
    ),
    "get_note_content": (
        _("Reading note..."),
        _("Fetching note content..."),
    ),
    "save_note": (
        _("Saving note..."),
        _("Writing to notes..."),
    ),
    "delete_note": (
        _("Deleting note..."),
        _("Removing note..."),
    ),
}


def _thinking_custom_emoji(emoji_id: str | None = None) -> Element:
    return PreformattedHTML(f'<tg-emoji emoji-id="{emoji_id or _DEFAULT_THINKING_CUSTOM_EMOJI_ID}">💭</tg-emoji>')


def _random_thinking_text() -> str:
    return choice(
        (
            _("Thinking..."),
            _("Working on it..."),
            _("Let me think..."),
            _("Generating response..."),
            _("Preparing an answer..."),
            _("Reading the context..."),
            _("Checking the details..."),
            _("Looking into it..."),
        )
    )


def _thinking_header_element(emoji_id: str | None = None) -> Element:
    return HList(_thinking_custom_emoji(emoji_id), _random_thinking_text(), divider=" ")


def _coerce_stream_backoff_seconds(value: object) -> float:
    if isinstance(value, (int, float)):
        return max(float(value), _MIN_STREAM_BACKOFF_SECONDS)

    try:
        return max(float(str(value)), _MIN_STREAM_BACKOFF_SECONDS)
    except ValueError:
        return _DEFAULT_STREAM_BACKOFF_SECONDS


def _truncate_stream_text(output_text: str) -> str:
    if len(output_text) <= _MAX_STREAM_TEXT_LENGTH:
        return output_text
    return f"{output_text[: _MAX_STREAM_TEXT_LENGTH - 3]}..."


@dataclass(slots=True)
class ChatbotMessageStreamer:
    source_message: Message
    header: Element
    enabled: bool
    throttle_seconds: float
    response_message: Message | None = None
    tool_thinking_texts: dict[str, tuple[str, ...]] | None = None
    connection: ChatConnection | None = None
    model: Model | None = None
    emoji_id: str | None = None
    last_sent_text: str = ""
    last_sent_at: float = 0.0

    async def send_thinking_message(self) -> None:
        self.response_message = await self.source_message.reply(
            Doc(self.header).to_html(),
            disable_web_page_preview=True,
        )

    async def stream(self, text: str) -> None:
        if not self.enabled or not text.strip():
            return

        monotonic_time = time.monotonic()
        if monotonic_time - self.last_sent_at < self.throttle_seconds:
            return

        draft_text = _truncate_stream_text(text)
        if draft_text == self.last_sent_text:
            return

        doc = await _build_reply_doc(
            self.header,
            draft_text,
            model=None,
            result=None,
            explicit_debug_mode=False,
            chat_tid=self.source_message.chat.id,
        )
        if not await self._edit_or_send(doc):
            self.enabled = False
            return

        self.last_sent_text = draft_text
        self.last_sent_at = monotonic_time

    async def update_thinking_for_tool(self, tool_name: str) -> None:
        if not self.tool_thinking_texts or not self.connection or not self.model:
            return

        texts = self.tool_thinking_texts.get(tool_name)
        if not texts:
            return

        thinking_text = choice(texts)
        thinking_element = HList(_thinking_custom_emoji(self.emoji_id), thinking_text, divider=" ")
        self.header = await _build_chatbot_header(
            self.connection,
            self.model,
            [],
            additional_header_items=[thinking_element],
            skip_battery=True,
        )
        if self.response_message is None:
            return

        try:
            await self.response_message.edit_text(
                Doc(self.header).to_html(),
                disable_web_page_preview=True,
            )
        except TelegramAPIError:
            pass

    async def send_final(self, doc: Doc, **reply_kwargs: Any) -> Message:
        if self.response_message is None:
            return await self.source_message.reply(
                doc.to_html(),
                disable_web_page_preview=True,
                **reply_kwargs,
            )

        try:
            edited_message = await self.response_message.edit_text(
                doc.to_html(),
                disable_web_page_preview=True,
                **reply_kwargs,
            )
            if isinstance(edited_message, Message):
                return edited_message
            return self.response_message
        except TelegramAPIError:
            return await self.source_message.reply(
                doc.to_html(),
                disable_web_page_preview=True,
                **reply_kwargs,
            )

    async def _edit_or_send(self, doc: Doc) -> bool:
        try:
            if self.response_message is None:
                self.response_message = await self.source_message.reply(
                    doc.to_html(),
                    disable_web_page_preview=True,
                )
                return True

            await self.response_message.edit_text(doc.to_html(), disable_web_page_preview=True)
            return True
        except TelegramAPIError:
            return False


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


async def _get_search_tool(chat_tid: int) -> Any | None:
    search_provider = str(await get_value("ai_search_provider", chat_tid=chat_tid)).lower()
    if search_provider == "tavily":
        return tavily_search_tool(api_key=CONFIG.tavily_api_key) if CONFIG.tavily_api_key else None
    return kagi_search_ai_tool() if CONFIG.kagi_api_key else None


async def _get_chatbot_tools(chat_tid: int) -> list[Any]:
    memories_to_notes = await is_enabled("ai_memories_to_notes", chat_tid=chat_tid)
    tools = [
        tool for tool in CHATBOT_TOOLS if not memories_to_notes or tool.name not in {"write_memory", "forget_memory"}
    ]
    if search_tool := await _get_search_tool(chat_tid):
        tools.append(search_tool)
    if memories_to_notes or await is_enabled("ai_agent_save_notes", chat_tid=chat_tid):
        tools.append(save_note_ai_tool())
    if await is_enabled("ai_delete_notes", chat_tid=chat_tid):
        tools.append(delete_note_ai_tool())
    return tools


async def _build_system_prompt(chat_iid: PydanticObjectId, chat_tid: int, user_text: str | None = None) -> Doc:
    memories_to_notes = await is_enabled("ai_memories_to_notes", chat_tid=chat_tid)
    chat_name_enabled = await is_enabled("ai_chatbot_chat_name", chat_tid=chat_tid)
    system_prompt = Doc(
        _("You can use the web search tool to search for information. Include information sources as links."),
        _("You can also save important things to chat notes.")
        if memories_to_notes
        else _("You can also save important things to the memory."),
        _(
            "If the user asks anything regarding using Sophie bot, make sure to execute `cmds_help` tool to obtain a help context, do not search internet for bot information."
        ),
        Template(_("Available Sophie modules: {modules}"), modules=HList(*HELP_MODULES.keys())),
    )
    if chat_name_enabled:
        chat_model = await ChatModel.get_by_tid(chat_tid)
        if chat_model and chat_model.first_name_or_title:
            system_prompt += Template(
                _("This conversation is taking place in chat: {chat_name}"),
                chat_name=chat_model.first_name_or_title,
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
    if not memories_to_notes and (memory_lines := await AIMemoryModel.get_lines(chat_iid)):
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
    return await get_chat_default_model(connection.db_model.iid, chat_tid=connection.db_model.tid)


async def _generate_chatbot_result(
    message: Message,
    connection: ChatConnection,
    history: NewAIMessageHistory,
    model: Model,
    explicit_debug_mode: bool,
    service_tier: str | None = None,
    on_text_stream: TextStreamCallback | None = None,
    on_tool_call: ToolCallCallback | None = None,
) -> Any:
    allow_draft_streaming = message.chat.type == ChatType.PRIVATE and not explicit_debug_mode and on_text_stream is None
    draft_streamer = MessageDraftStreamer(message=message, enabled=allow_draft_streaming)
    agent_kwargs = {"deps": SophieAIToolContenxt(connection=connection)}
    session_id = _build_session_id(connection.db_model.iid, message.message_thread_id)

    tools = await _get_chatbot_tools(connection.tid)

    if on_text_stream is not None or allow_draft_streaming:
        return await new_ai_generate_stream(
            history,
            tools=tools,
            model=model,
            agent_kwargs=agent_kwargs,
            on_text_stream=on_text_stream or draft_streamer.stream,
            user_tracking_id=connection.db_model.iid,
            session_id=session_id,
            service_tier=service_tier,
            on_tool_call=on_tool_call,
        )

    return await new_ai_generate(
        history,
        tools=tools,
        model=model,
        agent_kwargs=agent_kwargs,
        user_tracking_id=connection.db_model.iid,
        session_id=session_id,
        service_tier=service_tier,
    )


async def _build_chatbot_header(
    connection: ChatConnection,
    model: Model,
    message_history: list[ModelRequest | ModelResponse],
    additional_header_items: list[Element] | None = None,
    skip_battery: bool = False,
) -> Element:
    header_items = [
        *(additional_header_items or []),
        *retrieve_tools_titles(message_history, short=True),
        HList(divider=", "),
    ]
    if not skip_battery and (quota_info := await get_quota_info(connection.db_model.iid)):
        percentage = (
            int((quota_info.remaining_credits / quota_info.total_credits) * 100) if quota_info.total_credits > 0 else 0
        )
        header_items.append(ai_credit_header(percentage))
    return ai_chatbot_header(model, *header_items)


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


async def _build_message_streamer(
    message: Message,
    connection: ChatConnection,
    model: Model,
    explicit_debug_mode: bool,
) -> ChatbotMessageStreamer | None:
    if explicit_debug_mode or message.chat.type == ChatType.PRIVATE:
        return None

    thinking_enabled = await is_enabled("ai_chatbot_thinking_message", chat_tid=message.chat.id)
    streaming_enabled = await is_enabled("ai_chatbot_streaming", chat_tid=message.chat.id)
    if not thinking_enabled and not streaming_enabled:
        return None

    header_items = None
    emoji_id = None
    if thinking_enabled:
        if await is_enabled("ai_chatbot_random_emoji", chat_tid=message.chat.id):
            emoji_id = choice(_THINKING_CUSTOM_EMOJI_IDS)
        header_items = [_thinking_header_element(emoji_id=emoji_id)]
    header = await _build_chatbot_header(
        connection,
        model,
        [],
        additional_header_items=header_items,
        skip_battery=thinking_enabled,
    )
    backoff_seconds = _coerce_stream_backoff_seconds(
        await get_value("ai_chatbot_streaming_backoff_seconds", chat_tid=message.chat.id)
    )
    streamer = ChatbotMessageStreamer(
        source_message=message,
        header=header,
        enabled=streaming_enabled,
        throttle_seconds=backoff_seconds,
        tool_thinking_texts=_TOOL_THINKING_TEXTS
        if thinking_enabled and await is_enabled("ai_chatbot_tool_thinking", chat_tid=message.chat.id)
        else None,
        connection=connection,
        model=model,
        emoji_id=emoji_id,
    )
    if thinking_enabled:
        await streamer.send_thinking_message()
    return streamer


async def _build_reply_doc(
    header: Element,
    output_text: str,
    model: Model | None,
    result: Any,
    explicit_debug_mode: bool,
    chat_tid: int | None,
) -> Doc:
    reply_body = PreformattedHTML(ai_markdown_to_html(output_text, extract_headings=True))
    if await is_enabled("ai_chatbot_blockquote", chat_tid=chat_tid):
        reply_body = BlockQuote(reply_body)

    doc = Doc(header, reply_body)
    if explicit_debug_mode and model is not None:
        doc += " "
        doc += _build_debug_doc(model, result)
    return doc


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
        return

    if not connection.db_model:
        return

    # Track active AI conversation
    async with track_ai_conversation():
        explicit_debug_mode = _is_explicit_debug_mode(message, user_text, debug_mode)
        model = await _resolve_model(connection, model)
        message_streamer = await _build_message_streamer(message, connection, model, explicit_debug_mode)
        history = await _prepare_history(message, connection.db_model.iid, user_text)
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
        )

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
        if message_streamer:
            return await message_streamer.send_final(doc, **kwargs)
        return await message.reply(doc.to_html(), disable_web_page_preview=True, **kwargs)
