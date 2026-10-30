from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from beanie import PydanticObjectId
from beanie.operators import In
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart
from pydantic_ai.models import Model
from redis.asyncio import Redis
from stfu_tg import BlockQuote, Doc, KeyValue, Section
from stfu_tg.doc import Element

from sophie_bot.db.models import ChatModel
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.db.models.chat import ChatType
from sophie_bot.modules.ai.utils.ai_header import ai_credit_header
from sophie_bot.modules.ai.utils.ai_quota import get_quota_info
from sophie_bot.modules.ai.utils.ai_response import AIResponseDoc, render_ai_response
from sophie_bot.modules.ai.utils.ai_run import AIAgentResult
from sophie_bot.modules.ai.utils.ai_tool import AI_TOOLS_BY_NAME, AITool
from sophie_bot.modules.ai.utils.mention_usernames import (
    MentionCandidate,
    MentionIndex,
    MentionPolicy,
    build_mention_index,
    resolve_mention_index,
    resolve_mentions,
)
from sophie_bot.utils.feature_flags import get_value, is_enabled

TELEGRAM_MESSAGE_SAFE_LIMIT = 3900


def select_mention_policy(*, modern: bool, mode: AIMode) -> MentionPolicy:
    if not modern:
        return MentionPolicy.LEGACY_DISPLAY_NAMES
    return MentionPolicy.MODERN_FIRST_NAMES if mode == AIMode.entertainment else MentionPolicy.OPAQUE


async def resolve_reply_mention_index(
    chat_tid: int | None,
    *,
    redis: Redis,
    mention_policy: MentionPolicy,
    speaker_names: Sequence[tuple[int, str]] = (),
) -> MentionIndex | None:
    if mention_policy == MentionPolicy.OPAQUE or chat_tid is None:
        return None
    if mention_policy == MentionPolicy.LEGACY_DISPLAY_NAMES:
        return await resolve_mention_index(chat_tid, redis=redis) or build_mention_index(())
    user_tids = tuple(dict.fromkeys(user_tid for user_tid, _name in speaker_names))
    users = (
        await ChatModel.find(In(ChatModel.tid, user_tids), ChatModel.type == ChatType.private).to_list()
        if user_tids
        else []
    )
    usernames = {user.tid: user.username or "" for user in users}
    # Select only first-name labels from the session, never current profile names or surnames.
    # Missing/handleless actors still occupy their supplied names and block misattribution.
    return build_mention_index(
        (
            MentionCandidate(display_names=(first_name,), username=usernames.get(user_tid, ""))
            for user_tid, first_name in speaker_names
        ),
        policy=mention_policy,
    )


def used_tool_labels(message_history: Sequence[ModelRequest | ModelResponse]) -> tuple[AITool, ...]:
    used_labels: set[tuple[str, str]] = set()
    tools: list[AITool] = []
    for message in message_history:
        for part in message.parts:
            if not isinstance(part, ToolCallPart):
                continue
            tool = AI_TOOLS_BY_NAME.get(part.tool_name)
            if tool is None or not tool.display_in_ai_header:
                continue
            category = (tool.emoji, tool.display_label())
            if category in used_labels:
                continue
            used_labels.add(category)
            tools.append(tool)
    search_emoji = AI_TOOLS_BY_NAME["web_search"].emoji
    tools.sort(key=lambda tool: tool.emoji != search_emoji)
    return tuple(tools)


def model_display_name(model: Model) -> str:
    model_name = model.model_name.rsplit("/", 1)[-1]
    words = model_name.replace("_", "-").split("-")
    return " ".join(word.upper() if word.casefold() in {"ai", "gpt"} else word.capitalize() for word in words)


async def build_chatbot_header(
    chat_iid: PydanticObjectId,
    model_label: str | None = None,
    *,
    redis: Redis,
) -> Element | str:
    battery: Element | str = "🔋"
    if quota_info := await get_quota_info(chat_iid, redis=redis):
        percentage = (
            int((quota_info.remaining_credits / quota_info.total_credits) * 100) if quota_info.total_credits > 0 else 0
        )
        battery = ai_credit_header(percentage, model_label)

    return battery


def build_debug_doc(model: Model, result: AIAgentResult[Any]) -> Section:
    return Section(
        BlockQuote(
            Doc(
                KeyValue("Model", model.model_name),
                KeyValue("LLM Requests", result.usage.requests),
                KeyValue("Retries", result.retries if result.retries is not None else "-"),
                KeyValue("Request tokens", result.usage.input_tokens),
                KeyValue("Response tokens", result.usage.output_tokens),
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
    mention_policy: MentionPolicy = MentionPolicy.LEGACY_DISPLAY_NAMES,
    speaker_names: Sequence[tuple[int, str]] = (),
    tool_labels: Sequence[AITool] = (),
    strip_alien_html_tags: bool | None = None,
) -> AIResponseDoc:
    # Modern alias-only modes must not back-trace names, even with a preloaded index.
    # Allowed mentions are resolved before Markdown so escaping remains STFU's job.
    resolved_text = output_text
    if mention_policy != MentionPolicy.OPAQUE and "@" in output_text:
        if mention_index is None:
            mention_index = await resolve_reply_mention_index(
                chat_tid,
                redis=redis,
                mention_policy=mention_policy,
                speaker_names=speaker_names,
            )
        if mention_index is not None:
            resolved_text = resolve_mentions(output_text, mention_index)
    if strip_alien_html_tags is None:
        strip_alien_html_tags = await is_enabled(
            "ai_chatbot_strip_alien_html_tags",
            chat_tid=chat_tid,
            redis=redis,
        )
    max_columns = max(1, int(await get_value("ai_chatbot_table_max_columns", chat_tid=chat_tid, redis=redis)))
    max_rows = max(1, int(await get_value("ai_chatbot_table_max_rows", chat_tid=chat_tid, redis=redis)))
    card_threshold = int(await get_value("ai_chatbot_table_card_threshold", chat_tid=chat_tid, redis=redis))
    doc = render_ai_response(
        header,
        resolved_text,
        tool_labels=tool_labels,
        strip_alien_html_tags=strip_alien_html_tags,
        max_columns=max_columns,
        max_rows=max_rows,
        card_threshold=card_threshold,
    )
    if explicit_debug_mode and model is not None and result is not None:
        doc += " "
        doc += build_debug_doc(model, result)
    return doc
