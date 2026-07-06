from __future__ import annotations

from typing import Any, cast

from beanie import PydanticObjectId
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models import Model
from stfu_tg import BlockQuote, Doc, HList, KeyValue, PreformattedHTML, Section
from stfu_tg.doc import Element

from sophie_bot.modules.ai.utils.ai_agent_run import AIAgentResult
from sophie_bot.modules.ai.utils.ai_header import ai_chatbot_header, ai_credit_header
from sophie_bot.modules.ai.utils.ai_models import AI_MODEL_TO_SHORT_NAME
from sophie_bot.modules.ai.utils.ai_quota import get_quota_info
from sophie_bot.modules.ai.utils.ai_usage_service import usage_input_tokens, usage_output_tokens
from sophie_bot.modules.ai.utils.markdown_to_html import ai_markdown_to_html
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.i18n import lazy_gettext as l_

TELEGRAM_MESSAGE_SAFE_LIMIT = 3900

CHATBOT_TOOLS_TITLES: dict[str, Element] = {
    "write_memory": cast(Element, l_("Memory 💾")),
    "forget_memory": cast(Element, l_("Forget 🗑")),
    "cmds_help": cast(Element, l_("Commands 📋")),
    "tavily_search": cast(Element, l_("Search 🔍")),
    "kagi_search": cast(Element, l_("Search 🔍")),
    "get_notes": cast(Element, l_("Notes 🗒")),
    "get_note_content": cast(Element, l_("Note 🗒")),
    "save_note": cast(Element, l_("Save note 🗒")),
    "delete_note": cast(Element, l_("Delete note 🗑")),
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
    additional_header_items: list[Element] | None = None,
    skip_battery: bool = False,
) -> Element:
    header_items = [
        *(additional_header_items or []),
        *retrieve_tools_titles(message_history),
        HList(divider=", "),
    ]
    if not skip_battery and (quota_info := await get_quota_info(chat_iid)):
        percentage = (
            int((quota_info.remaining_credits / quota_info.total_credits) * 100) if quota_info.total_credits > 0 else 0
        )
        header_items.append(ai_credit_header(percentage))
    return ai_chatbot_header(model, *header_items)


def build_debug_doc(model: Model, result: AIAgentResult[Any]) -> Section:
    return Section(
        BlockQuote(
            Doc(
                KeyValue("Model", AI_MODEL_TO_SHORT_NAME.get(model.model_name, model.model_name)),
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


async def build_reply_doc(
    header: Element,
    output_text: str,
    model: Model | None,
    result: AIAgentResult[Any] | None,
    explicit_debug_mode: bool,
    chat_tid: int | None,
) -> Doc:
    if await is_enabled("ai_chatbot_rich_markdown", chat_tid=chat_tid):
        from stfu_tg.ai_md import ai_markdown_to_doc

        reply_body: Element = ai_markdown_to_doc(output_text)
    else:
        reply_body = PreformattedHTML(ai_markdown_to_html(output_text, extract_headings=True))
        if await is_enabled("ai_chatbot_blockquote", chat_tid=chat_tid):
            reply_body = BlockQuote(reply_body)

    doc = Doc(header, reply_body)
    if explicit_debug_mode and model is not None and result is not None:
        doc += " "
        doc += build_debug_doc(model, result)
    return doc
