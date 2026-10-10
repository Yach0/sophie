from __future__ import annotations

import re
import secrets
from collections.abc import Sequence
from html import escape
from html.parser import HTMLParser
from typing import Any, Final

from beanie import PydanticObjectId
from beanie.operators import In
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart
from pydantic_ai.models import Model
from redis.asyncio import Redis
from stfu_tg import BlockQuote, Doc, KeyValue, Section
from stfu_tg.ai_md import ai_markdown_to_doc
from stfu_tg.doc import Element

from sophie_bot.db.models import ChatModel
from sophie_bot.db.models.ai.ai_mode import AIMode
from sophie_bot.db.models.chat import ChatType
from sophie_bot.modules.ai.utils.ai_agent_run import AIAgentResult
from sophie_bot.modules.ai.utils.ai_header import (
    AI_CHATBOT_CUSTOM_EMOJI_ID,
    ai_credit_header,
    build_ai_message_doc,
)
from sophie_bot.modules.ai.utils.ai_quota import get_quota_info
from sophie_bot.modules.ai.utils.ai_tool import AI_TOOLS_BY_NAME, AITool
from sophie_bot.modules.ai.utils.ai_usage_service import usage_input_tokens, usage_output_tokens
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


_ALLOWED_HTML_ATTRIBUTES: Final[dict[str, frozenset[str]]] = {
    "a": frozenset({"href"}),
    "b": frozenset(),
    "blockquote": frozenset({"expandable"}),
    "br": frozenset(),
    "code": frozenset({"class"}),
    "del": frozenset(),
    "em": frozenset(),
    "i": frozenset(),
    "ins": frozenset(),
    "pre": frozenset(),
    "s": frozenset(),
    "span": frozenset({"class"}),
    "strike": frozenset(),
    "strong": frozenset(),
    "tg-emoji": frozenset({"emoji-id"}),
    "tg-spoiler": frozenset(),
    "u": frozenset(),
}
_SAFE_LINK_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https", "mailto", "tg"})
_LANGUAGE_CLASS_PATTERN: Final[re.Pattern[str]] = re.compile(r"language-[A-Za-z0-9_-]+")


def _render_allowed_html_tag(
    tag: str,
    attributes: list[tuple[str, str | None]],
    *,
    closing: bool,
    self_closing: bool,
) -> str | None:
    allowed_attributes = _ALLOWED_HTML_ATTRIBUTES.get(tag)
    if allowed_attributes is None or (self_closing and tag != "br"):
        return None
    if closing:
        return None if tag == "br" else f"</{tag}>"

    rendered_attributes: list[str] = []
    for name, value in attributes:
        name = name.casefold()
        if name not in allowed_attributes:
            continue
        if tag == "blockquote" and name == "expandable":
            rendered_attributes.append("expandable")
        elif tag == "a" and name == "href" and value is not None:
            scheme = value.partition(":")[0].casefold()
            if scheme in _SAFE_LINK_SCHEMES:
                rendered_attributes.append(f'href="{escape(value, quote=True)}"')
        elif tag == "code" and name == "class" and value is not None:
            if _LANGUAGE_CLASS_PATTERN.fullmatch(value):
                rendered_attributes.append(f'class="{value}"')
        elif tag == "span" and name == "class" and value == "tg-spoiler":
            rendered_attributes.append('class="tg-spoiler"')
        elif tag == "tg-emoji" and name == "emoji-id" and value is not None and value.isdigit():
            rendered_attributes.append(f'emoji-id="{value}"')

    attributes_html = f" {' '.join(rendered_attributes)}" if rendered_attributes else ""
    return f"<{tag}{attributes_html}>"


class _SingleHTMLTagParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.recognized = False
        self.invalid = False
        self.rendered: str | None = None

    def _record(self, rendered: str | None) -> None:
        if self.recognized:
            self.invalid = True
            return
        self.recognized = True
        self.rendered = rendered

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._record(_render_allowed_html_tag(tag, attrs, closing=False, self_closing=False))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._record(_render_allowed_html_tag(tag, attrs, closing=False, self_closing=True))

    def handle_endtag(self, tag: str) -> None:
        self._record(_render_allowed_html_tag(tag, [], closing=True, self_closing=False))

    def handle_data(self, data: str) -> None:
        if data:
            self.invalid = True


def _find_html_tag_end(text: str, start: int) -> int | None:
    quote: str | None = None
    for index in range(start + 1, len(text)):
        character = text[index]
        if quote is not None:
            if character == quote:
                quote = None
        elif character in {'"', "'"}:
            quote = character
        elif character == ">":
            return index
    return None


def _protect_html_tags_in_line(
    line: str,
    token_prefix: str,
    token_suffix: str,
    replacements: list[str],
) -> str:
    parts: list[str] = []
    cursor = 0
    while cursor < len(line):
        character = line[cursor]
        if character == "`":
            code_end = line.find("`", cursor + 1)
            if code_end != -1:
                parts.append(line[cursor : code_end + 1])
                cursor = code_end + 1
                continue
        if character != "<":
            parts.append(character)
            cursor += 1
            continue

        tag_end = _find_html_tag_end(line, cursor)
        if tag_end is None:
            parts.append(character)
            cursor += 1
            continue
        parser = _SingleHTMLTagParser()
        parser.feed(line[cursor : tag_end + 1])
        parser.close()
        if parser.invalid or not parser.recognized:
            parts.append(character)
            cursor += 1
            continue
        if parser.rendered is not None:
            parts.append(f"{token_prefix}{len(replacements)}{token_suffix}")
            replacements.append(parser.rendered)
        cursor = tag_end + 1
    return "".join(parts)


def _protect_supported_html(text: str) -> tuple[str, tuple[str, ...], str, str]:
    nonce = secrets.token_hex(8)
    while nonce in text:
        nonce = secrets.token_hex(8)
    token_prefix = f"\ue000{nonce}:"
    token_suffix = "\ue001"
    replacements: list[str] = []
    protected_lines: list[str] = []
    in_code_fence = False
    for line in text.splitlines(keepends=True):
        if line.startswith("```"):
            in_code_fence = not in_code_fence
            protected_lines.append(line)
        elif in_code_fence:
            protected_lines.append(line)
        else:
            protected_lines.append(_protect_html_tags_in_line(line, token_prefix, token_suffix, replacements))
    return "".join(protected_lines), tuple(replacements), token_prefix, token_suffix


class _ProtectedHTMLDoc(Element):
    def __init__(self, doc: Doc, replacements: tuple[str, ...], token_prefix: str, token_suffix: str) -> None:
        self.doc = doc
        self.replacements = replacements
        self.token_pattern = re.compile(rf"{re.escape(token_prefix)}(\d+){re.escape(token_suffix)}")

    def _restore(self, text: str) -> str:
        return self.token_pattern.sub(
            lambda match: self.replacements[int(match.group(1))],
            text,
        )

    def to_html(self, *_args: Any) -> str:
        return self._restore(self.doc.to_html())

    def to_rich(self) -> str:
        return self._restore(self.doc.to_rich())

    def to_md(self) -> str:
        return self._restore(self.doc.to_md())


def _render_ai_markdown(
    text: str,
    *,
    strip_alien_html_tags: bool,
    max_columns: int,
    max_rows: int,
    card_threshold: int,
) -> Element:
    if not strip_alien_html_tags or "<" not in text:
        return ai_markdown_to_doc(text, max_columns=max_columns, max_rows=max_rows, card_threshold=card_threshold)
    protected_text, replacements, token_prefix, token_suffix = _protect_supported_html(text)
    doc = ai_markdown_to_doc(protected_text, max_columns=max_columns, max_rows=max_rows, card_threshold=card_threshold)
    if not replacements:
        return doc
    return _ProtectedHTMLDoc(doc, replacements, token_prefix, token_suffix)


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
) -> Doc:
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
    doc = build_ai_message_doc(
        header,
        _render_ai_markdown(
            resolved_text,
            strip_alien_html_tags=strip_alien_html_tags,
            max_columns=max_columns,
            max_rows=max_rows,
            card_threshold=card_threshold,
        ),
        tool_labels=tool_labels,
        emoji_id=AI_CHATBOT_CUSTOM_EMOJI_ID,
    )
    if explicit_debug_mode and model is not None and result is not None:
        doc += " "
        doc += build_debug_doc(model, result)
    return doc
