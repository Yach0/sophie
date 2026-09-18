from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from beanie import PydanticObjectId
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart
from pydantic_ai.models import Model
from redis.asyncio import Redis
from stfu_tg import BlockQuote, Doc, Italic, KeyValue, Section
from stfu_tg.ai_md import ai_markdown_to_doc
from stfu_tg.doc import Element

from sophie_bot.modules.ai.utils.ai_agent_run import AIAgentResult
from sophie_bot.modules.ai.utils.ai_header import (
    AIHeaderStyle,
    ai_credit_header,
    build_ai_header,
    build_ai_message_doc,
)
from sophie_bot.modules.ai.utils.ai_quota import get_quota_info
from sophie_bot.modules.ai.utils.ai_usage_service import usage_input_tokens, usage_output_tokens
from sophie_bot.modules.ai.utils.mention_usernames import MentionIndex, apply_mention_usernames, resolve_mentions
from sophie_bot.utils.feature_flags import is_enabled
from sophie_bot.utils.i18n import gettext as _

TELEGRAM_MESSAGE_SAFE_LIMIT = 3900


def _tool_label(tool_name: str) -> str | None:
    match tool_name:
        case "kagi_search" | "tinyfish_search" | "tavily_search" | "web_search":
            return _("🔍 Internet Search")
        case "get_notes" | "get_note_content":
            return _("📝 Notes")
        case "write_memory" | "forget_memory":
            return _("🧠 Memory")
        case "research_topic":
            return _("🔬 Research")
        case "sophie_help":
            return _("📖 Help")
        case "sophie_inspect":
            return _("🔧 Source Inspection")
        case _:
            return None


def used_tool_labels(message_history: Sequence[ModelRequest | ModelResponse]) -> tuple[str, ...]:
    used_labels: set[str] = set()
    labels: list[str] = []
    for message in message_history:
        for part in message.parts:
            if not isinstance(part, ToolCallPart):
                continue
            label = _tool_label(part.tool_name)
            if label is None or label in used_labels:
                continue
            used_labels.add(label)
            labels.append(label)
    return tuple(labels)


def model_display_name(model: Model) -> str:
    model_name = model.model_name.rsplit("/", 1)[-1]
    words = model_name.replace("_", "-").split("-")
    return " ".join(word.upper() if word.casefold() in {"ai", "gpt"} else word.capitalize() for word in words)


async def build_chatbot_header(
    chat_iid: PydanticObjectId,
    style: AIHeaderStyle = "simple",
    model_label: str | None = None,
    *,
    redis: Redis,
) -> Element | str | None:
    battery: Element | str = ""
    if quota_info := await get_quota_info(chat_iid, redis=redis):
        percentage = (
            int((quota_info.remaining_credits / quota_info.total_credits) * 100) if quota_info.total_credits > 0 else 0
        )
        battery = ai_credit_header(percentage, model_label)

    return build_ai_header(style, battery)


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


def truncate_output(header: Element | str | None, output_text: str) -> str:
    header_html = header.to_html() if isinstance(header, Element) else str(header or "")
    length = len(output_text) + len(header_html)
    if length > 4000:
        return output_text[:4000] + "..."
    return output_text


def build_truncated_note() -> Doc:
    """Shown when the agent loop hit a usage limit and the answer stops mid-thought."""
    return Doc(Italic(_("⚠️ Cut short — the reply hit its step limit.")))


async def build_reply_doc(
    header: Element | str | None,
    output_text: str,
    model: Model | None,
    result: AIAgentResult[Any] | None,
    explicit_debug_mode: bool,
    chat_tid: int | None,
    mention_index: MentionIndex | None = None,
    *,
    redis: Redis,
    tool_labels: Sequence[str] = (),
    strip_alien_html_tags: bool | None = None,
) -> Doc:
    # The single rendering chokepoint for both streamed drafts and the final message, so mention
    # resolution happens here — before Markdown is rendered, which keeps escaping STFU's job.
    resolved_text = (
        await apply_mention_usernames(
            output_text,
            chat_tid,
            redis=redis,
        )
        if mention_index is None
        else resolve_mentions(output_text, mention_index)
    )
    if strip_alien_html_tags is None:
        strip_alien_html_tags = await is_enabled(
            "ai_chatbot_strip_alien_html_tags",
            chat_tid=chat_tid,
            redis=redis,
        )
    doc = build_ai_message_doc(
        header,
        ai_markdown_to_doc(resolved_text, strip_alien_html_tags=strip_alien_html_tags),
        tool_labels=tool_labels,
    )
    print(doc.to_rich())
    if explicit_debug_mode and model is not None and result is not None:
        doc += " "
        doc += build_debug_doc(model, result)
    return doc
