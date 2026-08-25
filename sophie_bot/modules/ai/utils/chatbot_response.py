from __future__ import annotations

from typing import Any, cast

from beanie import PydanticObjectId
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models import Model
from stfu_tg import BlockQuote, Doc, HList, Italic, KeyValue, Section
from stfu_tg.ai_md import ai_markdown_to_doc
from stfu_tg.doc import Element

from sophie_bot.modules.ai.utils.ai_agent_run import AIAgentResult
from sophie_bot.modules.ai.utils.ai_header import ai_credit_header, ai_table_header
from sophie_bot.modules.ai.utils.ai_quota import get_quota_info
from sophie_bot.modules.ai.utils.ai_usage_service import usage_input_tokens, usage_output_tokens
from sophie_bot.modules.ai.utils.mention_usernames import MentionIndex, apply_mention_usernames, resolve_mentions
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_

TELEGRAM_MESSAGE_SAFE_LIMIT = 3900

CHATBOT_TOOLS_TITLES: dict[str, Element] = {
    "write_memory": cast(Element, l_("Memory 💾")),
    "forget_memory": cast(Element, l_("Forget 🗑")),
    "sophie_help": cast(Element, l_("Help 📖")),
    "sophie_inspect": cast(Element, l_("Sources 🧭")),
    "tavily_search": cast(Element, l_("Search 🔍")),
    "kagi_search": cast(Element, l_("Search 🔍")),
    "tinyfish_search": cast(Element, l_("Search 🔍")),
    "get_notes": cast(Element, l_("Notes 🗒")),
    "get_note_content": cast(Element, l_("Note 🗒")),
    "research_topic": cast(Element, l_("Research 🔎")),
}


def retrieve_tools_titles(message_history: list[ModelRequest | ModelResponse]) -> list[Element]:
    tool_title_elements: list[Element] = []
    seen_tool_names: set[str] = set()

    for message in message_history:
        for part in message.parts:
            if not isinstance(part, (ToolCallPart, ToolReturnPart)):
                continue
            if part.tool_name in seen_tool_names or part.tool_name not in CHATBOT_TOOLS_TITLES:
                continue
            seen_tool_names.add(part.tool_name)
            tool_title_elements.append(CHATBOT_TOOLS_TITLES[part.tool_name])

    return tool_title_elements


async def build_chatbot_header(
    chat_iid: PydanticObjectId,
    model: Model,
    message_history: list[ModelRequest | ModelResponse],
) -> Element:
    """The header of a *finished* AI message.

    Only built once generation completed: the status names what the run actually did and the battery
    reports the quota left after it was charged. In-progress messages use `ai_progress_line`.
    """
    status_items = retrieve_tools_titles(message_history)
    # Nothing to report means nothing was used: name the model instead of leaving the cell empty.
    status: Element | str = HList(*status_items, divider=", ") if status_items else model.model_name

    battery: Element | str = ""
    if quota_info := await get_quota_info(chat_iid):
        percentage = (
            int((quota_info.remaining_credits / quota_info.total_credits) * 100) if quota_info.total_credits > 0 else 0
        )
        battery = ai_credit_header(percentage)

    return ai_table_header(status, battery)


def build_debug_doc(model: Model, result: AIAgentResult[Any]) -> Section:
    return Section(
        BlockQuote(
            Doc(
                KeyValue("Model", model.model_name),
                KeyValue("LLM Requests", result.usage.requests),
                KeyValue("Retries", result.retries if result.retries is not None else "-"),
                KeyValue("Request tokens", usage_input_tokens(result.usage) or 0),
                KeyValue("Response tokens", usage_output_tokens(result.usage) or 0),
                KeyValue("Total tokens", result.usage.total_tokens),
                KeyValue("Details", result.usage.details or "-"),
            ),
            expandable=True,
        ),
        title="Provider debug",
    )


def truncate_output(header: Element, output_text: str) -> str:
    length = len(output_text) + len(header.to_html())
    if length > 4000:
        return output_text[:4000] + "..."
    return output_text


def build_truncated_note() -> Doc:
    """Shown when the agent loop hit a usage limit and the answer stops mid-thought."""
    return Doc(Italic(_("⚠️ Cut short — the reply hit its step limit.")))


async def build_reply_doc(
    header: Element,
    output_text: str,
    model: Model | None,
    result: AIAgentResult[Any] | None,
    explicit_debug_mode: bool,
    chat_tid: int | None,
    mention_index: MentionIndex | None = None,
) -> Doc:
    # The single rendering chokepoint for both streamed drafts and the final message, so mention
    # resolution happens here — before Markdown is rendered, which keeps escaping STFU's job.
    resolved_text = (
        await apply_mention_usernames(output_text, chat_tid)
        if mention_index is None
        else resolve_mentions(output_text, mention_index)
    )
    doc = Doc(header, ai_markdown_to_doc(resolved_text))
    if explicit_debug_mode and model is not None and result is not None:
        doc += " "
        doc += build_debug_doc(model, result)
    return doc
